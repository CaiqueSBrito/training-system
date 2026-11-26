"""T020: casos escritos antes do produto; PostgreSQL descartável, sem LLM."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from training_contracts.chat import ChatResponse
from training_contracts.ingestao import CitationSpanSelection
from training_contracts.linhagem import (
    ChatTurnRecord, ItemRecord, CitationRecord, RetrievalRunRecord, VerdictRecord,
    SearchPlanRecord,
)
from training_lineage import create_lineage_engine, migrate, LineageRepository
from training_lineage.chat import ChatLineageRepository
from training_lineage import tables as t
from test_ressalvas_persistencia import bundle, persist, events, formatted, KEY

NOW = datetime(2026, 11, 24, tzinfo=timezone.utc)


@pytest.fixture
def engine():
    admin = create_lineage_engine()
    name = 'test_t020_' + uuid4().hex
    with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
        c.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
    db = create_engine(admin.url.set(database=name), hide_parameters=True)
    try:
        migrate(db, '0003')
        with db.begin() as c:
            repo=LineageRepository(c)
            from training_contracts.ingestao import IngestionReservation
            reservation=IngestionReservation(reservation_id=uuid4().hex,owner_id='owner',source_sha256='a'*64,
                document_version_id='v1',phase='reserved',generation=1,
                lease_until=repo._now()+timedelta(minutes=5),attempt_id='attempt-1',error_code=None)
            c.execute(t.ingestion_reservation.insert().values(**reservation.model_dump()))
            from training_contracts.ingestao import ReconciliationJob
            records=bundle()
            job=ReconciliationJob(job_id='job',owner_id='owner',document_version_id='v1',
                embedding_key=KEY,desired_revision=1,cursor=None,attempt=1,state='pending',error_code=None,created_at=NOW)
            repo.transition_reservation(reservation.reservation_id,1,owner_id='owner',phase='parsing')
            from training_llm import document_input
            repo.commit_parsed(reservation.reservation_id,1,owner_id='owner',document=records[0],
                canonical=records[1],spans=records[2],chunks=[records[3]],provenance=[records[4]],
                embedding_inputs=[records[5]],job=job,format_input=document_input)
            decision=repo.append_assessment(events(records),0,evaluated_input=formatted(records))
            repo.transition_reservation(reservation.reservation_id,1,owner_id='owner',phase='indexing')
            repo.record_index_ack('owner','c-v1',KEY,reservation_id=reservation.reservation_id,generation=1,decision_revision=decision.revision)
            repo.checkpoint_job(job.model_copy(update={'state':'complete'}),expected_attempt=1,
                expected_cursor=None,reservation_id=reservation.reservation_id,generation=1)
            repo.publish_ready(reservation.reservation_id,1,owner_id='owner',embedding_key=KEY)
        migrate(db)
        yield db
    finally:
        db.dispose()
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
            c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def refusal(turn_id='turn', owner='owner'):
    turn = ChatTurnRecord(chat_turn_id=turn_id, session_id='session', user_id=owner,
        prompt='input', status='recusa', motivo_recusa='limite_de_taxa',
        trace_id='trace-' + turn_id, created_at=NOW)
    response = ChatResponse(status='recusa', motivo_recusa='limite_de_taxa',
        chat_turn_id=turn_id, trace_id=turn.trace_id)
    return turn, response


def success():
    turn = ChatTurnRecord(chat_turn_id='turn', session_id='session', user_id='owner',
        prompt='input', status='ok', intencao='gerar_flashcards', modo='consulta',
        input_verdict={'malicioso': False, 'motivo':'seguro'}, trace_id='trace', created_at=NOW)
    raw = dict(tipo='flashcard', front='Qual?', back='Eco', bloom_level='lembrar',
               citations=[dict(chunk_ref='C1', evidence='Eco')])
    item = ItemRecord(item_id='item', chat_turn_id='turn', raw_content=raw, content=raw, status='entregue')
    citation = CitationRecord(citation_id='cite', item_id='item', chunk_ref='C1', chunk_id='c-v1', evidence='Eco')
    run = RetrievalRunRecord(retrieval_run_id='run', chat_turn_id='turn', modo='consulta',
        candidates=[dict(chunk_id='c-v1', selected=True)])
    response = ChatResponse(status='ok', chat_turn_id='turn', trace_id='trace',
        itens=[dict(item_id='item', conteudo=raw)], fontes=[dict(chunk_ref='C1',chunk_id='c-v1',
        document_id='doc-v1', document_version_id='v1',page=3,section_path=['A','Detalhes'])])
    verdicts = [VerdictRecord(verdict_id='e9', item_id='item', origem='E9',aprovado=True,created_at=NOW),
        VerdictRecord(verdict_id='e10', item_id='item', origem='E10',aprovado=True,
        fundamentado=True, conforme_rubrica=True,judge_model='real-model-id',created_at=NOW)]
    selection = CitationSpanSelection(chunk_id='c-v1',canonical_id='can-v1',start=6,end=9,evidence='Eco')
    plan=SearchPlanRecord(search_plan_id='plan',chat_turn_id='turn',objetivos=['Eco'],bloom_level='lembrar',consultas=['Eco'])
    return turn, plan, run, [item], [citation], verdicts, response, {'cite':selection}


def test_save_read_exact_spans_owner_and_caller_transaction(engine):
    with engine.connect() as c:
        repo=ChatLineageRepository(c)
        repo.save_turn(*success())
        assert repo.get_item_lineage('other','item') is None
        found=repo.get_item_lineage('owner','item')
        assert found['lineage'].trace_id=='trace'
        assert [(s.start,s.end,s.page) for s in found['spans']['cite']]==[(6,9,3)]
        c.rollback()
    with engine.connect() as c:
        assert ChatLineageRepository(c).get_item_lineage('owner','item') is None
    with engine.begin() as c:
        ChatLineageRepository(c).save_turn(*success())
    with engine.connect() as c:
        assert c.execute(select(t.chat_turn.c.response)).scalar_one()==success()[6].model_dump(mode='json')


def test_e1_error_and_final_failure_atomic(engine):
    with engine.begin() as c:
        repo=ChatLineageRepository(c)
        turn,response=refusal()
        repo.save_turn(turn,None,None,[],[],[],response)
        row=c.execute(select(t.chat_turn)).mappings().one()
        assert row['record']['intencao'] is None and row['record']['input_verdict'] is None
        error=turn.model_copy(update={'chat_turn_id':'error','status':'erro','motivo_recusa':None})
        repo.save_turn(error,None,None,[],[],[],ChatResponse(status='erro',chat_turn_id='error',trace_id=error.trace_id))
    with pytest.raises(RuntimeError),engine.begin() as c:
        turn,response=refusal('rollback')
        ChatLineageRepository(c).save_turn(turn,None,None,[],[],[],response)
        raise RuntimeError('final failure')
    with engine.connect() as c:
        assert set(c.execute(select(t.chat_turn.c.chat_turn_id)).scalars())=={'turn','error'}


def test_cross_owner_and_database_foreign_keys(engine):
    args=list(success());args[0]=args[0].model_copy(update={'user_id':'other'})
    with pytest.raises(PermissionError),engine.begin() as c:
        ChatLineageRepository(c).save_turn(*args)
    with pytest.raises(DBAPIError),engine.begin() as c:
        c.execute(t.chat_item.insert().values(item_id='orphan',chat_turn_id='absent',record={}))
    with engine.begin() as c:
        turn,response=refusal();ChatLineageRepository(c).save_turn(turn,None,None,[],[],[],response)
    with pytest.raises(DBAPIError),engine.begin() as c:
        c.execute(t.chat_citation.insert().values(citation_id='bad',item_id='missing',
            chat_turn_id='turn',owner_id='other',chunk_id='c-v1',record={},selection={},spans=[]))


def test_rate_limit_concurrent_durable_and_database_clock(engine):
    barrier=Barrier(2)
    def slot(_):
        with engine.begin() as c:
            barrier.wait()
            return ChatLineageRepository(c).acquire_chat_rate_slot('owner',1,60)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(slot,range(2)))==[False,True]
    with engine.begin() as c:
        repo=ChatLineageRepository(c)
        assert not repo.acquire_chat_rate_slot('owner',1,60)
        assert repo.acquire_chat_rate_slot('other',1,60)
        c.execute(text("UPDATE chat_rate_event SET acquired_at=clock_timestamp()-interval '61 seconds'"))
        assert repo.acquire_chat_rate_slot('owner',1,60)


def test_continuation_scope_cursor_used_count_isolated_and_rollback(engine):
    with engine.begin() as c:
        repo=ChatLineageRepository(c)
        turn,response=refusal();repo.save_turn(turn,None,None,[],[],[],response)
        repo.save_continuation('owner','session',chat_turn_id='turn',scope={'document_version_ids':['v1']},
            cursor={'section':2,'offset':4},used_chunk_ids=['c-v1'],quantity=8)
    with engine.connect() as c:
        repo=ChatLineageRepository(c)
        state=repo.get_continuation('owner','session')
        assert state['cursor']=={'section':2,'offset':4} and state['quantity']==8
        assert state['used_chunk_ids']==['c-v1'] and state['scope']=={'document_version_ids':['v1'],'section_prefix':[]}
        assert repo.get_continuation('other','session') is None
        assert repo.get_continuation('owner','other') is None
        repo.save_continuation('owner','session',chat_turn_id='turn',scope=state['scope'],
            cursor={'section':3},used_chunk_ids=['c-v1'],quantity=16)
        c.rollback()
        assert repo.get_continuation('owner','session')['quantity']==8


def test_downgrade_preserves_history(engine):
    with engine.begin() as c:
        turn,response=refusal();ChatLineageRepository(c).save_turn(turn,None,None,[],[],[],response)
    with pytest.raises(DBAPIError,match='história'):
        migrate(engine,'0003')
    with engine.connect() as c:
        assert c.execute(select(t.chat_turn.c.chat_turn_id)).scalar_one()=='turn'


def test_last_insert_failure_rolls_back_only_savepoint(engine):
    with engine.begin() as c:
        repo=ChatLineageRepository(c)
        repo.save_turn(*success())
        args=list(success())
        args[0]=args[0].model_copy(update={'chat_turn_id':'turn2'})
        args[1]=args[1].model_copy(update={'chat_turn_id':'turn2','search_plan_id':'plan2'})
        args[2]=args[2].model_copy(update={'chat_turn_id':'turn2','retrieval_run_id':'run2'})
        args[3]=[args[3][0].model_copy(update={'item_id':'item2','chat_turn_id':'turn2'})]
        args[4]=[args[4][0].model_copy(update={'citation_id':'cite2','item_id':'item2'})]
        args[5]=[v.model_copy(update={'item_id':'item2','verdict_id':'e9-2' if v.origem.value=='E9' else 'e10'}) for v in args[5]]
        args[6]=args[6].model_copy(update={'chat_turn_id':'turn2','itens':[args[6].itens[0].model_copy(update={'item_id':'item2'})]})
        args[7]={'cite2':args[7]['cite']}
        with pytest.raises(DBAPIError):
            repo.save_turn(*args)
        assert c.execute(select(t.chat_turn.c.chat_turn_id)).scalars().all()==['turn']
        assert c.execute(select(t.chat_item.c.item_id)).scalars().all()==['item']
        assert c.execute(select(t.chat_verdict.c.verdict_id).order_by(t.chat_verdict.c.verdict_id)).scalars().all()==['e10','e9']


def test_database_owner_check_and_catalog(engine):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    with engine.connect() as c:
        assert compare_metadata(MigrationContext.configure(c),t.metadata)==[]
    with engine.begin() as c:
        repo=ChatLineageRepository(c)
        turn,response=refusal('foreign','other');repo.save_turn(turn,None,None,[],[],[],response)
        run=RetrievalRunRecord(retrieval_run_id='foreign-run',chat_turn_id='foreign',modo='consulta',candidates=[])
        c.execute(t.chat_retrieval.insert().values(retrieval_run_id='foreign-run',chat_turn_id='foreign',record=run.model_dump(mode='json')))
    with pytest.raises(DBAPIError) as caught,engine.begin() as c:
        c.execute(t.chat_candidate.insert().values(chat_turn_id='foreign',owner_id='other',chunk_id='c-v1',selected=True))
    assert caught.value.orig.sqlstate=='23503'


def test_reject_unbound_response_missing_provenance_and_reset(engine):
    args=list(success())
    with engine.begin() as c:
        repo=ChatLineageRepository(c)
        with pytest.raises(ValueError,match='Proveniência'):
            repo.save_turn(*args[:7])
        args[6]=args[6].model_copy(update={'trace_id':'wrong'})
        with pytest.raises(ValueError,match='trace'):
            repo.save_turn(*args)
        assert not c.execute(select(t.chat_turn.c.chat_turn_id)).all()
        turn,response=refusal();repo.save_turn(turn,None,None,[],[],[],response)
        state=dict(chat_turn_id='turn',scope={'document_version_ids':['v1']},cursor={'offset':3},used_chunk_ids=['c-v1'],quantity=8)
        repo.save_continuation('owner','session',**state)
        with pytest.raises(ValueError,match='progresso'):
            repo.save_continuation('owner','session',**(state|{'quantity':0,'used_chunk_ids':[]}))


def test_revoked_decision_blocks_delivery(engine):
    with engine.begin() as c:
        repo=LineageRepository(c)
        repo.append_assessment(events(bundle(),attempt='revoke',start=4,outcome='injection',previous='attempt-1-d'),3,
            evaluated_input=formatted(bundle()))
        with pytest.raises(PermissionError,match='revogado'):
            ChatLineageRepository(c).save_turn(*success())
        assert not c.execute(select(t.chat_turn)).all()


@pytest.mark.parametrize('operation',['reconciliation','reserve','assessment'])
def test_chat_holds_publication_and_decision_until_caller_commit(engine,operation):
    """Duas conexões reais: a segunda não pode invalidar o contexto antes do commit."""
    with engine.connect() as chat,engine.connect() as writer:
        ChatLineageRepository(chat).save_turn(*success())
        writer.execute(text("SET LOCAL lock_timeout='200ms'"))
        repo=LineageRepository(writer)
        with pytest.raises(DBAPIError) as caught:
            if operation=='reconciliation':
                repo.begin_reconciliation('owner','v1',KEY,'next')
            elif operation=='reserve':
                repo.reserve('owner','a'*64,'next')
            else:
                repo.append_assessment(events(bundle(),attempt='revoke',start=4,outcome='injection',previous='attempt-1-d'),3,
                    evaluated_input=formatted(bundle()))
        assert caught.value.orig.sqlstate=='55P03'
        writer.rollback()
        # Ainda publicado; nenhum turno é visível à conexão que não confirmou.
        assert writer.execute(select(t.chat_turn.c.chat_turn_id)).first() is None
        writer.rollback()
        chat.commit()
        if operation=='assessment':
            repo.append_assessment(events(bundle(),attempt='revoke',start=4,outcome='injection',previous='attempt-1-d'),3,
                evaluated_input=formatted(bundle()))
        elif operation=='reconciliation':
            assert repo.begin_reconciliation('owner','v1',KEY,'next')[0]=='acquired'
        else:
            assert repo.reserve('owner','a'*64,'next')[0]=='ready'
        writer.commit()
        assert writer.execute(select(t.chat_turn.c.response)).scalar_one()==success()[6].model_dump(mode='json')
        assert ChatLineageRepository(writer).get_item_lineage('owner','item')['lineage'].item_id=='item'


def test_chat_waits_before_chunk_lock_when_reconciliation_owns_reservation(engine):
    """Reserva -> chunks: chat esperando reserva não pode travar reclassificação do writer."""
    from threading import Event
    from time import monotonic
    entered=Event()
    reader_pid=[]
    def save():
        with engine.connect() as reader:
            reader.execute(text("SET LOCAL statement_timeout='5s'"))
            reader_pid.append(reader.execute(text('SELECT pg_backend_pid()')).scalar_one())
            entered.set()
            with pytest.raises(PermissionError,match='revogado'):
                ChatLineageRepository(reader).save_turn(*success())
            reader.rollback()
    with engine.connect() as writer,ThreadPoolExecutor(max_workers=1) as pool:
        repo=LineageRepository(writer)
        status,reservation,_=repo.begin_reconciliation('owner','v1',KEY,'next')
        assert status=='acquired'
        writer_pid=writer.execute(text('SELECT pg_backend_pid()')).scalar_one()
        future=pool.submit(save)
        try:
            assert entered.wait(2)
            deadline=monotonic()+2
            while not writer.execute(text('SELECT :writer = ANY(pg_blocking_pids(:reader))'),
                    {'writer':writer_pid,'reader':reader_pid[0]}).scalar_one():
                assert monotonic()<deadline,'chat não aguardou a reserva'
                Event().wait(0.01)
            writer.execute(text("SET LOCAL lock_timeout='500ms'"))
            repo.append_assessment(events(bundle(),attempt='revoke',start=4,outcome='injection',previous='attempt-1-d'),3,
                evaluated_input=formatted(bundle()),reservation_id=reservation.reservation_id,generation=reservation.generation)
            writer.commit()
            future.result(timeout=6)
            assert writer.execute(select(t.chat_turn.c.chat_turn_id)).first() is None
        finally:
            writer.rollback()


@pytest.mark.parametrize('equivalent',[
    {'document_version_ids':['v2','v1']},
    {'document_version_ids':('v1','v2'),'section_prefix':()},
    {'document_version_ids':['v2','v1','v2','v1'],'section_prefix':[]},
])
def test_equivalent_scopes_cannot_reset_continuation(engine,equivalent):
    with engine.begin() as c:
        LineageRepository(c).insert_document_version(bundle('v2')[0].model_copy(update={'sha256':'b'*64}))
        repo=ChatLineageRepository(c)
        turn,response=refusal();repo.save_turn(turn,None,None,[],[],[],response)
        repo.save_continuation('owner','session',chat_turn_id='turn',scope={'document_version_ids':['v1','v2']},
            cursor={'offset':4},used_chunk_ids=['c-v1'],quantity=8)
    with engine.begin() as c:
        repo=ChatLineageRepository(c)
        with pytest.raises(ValueError,match='progresso'):
            repo.save_continuation('owner','session',chat_turn_id='turn',scope=equivalent,
                cursor=None,used_chunk_ids=[],quantity=0)
        state=repo.get_continuation('owner','session')
        assert state['scope']=={'document_version_ids':['v1','v2'],'section_prefix':[]}
        assert (state['cursor'],state['quantity'],state['used_chunk_ids'])==({'offset':4},8,['c-v1'])


def test_scope_keys_owner_and_legacy_encoding_are_fail_closed(engine):
    with engine.begin() as c:
        LineageRepository(c).insert_document_version(bundle('foreign','other')[0])
        repo=ChatLineageRepository(c)
        turn,response=refusal();repo.save_turn(turn,None,None,[],[],[],response)
        # Formato já persistido pela implementação anterior, sem section_prefix.
        c.execute(t.chat_continuation.insert().values(owner_id='owner',session_id='session',chat_turn_id='turn',
            state=dict(scope={'document_version_ids':['v1']},cursor={'offset':4},used_chunk_ids=['c-v1'],quantity=8)))
        with pytest.raises(ValueError,match='progresso'):
            repo.save_continuation('owner','session',chat_turn_id='turn',scope={'document_version_ids':('v1','v1'),'section_prefix':()},
                cursor=None,used_chunk_ids=[],quantity=0)
        with pytest.raises(ValueError,match='desconhecida'):
            repo.save_continuation('owner','session',chat_turn_id='turn',scope={'document_version_ids':['v1'],'extra':True},
                cursor=None,used_chunk_ids=[],quantity=0)
        with pytest.raises(PermissionError,match='owner'):
            repo.save_continuation('owner','session',chat_turn_id='turn',scope={'document_version_ids':['foreign']},
                cursor=None,used_chunk_ids=[],quantity=0)
        assert repo.get_continuation('owner','session')['quantity']==8


def test_retrieval_cursor_binding_canonicalized_without_changing_owner(engine):
    from hashlib import sha256
    import json
    def binding(owner,versions):
        return sha256(json.dumps([owner,versions,[],'consulta'],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    with engine.begin() as c:
        LineageRepository(c).insert_document_version(bundle('v2')[0].model_copy(update={'sha256':'b'*64}))
        repo=ChatLineageRepository(c)
        repo.save_turn(*success())
        scope={'document_version_ids':('v2','v1'),'section_prefix':()}
        cursor={'version':1,'binding':binding('owner',['v2','v1']),'used_chunk_ids':['c-v1'],'remaining':0}
        repo.save_continuation('owner','session',chat_turn_id='turn',scope=scope,cursor=cursor,used_chunk_ids=['c-v1'],quantity=8)
        state=repo.get_continuation('owner','session')
        assert state['scope']=={'document_version_ids':['v1','v2'],'section_prefix':[]}
        assert state['cursor']==cursor|{'binding':binding('owner',['v1','v2'])}
        with pytest.raises(ValueError,match='binding'):
            repo.save_continuation('owner','session',chat_turn_id='turn',scope=scope,
                cursor=cursor|{'binding':binding('other',['v2','v1'])},used_chunk_ids=['c-v1'],quantity=8)
        assert repo.get_continuation('owner','session')==state


def test_canonical_quote_crosses_true_pages_without_inventing_single_page(engine):
    args=list(success());value=bundle()[1].text
    content=args[3][0].content.model_dump(mode='json')|{'citations':[dict(chunk_ref='C1',evidence=value)]}
    args[3]=[args[3][0].model_copy(update={'raw_content':content,'content':__import__('training_contracts',fromlist=['validar_item']).validar_item(content)})]
    args[4]=[args[4][0].model_copy(update={'evidence':value})]
    args[6]=args[6].model_copy(update={'itens':[args[6].itens[0].model_copy(update={'conteudo':args[3][0].content})],
        'fontes':[args[6].fontes[0].model_copy(update={'page':None,'section_path':[]})]})
    args[7]={'cite':CitationSpanSelection(chunk_id='c-v1',canonical_id='can-v1',start=0,end=len(value),evidence=value)}
    with engine.begin() as c:
        repo=ChatLineageRepository(c);repo.save_turn(*args)
        actual=repo.get_item_lineage('owner','item')
        assert actual['lineage'].citations[0].page is None
        assert [(s.start,s.end,s.page) for s in actual['spans']['cite']]==[(0,4,2),(4,6,None),(6,len(value),3)]

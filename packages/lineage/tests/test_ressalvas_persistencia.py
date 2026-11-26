"""C01–C04/C12/C14: casos escritos antes do produto T-011; Postgres real."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text, select
from sqlalchemy.exc import DBAPIError
from training_contracts.ingestao import (
    CanonicalTextRecord, CanonicalSpan, ChunkProvenanceRecord, CitationSpanSelection,
    EmbeddingInputRecord, IngestionAuditEvent, JudgeMetadata, ReconciliationJob,
)
from training_contracts.linhagem import DocumentVersionRecord, ChunkRecord
from training_lineage import LineageRepository, create_lineage_engine, migrate
import training_lineage.migration as migration_module
from training_llm import document_input

NOW = datetime(2026, 11, 24, tzinfo=timezone.utc)
KEY = 'gemini-embedding-2@3072@fmt-v1'
HISTORY = ('canonical_text', 'canonical_span', 'chunk_provenance', 'embedding_input', 'ingestion_audit_event')


@pytest.fixture
def migration_head():
    config = Config()
    config.set_main_option('script_location', str(Path(migration_module.__file__).with_name('migrations')))
    return ScriptDirectory.from_config(config).get_current_head()


def digest(value):
    return sha256(value.encode('utf-8')).hexdigest()


@pytest.fixture
def empty_database():
    admin = create_lineage_engine()
    name = 'test_t011_' + uuid4().hex
    with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
        c.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
    engine = create_engine(admin.url.set(database=name), hide_parameters=True)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
            c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def engine(empty_database):
    migrate(empty_database)
    return empty_database


def bundle(version_id='v1', owner='owner', *, page=3, provenance='exact'):
    value = 'Eco😀\n\nEco e\u0301'
    version = DocumentVersionRecord(document_version_id=version_id, document_id='doc-' + version_id,
        owner_id=owner, sha256='a'*64, parser_version='parser-v1', created_at=NOW)
    canonical = CanonicalTextRecord(canonical_id='can-' + version_id, document_version_id=version_id,
        canonical_version='recipe-v1', parser_version='parser-v1', text=value,
        text_sha256=digest(value), created_at=NOW)
    spans = tuple(CanonicalSpan(span_id=f's{i}-{version_id}', canonical_id=canonical.canonical_id,
        start=start, end=end, page=p, section_path=section, h1_id='h1', kind=kind,
        provenance=prov) for i, (start,end,p,section,kind,prov) in enumerate([
            (0,4,None if page is None and provenance=='exact' else 2,('A',),'paragraph','exact'), (4,6,None,('A',),'separator','exact'),
            (6,len(value),page,('A','Detalhes'),'paragraph',provenance)]))
    chunk = ChunkRecord(chunk_id='c-' + version_id, document_version_id=version_id,
        chunker_version='structure-v2', page=spans[0].page, section_path=['A'], start_offset=0,
        end_offset=len(value), text=value)
    mapping = ChunkProvenanceRecord(chunk_id=chunk.chunk_id, canonical_id=canonical.canonical_id,
        start=0, end=len(value), span_ids=tuple(s.span_id for s in spans), overlap_ranges=(),
        mapping_status='exact', created_at=NOW)
    formatted = document_input('Documento', ['A','Detalhes'], value)
    embedding = EmbeddingInputRecord(input_id='input-' + version_id, chunk_id=chunk.chunk_id,
        title='Documento', section_context=('A','Detalhes'), text_sha256=digest(value),
        format_version='fmt-v1', embedding_key=KEY, input_sha256=digest(formatted), created_at=NOW)
    return version, canonical, spans, chunk, mapping, embedding


def persist(repo, records):
    version, canonical, spans, chunk, mapping, embedding = records
    repo.insert_document_version(version)
    repo.persist_canonical(version.owner_id, canonical, spans, [chunk], [mapping], [embedding],
                           format_input=document_input)


def events(records, *, attempt='attempt-1', start=1, outcome='safe', precise=False, previous=None, policy='policy-v1'):
    version, canonical, spans, chunk, mapping, embedding = records
    common = dict(attempt_id=attempt, owner_id=version.owner_id,
        document_version_id=version.document_version_id, chunk_id=chunk.chunk_id,
        policy_version=policy, input_sha256=embedding.input_sha256,
        heuristic_version='heuristic-v1', heuristic_rule_ids=('rule-1',), created_at=NOW)
    triage = IngestionAuditEvent(**common, event_id=attempt+'-t', sequence=start,
        stage='triage', judge_metadata=None, outcome='suspect', reason_code='suspected',
        supersedes_decision_id=None)
    metadata = JudgeMetadata(provider='google', model='approved-judge', config_version='cfg-v1',
        config_sha256='b'*64, prompt_version='prompt-v1', prompt_sha256='c'*64,
        request_id='request-1', result=outcome, error_kind='timeout' if outcome=='error' else None)
    reason = {'safe':'judge_release','injection':'judge_block','error':'judge_unavailable'}[outcome]
    judge = IngestionAuditEvent(**common, event_id=attempt+'-j', sequence=start+1, stage='judge',
        judge_metadata=metadata, outcome=outcome, reason_code=reason, triage_event_id=triage.event_id,
        supersedes_decision_id=None)
    decision = IngestionAuditEvent(**common, event_id=attempt+'-d', sequence=start+2, stage='decision',
        judge_metadata=None, outcome=('blocked' if precise or outcome=='injection' else
                                     'unavailable' if outcome=='error' else 'allowed'),
        reason_code='rule_block' if precise else reason, triage_event_id=triage.event_id,
        judge_event_id=judge.event_id, supersedes_decision_id=previous)
    return triage, judge, decision


def formatted(records):
    return document_input(records[5].title, list(records[5].section_context), records[3].text)


@pytest.mark.parametrize('initial', ['base','0001','0002'])
def test_C01_upgrade_preserves_old_records(empty_database, initial, migration_head):
    if initial != 'base':
        migrate(empty_database, initial)
        with empty_database.begin() as c:
            repo = LineageRepository(c)
            repo.insert_document_version(bundle()[0]); repo.insert_chunk(bundle()[3])
    migrate(empty_database)
    migrate(empty_database)
    with empty_database.connect() as c:
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == migration_head
        if initial != 'base':
            assert LineageRepository(c).get_document_version('v1') == bundle()[0]
            assert LineageRepository(c).get_chunk('c-v1') == bundle()[3]
            assert LineageRepository(c).authorize_candidates('owner', ['c-v1'], {'c-v1':1}) == {}
    assert set(HISTORY).issubset(inspect(empty_database).get_table_names())


def test_C02_empty_downgrade_preserves_0002_and_protections(engine):
    migrate(engine, '0002')
    assert not set(HISTORY).intersection(inspect(engine).get_table_names())
    with engine.begin() as c:
        repo=LineageRepository(c); repo.insert_document_version(bundle()[0]); repo.insert_chunk(bundle()[3])
    for table in ('chunk','document_version'):
        with pytest.raises(DBAPIError), engine.begin() as c:
            c.execute(text(f'DELETE FROM {table}'))


def test_C02_history_refuses_downgrade_before_any_drop(engine):
    with engine.begin() as c:
        persist(LineageRepository(c), bundle())
    # Contrato: a recusa ocorre antes de qualquer DROP e preserva a revisão atual (qualquer que
    # seja a cabeça do Alembic), as tabelas e os dados; não um literal de revisão incidental.
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    import training_lineage.migration as migration_module
    config=Config();config.set_main_option('script_location',str(Path(migration_module.__file__).with_name('migrations')))
    def observed():
        with engine.connect() as c:
            return (c.execute(text('SELECT version_num FROM alembic_version')).scalar_one(),
                    sorted(inspect(c).get_table_names()),
                    {table: c.execute(text(f'SELECT count(*) FROM {table}')).scalar_one() for table in HISTORY})
    before=observed()
    assert before[0]==ScriptDirectory.from_config(config).get_current_head()
    assert any(before[2].values()) and set(HISTORY).issubset(before[1])
    with pytest.raises(DBAPIError, match='história'):
        migrate(engine,'0002')
    assert observed()==before
    with engine.connect() as c:
        assert LineageRepository(c).get_canonical('owner','can-v1') == bundle()[1]


@pytest.mark.parametrize('page,provenance', [(3,'exact'),(None,'exact'),(None,'unknown')])
def test_C03_C04_citation_intersections_from_persisted_source(engine,page,provenance):
    records=bundle(page=page,provenance=provenance)
    with engine.begin() as c:
        persist(LineageRepository(c),records)
    with engine.connect() as c:
        repo=LineageRepository(c)
        selected=repo.resolve_citation('owner',CitationSpanSelection(chunk_id='c-v1',canonical_id='can-v1',
            start=6,end=9,evidence='Eco'))
        assert [(s.start,s.end,s.page,s.provenance) for s in selected] == [(6,9,page,provenance)]
        first=repo.resolve_citation('owner',CitationSpanSelection(chunk_id='c-v1',canonical_id='can-v1',
            start=0,end=3,evidence='Eco'))
        assert first[0].page==records[2][0].page
        crossed=repo.resolve_citation('owner',CitationSpanSelection(chunk_id='c-v1',canonical_id='can-v1',
            start=3,end=7,evidence=records[1].text[3:7]))
        assert [s.page for s in crossed]==[records[2][0].page,None,page]
        with pytest.raises(PermissionError):
            repo.resolve_citation('other',CitationSpanSelection(chunk_id='c-v1',canonical_id='can-v1',
                start=6,end=9,evidence='Eco'))
        loaded=repo.load_embedding_inputs('owner',['c-v1'],embedding_key=KEY)
        assert loaded['c-v1']==records[5]
        assert repo.reproduce_embedding_input('owner','c-v1',KEY,format_input=document_input)==formatted(records)
        assert repo.load_embedding_inputs('other',['c-v1'],embedding_key=KEY)=={}


@pytest.mark.parametrize('fault', ['hash','text','range','span','parser','input','h1'])
def test_C04_rejects_relational_mismatch_without_partial_artifacts(engine,fault):
    records=list(bundle()); version,canonical,spans,chunk,mapping,embedding=records
    if fault=='hash': canonical=canonical.model_copy(update={'text_sha256':'0'*64})
    if fault=='text': chunk=chunk.model_copy(update={'text':chunk.text.replace('Eco','Ego')})
    if fault=='range': mapping=mapping.model_copy(update={'end':len(canonical.text)+1})
    if fault=='span': mapping=mapping.model_copy(update={'span_ids':('not-a-span',)})
    if fault=='parser': canonical=canonical.model_copy(update={'parser_version':'another-parser'})
    if fault=='input': embedding=embedding.model_copy(update={'input_sha256':'0'*64})
    if fault=='h1': spans=spans[:-1]+(spans[-1].model_copy(update={'h1_id':'different-h1'}),)
    with engine.begin() as c:
        repo=LineageRepository(c); repo.insert_document_version(version)
        with pytest.raises(ValueError):
            repo.persist_canonical('owner',canonical,spans,[chunk],[mapping],[embedding],format_input=document_input)
        assert repo.get_canonical('owner','can-v1') is None


@pytest.mark.parametrize('outcome,precise,allowed', [('safe',False,True),('injection',False,False),
                                                   ('safe',True,False),('error',False,False)])
def test_C12_audit_preserves_judge_and_real_block_reason(engine,outcome,precise,allowed):
    records=bundle()
    with engine.begin() as c:
        repo=LineageRepository(c); persist(repo,records)
        decision=repo.append_assessment(events(records,outcome=outcome,precise=precise),0,
                                        evaluated_input=formatted(records))
        assert decision.allowed is allowed and decision.revision==3
        history=repo.list_assessments('owner','c-v1')
        assert [e.stage for e in history]==['triage','judge','decision']
        assert history[1].judge_metadata.result==outcome
        assert history[2].judge_metadata is None
        assert repo.authorize_candidates('owner',['c-v1'],{'c-v1':3})=={}  # ainda não ready


def test_C12_partial_error_revokes_old_release_and_stale_attempt(engine):
    records=bundle()
    with engine.begin() as c:
        repo=LineageRepository(c); persist(repo,records)
        repo.append_assessment(events(records),0,evaluated_input=formatted(records))
        new=events(records,attempt='second',start=4,outcome='error',previous='attempt-1-d')
        repo.append_assessment(new[:1],3,evaluated_input=formatted(records))
        assert repo.get_effective_decisions('owner',['c-v1'])=={}
        with pytest.raises(ValueError,match='obsoleta'):
            repo.append_assessment(new[1:],3,evaluated_input=formatted(records))
        repo.append_assessment(new[1:2],4,evaluated_input=formatted(records))
        assert repo.get_effective_decisions('owner',['c-v1'])=={}
        repo.append_assessment(new[2:],5,evaluated_input=formatted(records))
        assert not repo.get_effective_decisions('owner',['c-v1'])['c-v1'].allowed


def test_C12_clean_chain_has_no_invented_judge_and_cross_links_roll_back(engine):
    records=bundle()
    common=dict(attempt_id='clean',owner_id='owner',document_version_id='v1',chunk_id='c-v1',
        policy_version='policy-v1',input_sha256=records[5].input_sha256,heuristic_version='h-v1',
        heuristic_rule_ids=(),judge_metadata=None,created_at=NOW,supersedes_decision_id=None)
    triage=IngestionAuditEvent(**common,event_id='clean-t',sequence=1,stage='triage',outcome='clean',reason_code='no_suspicion')
    decision=IngestionAuditEvent(**common,event_id='clean-d',sequence=2,stage='decision',outcome='allowed',
        reason_code='no_suspicion',triage_event_id='clean-t')
    with engine.begin() as c:
        repo=LineageRepository(c);persist(repo,records)
        repo.append_assessment([triage,decision],0,evaluated_input=formatted(records))
        assert [e.stage for e in repo.list_assessments('owner','c-v1')]==['triage','decision']
        other=bundle('v2','other');persist(repo,other)
        repo.append_assessment(events(other,attempt='other-attempt'),0,evaluated_input=formatted(other))
        invalid=events(records,attempt='wrong-link',start=3,previous='other-attempt-d')
        with pytest.raises(DBAPIError):
            repo.append_assessment(invalid,2,evaluated_input=formatted(records))
        assert len(repo.list_assessments('owner','c-v1'))==2


def test_C02_database_catalog_and_specific_checks(engine):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from training_lineage import tables
    with engine.connect() as c:
        assert compare_metadata(MigrationContext.configure(c),tables.metadata)==[]
        for table in HISTORY:
            assert c.execute(text('SELECT count(*) FROM pg_trigger WHERE tgrelid=CAST(:t AS regclass) AND NOT tgisinternal'),{'t':table}).scalar_one()>=2
    with engine.begin() as c:
        repo=LineageRepository(c);repo.insert_document_version(bundle()[0])
    for changes,constraint in [({'page':0},'canonical_span_page'),({'start':0,'end':0},'canonical_span_range')]:
        with pytest.raises(DBAPIError) as caught, engine.begin() as c:
            can=bundle()[1]
            c.execute(tables.canonical_text.insert().values(**can.model_dump()))
            values=bundle()[2][0].model_dump() | changes
            c.execute(tables.canonical_span.insert().values(**values))
        assert caught.value.orig.sqlstate=='23514'
        assert caught.value.orig.diag.constraint_name==constraint
    with pytest.raises(DBAPIError) as caught, engine.begin() as c:
        values=bundle()[2][0].model_dump() | {'canonical_id':'missing'}
        c.execute(tables.canonical_span.insert().values(**values))
    assert caught.value.orig.sqlstate=='23503'
    with engine.begin() as c:
        # Parent already exists; no second document insert.
        r=bundle();LineageRepository(c).persist_canonical('owner',r[1],r[2],[r[3]],[r[4]],[r[5]],format_input=document_input)
    bad=events(bundle())[0].model_dump() | {'chunk_id':'missing'}
    with pytest.raises(DBAPIError) as caught, engine.begin() as c:
        c.execute(tables.ingestion_audit_event.insert().values(**bad))
    assert caught.value.orig.sqlstate=='23503'
    bad=events(bundle())[0].model_dump() | {'reason_code':'judge_release'}
    with pytest.raises(DBAPIError) as caught, engine.begin() as c:
        c.execute(tables.ingestion_audit_event.insert().values(**bad))
    assert caught.value.orig.sqlstate=='23514'


def test_C12_concurrent_cas_only_one_writer(engine):
    records=bundle()
    with engine.begin() as c:
        repo=LineageRepository(c); persist(repo,records)
        repo.append_assessment(events(records),0,evaluated_input=formatted(records))
    barrier=Barrier(2)
    def writer(attempt):
        barrier.wait()
        try:
            with engine.begin() as c:
                LineageRepository(c).append_assessment(events(records,attempt=attempt,start=4,
                    outcome='injection',previous='attempt-1-d'),3,evaluated_input=formatted(records))
            return 'ok'
        except ValueError:
            return 'stale'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(writer,['writer-a','writer-b']))==['ok','stale']
    with engine.connect() as c:
        assert len(LineageRepository(c).list_assessments('owner','c-v1'))==6


@pytest.mark.parametrize('table', HISTORY)
@pytest.mark.parametrize('operation',['UPDATE','DELETE','TRUNCATE'])
def test_C02_all_new_history_is_append_only(engine,table,operation):
    with engine.begin() as c:
        repo=LineageRepository(c); persist(repo,bundle())
        repo.append_assessment(events(bundle()),0,evaluated_input=formatted(bundle()))
    key={'canonical_text':'canonical_id','canonical_span':'span_id','chunk_provenance':'chunk_id',
         'embedding_input':'input_id','ingestion_audit_event':'event_id'}[table]
    statement=(f'UPDATE {table} SET {key}={key}' if operation=='UPDATE' else
               f'DELETE FROM {table}' if operation=='DELETE' else f'TRUNCATE {table} CASCADE')
    with pytest.raises(DBAPIError) as caught, engine.begin() as c:
        c.execute(text(statement))
    assert caught.value.orig.sqlstate=='23514'


def test_C14_legacy_insufficient_is_explicit_without_fabricated_source(engine):
    with engine.begin() as c:
        repo=LineageRepository(c); repo.insert_document_version(bundle()[0]); repo.insert_chunk(bundle()[3])
        mapping=repo.mark_legacy_insufficient('owner','c-v1',created_at=NOW)
        assert mapping.mapping_status=='insufficient' and mapping.canonical_id is None
        assert mapping.span_ids==() and mapping.start is None
        assert repo.authorize_candidates('owner',['c-v1'],{'c-v1':1})=={}
        with pytest.raises(ValueError,match='insuficiente'):
            repo.resolve_citation('owner',CitationSpanSelection(chunk_id='c-v1',canonical_id='can-v1',start=0,end=3,evidence='Eco'))
        assert repo.get_chunk('c-v1')==bundle()[3]


def test_C14_new_policy_can_release_historical_flag_without_mutation(engine):
    records=list(bundle())
    records[3]=records[3].model_copy(update={'injection_flagged':True,'injection_reason':'histórico'})
    with engine.begin() as c:
        repo=LineageRepository(c);persist(repo,records)
        old=repo.get_chunk('c-v1'),repo.get_document_version('v1')
        repo.append_assessment(events(records,outcome='injection'),0,evaluated_input=formatted(records))
        repo.append_assessment(events(records,attempt='release',start=4,policy='policy-v2',previous='attempt-1-d'),3,
                               evaluated_input=formatted(records))
        decision=repo.get_effective_decisions('owner',['c-v1'])['c-v1']
        assert decision.allowed and decision.policy_version=='policy-v2'
        assert old==(repo.get_chunk('c-v1'),repo.get_document_version('v1'))
        repo.append_assessment(events(records,attempt='failure',start=7,policy='policy-v3',outcome='error',previous='release-d'),6,
                               evaluated_input=formatted(records))
        assert not repo.get_effective_decisions('owner',['c-v1'])['c-v1'].allowed
        assert repo.authorize_candidates('owner',['c-v1'],{'c-v1':9})=={}
        assert old==(repo.get_chunk('c-v1'),repo.get_document_version('v1'))


def test_C07_C12_C14_reservation_fencing_ready_and_current_decision(engine):
    with engine.begin() as c:
        status, first=LineageRepository(c).reserve('owner','a'*64,'attempt-1')
        assert status=='acquired'
    with engine.begin() as c:
        repo=LineageRepository(c)
        assert repo.reserve('owner','a'*64,'another-attempt')[0]=='busy'
        assert repo.reserve('other','a'*64,'attempt-1')[1].document_version_id!=first.document_version_id
        c.execute(text("UPDATE ingestion_reservation SET lease_until=clock_timestamp()-interval '1 second' WHERE reservation_id=:id"),{'id':first.reservation_id})
    with engine.begin() as c:
        repo=LineageRepository(c); status,current=repo.reserve('owner','a'*64,'attempt-2')
        assert status=='acquired' and current.generation==2
        assert current.document_version_id==first.document_version_id
        with pytest.raises(ValueError,match='obsoleta'):
            repo.renew(first.reservation_id,first.generation,owner_id='owner')
        with pytest.raises(ValueError,match='obsoleta'):
            repo.publish_ready(first.reservation_id,first.generation,owner_id='owner',embedding_key=KEY)
        records=bundle(current.document_version_id)
        job=ReconciliationJob(job_id='queue-job-1',owner_id='owner',document_version_id=current.document_version_id,
            embedding_key=KEY,desired_revision=1,cursor=None,attempt=1,state='pending',error_code=None,created_at=NOW)
        repo.transition_reservation(current.reservation_id,2,owner_id='owner',phase='parsing')
        repo.commit_parsed(current.reservation_id,2,owner_id='owner',document=records[0],
            canonical=records[1],spans=records[2],chunks=[records[3]],provenance=[records[4]],
            embedding_inputs=[records[5]],job=job,format_input=document_input)
        seq=repo.append_assessment(events(records),0,evaluated_input=formatted(records))
        repo.transition_reservation(current.reservation_id,2,owner_id='owner',phase='indexing')
        assert repo.authorize_candidates('owner',[records[3].chunk_id],{records[3].chunk_id:seq.revision})=={}
        with pytest.raises(ValueError,match='incompleta'):
            repo.publish_ready(current.reservation_id,2,owner_id='owner',embedding_key=KEY)
        repo.record_index_ack('owner',records[3].chunk_id,KEY,reservation_id=current.reservation_id,
                              generation=2,decision_revision=seq.revision)
        repo.checkpoint_job(job.model_copy(update={'state':'complete'}),expected_attempt=1,
            expected_cursor=None,reservation_id=current.reservation_id,generation=2)
        repo.publish_ready(current.reservation_id,2,owner_id='owner',embedding_key=KEY)
        assert repo.authorize_candidates('owner',[records[3].chunk_id],{records[3].chunk_id:seq.revision})=={records[3].chunk_id:records[3]}
        assert repo.authorize_candidates('other',[records[3].chunk_id],{records[3].chunk_id:seq.revision})=={}
        assert repo.authorize_candidates('owner',[records[3].chunk_id],{records[3].chunk_id:1})=={}
        assert repo.get_reconciliation_job('owner','queue-job-1').state=='complete'
        assert repo.get_reconciliation_job('other','queue-job-1') is None
        assert repo.list_reconciliation_chunks('owner',current.document_version_id,limit=1)==[records[3]]
        assert repo.list_reconciliation_chunks('owner',current.document_version_id,cursor=records[3].chunk_id)==[]
        before=repo.get_document_version(current.document_version_id),repo.get_chunk(records[3].chunk_id)
        repo.append_assessment(events(records,attempt='reclassify',start=4,outcome='injection',previous='attempt-1-d',policy='policy-v2'),3,
                               evaluated_input=formatted(records))
        assert repo.authorize_candidates('owner',[records[3].chunk_id],{records[3].chunk_id:seq.revision})=={}
        assert before==(repo.get_document_version(current.document_version_id),repo.get_chunk(records[3].chunk_id))
        with pytest.raises(ValueError):
            repo.record_index_ack('owner',records[3].chunk_id,KEY,reservation_id=current.reservation_id,
                                  generation=2,decision_revision=seq.revision)

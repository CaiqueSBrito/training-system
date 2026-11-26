"""T021: consumidores reais de PG/Qdrant; somente chamadas de modelos são dublês."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import subprocess
import time
from urllib.request import urlopen

import pytest
from sqlalchemy import create_engine, event, select, text
from qdrant_client import QdrantClient
from training_contracts import Classification, InputVerdict, RawGenerationOutput, SearchPlan, OutputJudgeResult
from training_generation import GenerationMetadata, GenerationError
from training_generation.chat import ChatPipeline
from training_lineage import create_lineage_engine, migrate, LineageRepository
from training_lineage.chat import ChatLineageRepository
from training_lineage import tables as t
from training_llm import ModelSettings
from training_ingestion import Ingestor, ChunkConfig
from training_ingestion.indexing import QdrantIndex, IndexingPipeline
from training_ingestion.retrieval.eligibility import SafeRetriever


@pytest.fixture(scope='module')
def qdrant_url():
    name='test-t021-'+uuid4().hex
    subprocess.run(['docker','run','--pull=never','--rm','-d','--name',name,'-p','127.0.0.1::6333',
        '-e','QDRANT__TELEMETRY_DISABLED=true','qdrant/qdrant:v1.20.2'],check=True,capture_output=True)
    try:
        url='http://'+subprocess.check_output(['docker','port',name,'6333/tcp'],text=True).strip()
        until=time.monotonic()+30
        while True:
            try:
                with urlopen(url+'/readyz',timeout=1) as response: assert response.status==200
                break
            except (OSError,AssertionError):
                if time.monotonic()>until: raise RuntimeError('Qdrant descartável indisponível') from None
                time.sleep(.2)
        yield url
    finally:
        subprocess.run(['docker','rm','-f',name],check=True,capture_output=True)


class Embeddings:
    def embed_document(self,*args): return [1.]+[0.]*3071
    def embed_query(self,*args): return [1.]+[0.]*3071


class Reranker:
    cutoff=.5
    def score(self,queries,chunks): return [.99]*len(chunks)


class APIs:
    """Fronteira dos modelos; orquestração, RAG, SQL e proveniência continuam reais."""
    def __init__(self):
        self.settings=SimpleNamespace(max_input_chars=8000,max_items=20)
        self.calls=[]; self.kind='flashcard'; self.mode='consulta';self.intent='gerar_flashcards'
        self.malicious=False;self.fail_e2=False;self.slow=False;self.cancelled=False
        self.invalid=False;self.json_error=False;self.observe=None;self.reject_judge=False;self.reject_always=False;self.repair_bad=False
    def metadata(self):
        return GenerationMetadata(models={s:'test-external-model' for s in ('E2','E3','E4','E8','E10')},
            prompt_versions={p:'test-v1' for p in ('input_judge','classifier','pedagogue','generation','output_judge','output_rubric')},prompt_source='offline')
    async def input_verdict(self,prompt):
        self.calls.append('E2');await asyncio.sleep(.01)
        if self.fail_e2: raise GenerationError('timeout',stage='E2')
        return InputVerdict(malicioso=self.malicious,categoria='injection' if self.malicious else None,motivo='resultado teste')
    async def classify(self,prompt):
        self.calls.append('E3')
        if self.slow:
            try: await asyncio.sleep(30)
            finally: self.cancelled=True
        return Classification(intencao=self.intent,modo=None if self.intent=='fora_de_escopo' else self.mode)
    async def plan(self,prompt,classification):
        self.calls.append('E4');return SearchPlan(objetivos=['Entender células'],bloom_level='lembrar',consultas=['células'])
    async def generate(self,prompt,classification,plan,context,count,repair_items=None,*,item_type=None):
        self.calls.append(('E8',count,repair_items))
        if self.json_error and sum(isinstance(c,tuple) for c in self.calls)==1:
            raise GenerationError('invalid_json',stage='E8')
        if self.observe: self.observe()
        result=[]
        for i in range(count):
            chunk=context.chunks[-1] if repair_items else context.chunks[i%len(context.chunks)]
            common=dict(tipo=item_type,bloom_level='lembrar',citations=[dict(chunk_ref=chunk.ref,evidence=chunk.text)])
            if item_type=='flashcard': common.update(front=f'Qual propriedade {i} de {chunk.chunk_id[:8]}?' if not repair_items else f'Explique o resultado específico de {chunk.chunk_id[-8:]}',back='Propriedade descrita: '+chunk.text)
            else: common.update(stem=f'O que é descrito por {chunk.chunk_id[:8]}?',options=['Membrana celular','Estrela distante'],correct_index=0,explanation=chunk.text)
            if (self.invalid and repair_items is None and i==count-1) or (self.repair_bad and repair_items): common['citations']=[]
            result.append(common)
        return RawGenerationOutput(itens=result)
    async def judge(self,items,context):
        self.calls.append(('E10',len(items)))
        first=sum(isinstance(call,tuple) and call[0]=='E10' for call in self.calls)==1
        return OutputJudgeResult(vereditos=[dict(item_index=i,fundamentado=not (self.reject_judge and i==len(items)-1 and (first or self.reject_always)),conforme_rubrica=True,
            motivo='Não fundamentado no teste' if self.reject_judge and i==len(items)-1 and (first or self.reject_always) else None) for i in range(len(items))])


@pytest.fixture
def sandbox(qdrant_url,tmp_path):
    admin=create_lineage_engine();name='test_t021_'+uuid4().hex
    with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
        c.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
    db=create_engine(admin.url.set(database=name),hide_parameters=True)
    client=QdrantClient(url=qdrant_url,cloud_inference=True)
    index=QdrantIndex(client,ModelSettings(qdrant_url=qdrant_url));index.ensure_collection()
    try:
        migrate(db)
        embeddings=Embeddings();processor=IndexingPipeline(embeddings,index)
        path=tmp_path/'source.md'
        path.write_text('# Biologia\n\nA membrana celular controla a entrada de substâncias.\n\n## Energia\n\nA mitocôndria produz ATP pela respiração celular.\n\n## Núcleo\n\nO núcleo armazena o DNA da célula.\n\n## Citosol\n\nO citosol contém enzimas e solutos.',encoding='utf-8')
        result=Ingestor(db,processor=processor,config=ChunkConfig(target_tokens=18,max_tokens=38,overlap_tokens=0)).ingest(path,'owner')
        apis=APIs()
        yield SimpleNamespace(engine=db,index=index,embeddings=embeddings,processor=processor,
            version=result.version.document_version_id,apis=apis,path=path,
            pipeline=ChatPipeline(db,apis,index,embeddings,Reranker()))
    finally:
        client.delete_collection(index.collection_name);client.close();db.dispose()
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
            c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def run(box,message='Gere flashcards',**kwargs):
    return asyncio.run(box.pipeline.run('owner','session',message,document_version_ids=[box.version],count=kwargs.pop('count',1),**kwargs))


def test_initial_public_ingestion_is_searchable_without_reconciliation(sandbox):
    box=sandbox
    found=SafeRetriever(box.engine,box.processor).search('owner','células')
    assert found
    assert SafeRetriever(box.engine,box.processor).search('other','células')==[]
    with box.engine.connect() as c:
        assert c.execute(select(t.ingestion_reservation.c.generation)).scalar_one()==1
        assert c.execute(select(t.ingestion_reservation.c.phase)).scalar_one()=='ready'
        points=box.index.client.retrieve(box.index.collection_name,[box.index.point_id(found[0].chunk_id)],with_payload=True)
        assert points[0].payload['decision_revision']>0 and points[0].payload['input_sha256']


@pytest.mark.parametrize('failure',['malicious','e2timeout'])
def test_e2_rejects_and_cancels_slow_e3(sandbox,failure):
    box=sandbox;box.apis.slow=True;box.apis.malicious=failure=='malicious';box.apis.fail_e2=failure=='e2timeout'
    response=run(box)
    assert response.status.value=='recusa' and box.apis.cancelled
    assert 'E4' not in box.apis.calls and not response.itens
    with box.engine.connect() as c:
        record=c.execute(select(t.chat_turn.c.record)).scalar_one()
        assert record['intencao'] is None and record['modo'] is None
        assert (record['input_verdict'] is None)==(failure=='e2timeout')


@pytest.mark.parametrize('message,reason',[('x'*8001,'tamanho_excedido'),('\u200b'*8001,'tamanho_excedido'),('\u200b\u2060',None),('ﬃ'*3000,'tamanho_excedido')])
def test_e1_zero_llm_and_normalized_empty(sandbox,message,reason):
    response=run(sandbox,message)
    assert sandbox.apis.calls==[] and response.itens==[]
    assert response.status.value==('erro' if reason is None else 'recusa')
    assert (response.motivo_recusa.value if response.motivo_recusa else None)==reason
    with sandbox.engine.connect() as c:
        row=c.execute(select(t.chat_turn.c.record)).scalar_one()
        assert row['intencao'] is None and row['input_verdict'] is None and row['models']=={}


def test_outside_scope_no_rag(sandbox):
    sandbox.apis.intent='fora_de_escopo'
    assert run(sandbox).status.value=='fora_de_escopo'
    assert sandbox.apis.calls==['E2','E3']


def test_insufficient_zero_generation(sandbox):
    with sandbox.engine.begin() as c:
        repo=LineageRepository(c);repo.begin_reconciliation('owner',sandbox.version,sandbox.index.settings.embedding_key,'test')
    result=run(sandbox)
    assert result.status.value=='conteudo_insuficiente'
    assert not any(isinstance(call,tuple) for call in sandbox.apis.calls)


@pytest.mark.parametrize('kind,intent',[('flashcard','gerar_flashcards'),('question','gerar_questionario')])
def test_delivered_items_have_exact_owner_lineage_and_real_trace(sandbox,kind,intent):
    sandbox.apis.kind=kind;sandbox.apis.intent=intent
    response=run(sandbox)
    assert response.status.value=='ok' and len(response.itens)==1
    assert response.itens[0].conteudo.tipo==kind and int(response.trace_id,16)>0
    with sandbox.engine.connect() as c:
        repo=ChatLineageRepository(c)
        found=repo.get_item_lineage('owner',response.itens[0].item_id)
        assert found['lineage'].trace_id==response.trace_id and found['spans']
        assert repo.get_item_lineage('other',response.itens[0].item_id) is None
        assert c.execute(select(t.chat_turn.c.response)).scalar_one()==response.model_dump(mode='json')


def test_one_repair_preserves_good_item_and_ids(sandbox):
    sandbox.apis.invalid=True
    response=run(sandbox,count=2)
    assert response.status.value=='ok' and len(response.itens)==2
    calls=[c for c in sandbox.apis.calls if isinstance(c,tuple) and c[0]=='E8']
    assert [c[1] for c in calls]==[2,1] and calls[1][2][0].item['citations']==[]
    with sandbox.engine.connect() as c:
        records=c.execute(select(t.chat_item.c.record)).scalars().all()
        assert len(records)==2 and {r['item_id'] for r in records}=={i.item_id for i in response.itens}
        assert sorted(c.execute(select(t.chat_verdict.c.record)).scalars(),key=lambda v:v['verdict_id'])


def test_invalid_json_consumes_global_repair_budget(sandbox):
    sandbox.apis.json_error=True;sandbox.apis.invalid=True
    response=run(sandbox,count=1)
    assert response.status.value=='ok' and len(response.omissoes)==1 and not response.itens
    assert len([c for c in sandbox.apis.calls if isinstance(c,tuple) and c[0]=='E8'])==2


def test_commit_failure_never_delivers_items(sandbox):
    commits=[]
    def fail(connection):
        commits.append(connection.execute(select(t.chat_item.c.item_id)).scalars().all())
        raise RuntimeError('commit failed test sentinel')
    sandbox.apis.observe=lambda: event.listen(sandbox.engine,'commit',fail)
    try:
        response=run(sandbox)
        assert response.status.value=='erro' and response.itens==[] and 'sentinel' not in response.mensagem
        assert any(commits),'Falha precisa ocorrer no commit de um grafo completo, não antes'
    finally:
        event.remove(sandbox.engine,'commit',fail)
    with sandbox.engine.connect() as c:
        assert not c.execute(select(t.chat_item)).all()


def test_permission_changes_while_generation_prevent_delivery(sandbox):
    def revoke():
        with sandbox.engine.begin() as c:
            LineageRepository(c).begin_reconciliation('owner',sandbox.version,sandbox.index.settings.embedding_key,'revoked')
    sandbox.apis.observe=revoke
    response=run(sandbox)
    assert response.status.value=='erro' and not response.itens
    assert any(e['stage']=='E12' and e['kind']=='PermissionError' for e in sandbox.pipeline.last_execution['errors'])
    with sandbox.engine.connect() as c:
        assert not c.execute(select(t.chat_item)).all()
        assert c.execute(select(t.chat_turn.c.record)).scalar_one()['status']=='erro'


def test_generate_more_keeps_plan_cursor_owner_and_prevents_replay(sandbox):
    sandbox.apis.mode='cobertura'
    first=run(sandbox)
    assert first.itens
    sandbox.apis.intent='gerar_mais'
    second=run(sandbox,'Gere mais')
    assert second.itens and set(s.chunk_id for s in first.fontes).isdisjoint(s.chunk_id for s in second.fontes)
    assert sandbox.apis.calls.count('E4')==1
    with sandbox.engine.begin() as c:
        repo=ChatLineageRepository(c);state=repo.get_continuation('owner','session')
        assert state['quantity']==2 and len(state['used_chunk_ids'])>=2
        assert repo.get_continuation('other','session') is None
    result=asyncio.run(sandbox.pipeline.run('other','session','Gere mais',document_version_ids=[sandbox.version],count=1))
    assert result.status.value=='erro' and not result.itens


def test_escape_original_source_and_reject_altered_response(sandbox,tmp_path):
    source=tmp_path/'markup.md';source.write_text('# HTML\n\nO texto `<script>alert("x")</script>` usa & e aspas.',encoding='utf-8')
    result=Ingestor(sandbox.engine,processor=sandbox.processor).ingest(source,'owner')
    response=asyncio.run(sandbox.pipeline.run('owner','markup','Gere flashcard',document_version_ids=[result.version.document_version_id],count=1))
    assert response.itens and '&lt;script&gt;' in response.itens[0].conteudo.back
    assert '&amp;lt;' not in response.itens[0].conteudo.back
    with sandbox.engine.connect() as c:
        lineage=ChatLineageRepository(c).get_item_lineage('owner',response.itens[0].item_id)
        assert '<script>' in lineage['lineage'].content.back
        assert '<script>' in lineage['lineage'].citations[0].evidence
        assert c.execute(select(t.chat_turn.c.response).where(t.chat_turn.c.chat_turn_id==response.chat_turn_id)).scalar_one()==response.model_dump(mode='json')


def test_original_evidence_mapping_rejects_ambiguity_without_choosing_page():
    from training_generation.chat import _evidence_range
    assert _evidence_range('A molécula de e\u0301 usa ATP.','É usa ATP')==(14,24)
    with pytest.raises(ValueError,match='ambígua'):
        _evidence_range('Eco na primeira página. Eco na segunda página.','eco')


def test_response_tampering_fails_shared_rendering_check(sandbox):
    response=run(sandbox)
    assert response.itens
    from training_contracts import ChatTurnRecord,SearchPlanRecord,RetrievalRunRecord,ItemRecord,CitationRecord,VerdictRecord
    from training_contracts.ingestao import CitationSpanSelection
    with sandbox.engine.connect() as c:
        repo=ChatLineageRepository(c)
        turn=ChatTurnRecord.model_validate(c.execute(select(t.chat_turn.c.record)).scalar_one())
        plan=SearchPlanRecord.model_validate(c.execute(select(t.chat_plan.c.record)).scalar_one())
        retrieval=RetrievalRunRecord.model_validate(c.execute(select(t.chat_retrieval.c.record)).scalar_one())
        items=[ItemRecord.model_validate(r) for r in c.execute(select(t.chat_item.c.record)).scalars()]
        citations=[CitationRecord.model_validate(r) for r in c.execute(select(t.chat_citation.c.record)).scalars()]
        verdicts=[VerdictRecord.model_validate(r) for r in c.execute(select(t.chat_verdict.c.record)).scalars()]
        provenance={r['citation_id']:CitationSpanSelection.model_validate(r['selection']) for r in c.execute(select(t.chat_citation)).mappings()}
        bad=response.model_copy(deep=True)
        bad.itens[0].conteudo.back='<script>adulterado</script>'
        with pytest.raises(ValueError,match='Itens entregues'):
            repo.save_turn(turn,plan,retrieval,items,citations,verdicts,bad,provenance)


@pytest.mark.parametrize('failure',['upsert','stale_worker'])
def test_initial_ingestion_failure_or_stale_worker_never_acknowledges_or_publishes(sandbox,tmp_path,monkeypatch,failure):
    from training_ingestion.protocol import IngestionLeaseLost,IngestionStageError
    source=tmp_path/'new.md';source.write_text('# Outra fonte\n\nA água transporta solutos pela membrana.',encoding='utf-8')
    original=sandbox.index.upsert
    def fail(*args,**kwargs):
        if failure=='upsert': raise OSError('test transport failed')
        original(*args,**kwargs)
        with sandbox.engine.begin() as c:
            c.execute(text("UPDATE ingestion_reservation SET lease_until=clock_timestamp()-interval '1 second' WHERE document_version_id=:id"),
                {'id':args[0].document_version_id})
    monkeypatch.setattr(sandbox.index,'upsert',fail)
    with pytest.raises((IngestionStageError,IngestionLeaseLost)):
        Ingestor(sandbox.engine,processor=sandbox.processor).ingest(source,'owner')
    with sandbox.engine.connect() as c:
        version=c.execute(select(t.document_version.c.document_version_id).where(t.document_version.c.document_version_id!=sandbox.version)).scalar_one()
        assert not c.execute(select(t.index_ack).where(t.index_ack.c.document_version_id==version)).all()
        assert c.execute(select(t.ingestion_reservation.c.phase).where(t.ingestion_reservation.c.document_version_id==version)).scalar_one()!='ready'
        assert SafeRetriever(sandbox.engine,sandbox.processor).search('owner','água')
        assert all(ch.document_version_id!=version for ch in SafeRetriever(sandbox.engine,sandbox.processor).search('owner','água'))


def test_malformed_continuation_fails_before_models_and_does_not_reset(sandbox):
    sandbox.apis.mode='cobertura';assert run(sandbox).itens
    with sandbox.engine.begin() as c:
        state=c.execute(select(t.chat_continuation.c.state)).scalar_one()
        c.execute(t.chat_continuation.update().values(state=state|{'cursor':state['cursor']|{'binding':'wrong-owner'}}))
    sandbox.apis.calls.clear();sandbox.apis.intent='gerar_mais'
    response=run(sandbox,'Gere mais')
    # Correção da revisão (achado 1): o estado só é herdado por gerar_mais, conhecido após E3;
    # o cursor inválido falha antes de E4, recuperação e geração, sem reset.
    assert response.status.value=='erro' and not response.itens
    assert 'E4' not in sandbox.apis.calls and not any(isinstance(call,tuple) for call in sandbox.apis.calls)
    with sandbox.engine.connect() as c:
        assert c.execute(select(t.chat_continuation.c.state)).scalar_one()['quantity']==1


def test_concurrent_session_advance_rejects_stale_delivery(sandbox):
    def advance():
        with sandbox.engine.begin() as c:
            repo=ChatLineageRepository(c)
            from training_contracts import ChatTurnRecord
            turn=ChatTurnRecord(chat_turn_id='competing',session_id='session',user_id='owner',prompt='size',
                status='recusa',motivo_recusa='tamanho_excedido',trace_id=uuid4().hex,created_at=datetime.now(timezone.utc))
            response=__import__('training_contracts',fromlist=['ChatResponse']).ChatResponse(status='recusa',motivo_recusa='tamanho_excedido',
                chat_turn_id=turn.chat_turn_id,trace_id=turn.trace_id)
            repo.save_turn(turn,None,None,[],[],[],response)
            repo.save_continuation('owner','session',chat_turn_id=turn.chat_turn_id,scope={'document_version_ids':[sandbox.version]},
                cursor={'offset':1},used_chunk_ids=[],quantity=0)
    sandbox.apis.observe=advance
    result=run(sandbox)
    assert result.status.value=='erro' and not result.itens
    with sandbox.engine.connect() as c:
        assert not c.execute(select(t.chat_item)).all()
        assert c.execute(select(t.chat_continuation.c.chat_turn_id)).scalar_one()=='competing'


@pytest.mark.parametrize('mode',['consulta','cobertura'])
def test_real_stage_spans_hierarchy_without_fake_coverage_rerank(sandbox,mode):
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exporter=InMemorySpanExporter()
    sandbox.pipeline.provider.add_span_processor(SimpleSpanProcessor(exporter))
    sandbox.apis.mode=mode
    response=run(sandbox)
    assert response.itens
    spans=exporter.get_finished_spans();by_name={s.name:s for s in spans}
    assert {'chat_turn','E5_E7','E5_retrieval','E7_context'}<=set(by_name)
    assert ('E6_rerank' in by_name)==(mode=='consulta')
    if mode=='cobertura': assert by_name['E7_context'].attributes['training.E6.status']=='Não se aplica'
    for name in ['E5_retrieval','E7_context']+(['E6_rerank'] if mode=='consulta' else []):
        assert by_name[name].parent.span_id==by_name['E5_E7'].context.span_id
        assert by_name[name].context.trace_id==int(response.trace_id,16)
        assert sandbox.pipeline.last_execution['durations_seconds'][name]>0
    for span in spans:
        assert not any('A membrana celular' in str(v) for v in span.attributes.values())


def test_evaluation_capture_links_each_item_to_its_actual_generation_context(sandbox):
    sandbox.pipeline.evaluation_capture=True;sandbox.apis.invalid=True
    response=run(sandbox,count=2)
    assert len(response.itens)==2
    captured=sandbox.pipeline.last_evaluation
    calls={call['call_id']:call for call in captured['contexts']}
    assert [call['call'] for call in calls.values()]==['inicial','reparo']
    assert {item['attempt'] for item in captured['original_items']}=={1,2}
    from training_contracts.rendering import render_item
    with sandbox.engine.connect() as c:
        for item in captured['original_items']:
            assert calls[item['call_id']]['status']=='completed'
            assert calls[item['call_id']]['context']['chunks']
            stored=c.execute(select(t.chat_item.c.record).where(t.chat_item.c.item_id==item['item_id'])).scalar_one()
            assert stored['content']==item['content']
            delivered=next(i for i in response.itens if i.item_id==item['item_id'])
            assert render_item(__import__('training_contracts',fromlist=['validar_item']).validar_item(item['content']))==delivered.conteudo


def test_two_owners_same_session_have_separate_real_sources_and_progress(sandbox):
    sandbox.apis.mode='cobertura'
    first=run(sandbox)
    other=Ingestor(sandbox.engine,processor=sandbox.processor).ingest(sandbox.path,'other')
    second=asyncio.run(sandbox.pipeline.run('other','session','Gere flashcards',
        document_version_ids=[other.version.document_version_id],count=1))
    assert first.itens and second.itens
    assert set(s.chunk_id for s in first.fontes).isdisjoint(s.chunk_id for s in second.fontes)
    with sandbox.engine.begin() as c:
        repo=ChatLineageRepository(c)
        assert repo.get_continuation('owner','session')['quantity']==1
        assert repo.get_continuation('other','session')['quantity']==1
        assert repo.get_item_lineage('owner',second.itens[0].item_id) is None
        assert repo.get_item_lineage('other',first.itens[0].item_id) is None


@pytest.mark.parametrize('persistent',[False,True])
def test_output_judge_repairs_only_failed_and_omits_after_one_repair(sandbox,persistent):
    sandbox.apis.reject_judge=True;sandbox.apis.reject_always=persistent
    response=run(sandbox,count=2)
    assert response.status.value=='ok'
    assert len(response.itens)==(1 if persistent else 2)
    calls=[call for call in sandbox.apis.calls if isinstance(call,tuple)]
    assert [call[1] for call in calls if call[0]=='E8']==[2,1]
    assert [call[1] for call in calls if call[0]=='E10']==[2,1]
    if persistent:
        assert len(response.omissoes)==1 and response.omissoes[0].motivo.value=='reprovado_no_juiz_de_saida'
    with sandbox.engine.connect() as c:
        records=c.execute(select(t.chat_item.c.record)).scalars().all()
        good=next(r for r in records if r['item_id']==response.itens[0].item_id)
        assert good['raw_content']['front']==good['content']['front']
        assert len(records)==2


def test_persistent_e9_failure_keeps_reason_and_never_third_generation(sandbox):
    sandbox.apis.invalid=True;sandbox.apis.repair_bad=True
    response=run(sandbox,count=2)
    assert len(response.itens)==1 and len(response.omissoes)==1
    assert response.omissoes[0].motivo.value=='sem_citacao'
    assert [call[1] for call in sandbox.apis.calls if isinstance(call,tuple) and call[0]=='E8']==[2,1]
    with sandbox.engine.connect() as c:
        omitted=c.execute(select(t.chat_item.c.record).where(t.chat_item.c.item_id==response.omissoes[0].item_id)).scalar_one()
        assert omitted['status']=='omitido' and omitted['motivo']=='sem_citacao'
        assert not c.execute(select(t.chat_citation).where(t.chat_citation.c.item_id==omitted['item_id'])).all()


def test_new_request_resets_explicitly_then_more_uses_new_mode_type(sandbox):
    first=run(sandbox)
    sandbox.apis.intent='gerar_questionario';sandbox.apis.mode='cobertura'
    second=run(sandbox,'Faça um questionário cobrindo o documento')
    assert first.itens and second.itens and second.itens[0].conteudo.tipo=='question'
    with sandbox.engine.begin() as c:
        assert ChatLineageRepository(c).get_continuation('owner','session')['quantity']==1
    sandbox.apis.intent='gerar_mais'
    third=run(sandbox,'Gere mais questões')
    assert third.itens and third.itens[0].conteudo.tipo=='question'
    assert set(s.chunk_id for s in second.fontes).isdisjoint(s.chunk_id for s in third.fontes)
    assert sandbox.apis.calls.count('E4')==2


def test_fresh_request_reuses_sources_and_recovers_bad_old_cursor(sandbox):
    first=run(sandbox)
    with sandbox.engine.begin() as c:
        old=c.execute(select(t.chat_continuation.c.state)).scalar_one()
        c.execute(t.chat_continuation.update().values(state=old|{'cursor':old['cursor']|{'binding':'broken'}}))
    second=run(sandbox,'Crie um novo flashcard sobre a mesma fonte')
    assert first.itens and second.itens
    assert [s.chunk_id for s in first.fontes]==[s.chunk_id for s in second.fontes]
    with sandbox.engine.begin() as c:
        assert ChatLineageRepository(c).get_continuation('owner','session')['quantity']==1


def test_repair_and_judge_only_receive_filtered_section(sandbox,tmp_path):
    path=tmp_path/'mixed.md'
    # Chunk nunca cruza H1 e herda o caminho do primeiro span: a introdução fecha o 1º chunk e o
    # 2º começa em Permitida e entra em Outra (chunk misto real, como na ressalva da T-018).
    path.write_text('# Manual\n\nEste manual reúne conceitos de biologia celular para estudo dirigido.\n\n'
        '## Permitida\n\nA membrana controla substâncias.\n\n## Outra\n\nFORA_DA_SECAO: A mitocôndria produz energia.',encoding='utf-8')
    result=Ingestor(sandbox.engine,processor=sandbox.processor,config=ChunkConfig(target_tokens=18,max_tokens=38,overlap_tokens=0)).ingest(path,'owner')
    with sandbox.engine.connect() as c:
        mixed=[r for r in c.execute(select(t.chunk.c.section_path,t.chunk.c.text).where(t.chunk.c.document_version_id==result.version.document_version_id)).all()
            if list(r.section_path)==['Manual','Permitida']]
        assert mixed and any('FORA_DA_SECAO' in r.text for r in mixed),'fixture precisa de chunk misto real'
    original_generate=sandbox.apis.generate;original_judge=sandbox.apis.judge;seen=[]
    async def generate(*args,**kwargs):
        context=args[3];seen.append(('generate',bool(kwargs.get('repair_items') or (len(args)>5 and args[5])),context.model_dump(mode='json')))
        assert all('FORA_DA_SECAO' not in chunk.text for chunk in context.chunks)
        return await original_generate(*args,**kwargs)
    async def judge(items,context):
        seen.append(('judge',False,context.model_dump(mode='json')))
        assert all('FORA_DA_SECAO' not in chunk.text for chunk in context.chunks)
        return await original_judge(items,context)
    sandbox.apis.generate=generate;sandbox.apis.judge=judge;sandbox.apis.invalid=True
    response=asyncio.run(sandbox.pipeline.run('owner','filtered','Crie um card',document_version_ids=[result.version.document_version_id],
        section_prefix=['Manual','Permitida'],count=1))
    assert response.itens and [kind for kind,_,_ in seen]==['generate','generate','judge']
    assert seen[0][2]==seen[1][2]==seen[2][2]


def test_unknown_local_ref_is_rejected_as_local_not_global(sandbox):
    original=sandbox.apis.generate;sandbox.apis.mode='cobertura'
    async def bad(*args,**kwargs):
        output=await original(*args,**kwargs)
        for item in output.itens:
            item['citations'][0]['chunk_ref']='C999'
        return output
    sandbox.apis.generate=bad
    response=run(sandbox,count=2)
    assert not response.itens and response.omissoes
    assert {omission.motivo.value for omission in response.omissoes}=={'citacao_inexistente'}


def test_every_generation_attempt_is_durably_audited(sandbox):
    sandbox.apis.invalid=True
    response=run(sandbox,count=2)
    assert len(response.itens)==2
    with sandbox.engine.connect() as c:
        repo=ChatLineageRepository(c)
        attempts=[repo.get_generation_attempts('owner',item.item_id) for item in response.itens]
        assert sorted(len(history) for history in attempts)==[1,2]
        repaired=next(history for history in attempts if len(history)==2)
        assert [entry['attempt'] for entry in repaired]==[1,2]
        assert repaired[0]['raw_content']['citations']==[] and repaired[1]['raw_content']['citations']
        assert repaired[0]['call_id']!=repaired[1]['call_id'] and all(entry['context']['chunks'] for entry in repaired)
        assert repo.get_generation_attempts('other',response.itens[0].item_id)==[]


def test_oversized_prompt_is_bounded_and_hashed_in_audit(sandbox):
    from hashlib import sha256
    prompt='private-input-'*100000
    response=run(sandbox,prompt)
    assert response.status.value=='recusa' and not sandbox.apis.calls
    with sandbox.engine.connect() as c:
        record=c.execute(select(t.chat_turn.c.record)).scalar_one()
        audit=c.execute(select(t.chat_input_audit)).mappings().one()
        assert len(record['prompt'])<=sandbox.apis.settings.max_input_chars
        assert audit['raw_length']==len(prompt) and audit['raw_sha256']==sha256(prompt.encode()).hexdigest()
        assert audit['stored_length']==len(record['prompt'])


def test_cli_capture_rejects_symlink_without_modifying_target(tmp_path,capsys):
    from training_generation.cli import main
    target=tmp_path/'target';target.write_text('preserve')
    link=tmp_path/'capture';link.symlink_to(target)
    result=main(['--owner','local-owner','--session','local-session','--version','version',
        '--message','message','--calibration','packages/retrieval/calibration/bge-v1.json','--evaluation-capture',str(link)])
    assert result==2 and target.read_text()=='preserve'
    assert 'Traceback' not in capsys.readouterr().err


def test_invalid_initial_envelope_repair_is_attempt_two_persisted_and_t022_consumable(sandbox):
    """Consumidor real: envelope inicial inválido -> reparo válido -> linhagem e captura T-022."""
    from evals.runner_geracao import E8Capture
    sandbox.apis.json_error=True;sandbox.pipeline.evaluation_capture=True
    response=run(sandbox,count=2)
    assert response.status.value=='ok' and len(response.itens)==2
    capture=E8Capture.model_validate(sandbox.pipeline.last_evaluation)
    assert [(c.call,c.status) for c in capture.contexts]==[('inicial','failed'),('reparo','completed')]
    assert {item.attempt for item in capture.original_items}=={2}
    with sandbox.engine.connect() as c:
        repo=ChatLineageRepository(c)
        calls={call['call_id']:call for call in repo.get_generation_calls('owner',response.chat_turn_id)}
        initial=next(call for call in calls.values() if call['kind']=='inicial')
        assert (initial['status'],initial['failure_kind'])==('failed','invalid_json')
        for item in response.itens:
            history=repo.get_generation_attempts('owner',item.item_id)
            # Nenhum bruto inicial inventado: só a tentativa 2, ligada ao reparo da chamada falha.
            assert [(h['attempt'],h['call'],h['repairs_call_id']) for h in history]==[(2,'reparo',initial['call_id'])]
            verdicts=[v for v in c.execute(select(t.chat_verdict.c.record)).scalars() if v['item_id']==item.item_id]
            assert {v['tentativa'] for v in verdicts}=={2} and {v['origem'] for v in verdicts}=={'E9','E10'}
        assert repo.get_generation_calls('other',response.chat_turn_id)==[]


def test_coverage_repair_is_one_call_with_filtered_union_and_refs_mapped_back(sandbox):
    """Cobertura com vários grupos reprovados: UMA chamada E8 de reparo, contexto combinado injetivo
    e filtrado, e cada item reparado volta às refs do seu grupo e é entregue com fonte canônica."""
    from evals.runner_geracao import E8Capture
    sandbox.apis.mode='cobertura';sandbox.pipeline.evaluation_capture=True
    original_generate=sandbox.apis.generate;judged=[]
    async def generate(prompt,classification,plan,context,count,repair_items=None,*,item_type=None):
        if not repair_items: return await original_generate(prompt,classification,plan,context,count,repair_items,item_type=item_type)
        sandbox.apis.calls.append(('E8',count,repair_items))
        refs={c.ref for c in context.chunks}
        assert all(c['chunk_ref'] in refs for r in repair_items for c in r.item['citations'])
        return RawGenerationOutput(itens=[r.item|{'front':f'Explique a ideia reparada {k} da fonte citada'} for k,r in enumerate(repair_items)])
    async def judge(items,context):
        judged.append(len(items));sandbox.apis.calls.append(('E10',len(items)))
        first=len(judged)==1
        return OutputJudgeResult(vereditos=[dict(item_index=i,fundamentado=not first,conforme_rubrica=True,
            motivo='Reprovado no teste' if first else None) for i in range(len(items))])
    sandbox.apis.generate=generate;sandbox.apis.judge=judge
    response=run(sandbox,count=2)
    e8=[call for call in sandbox.apis.calls if isinstance(call,tuple) and call[0]=='E8']
    initial=[call for call in e8 if call[2] is None];repairs=[call for call in e8 if call[2] is not None]
    assert len(initial)>=2,'cenário precisa de vários grupos de cobertura'
    assert len(repairs)==1 and repairs[0][1]==len(initial)  # UMA chamada de reparo no turno
    assert judged==[len(initial),len(initial)]
    capture=E8Capture.model_validate(sandbox.pipeline.last_evaluation)
    repair_context=next(c for c in capture.contexts if c.call=='reparo').context
    assert len({c.chunk_id for c in repair_context.chunks})==len(repair_context.chunks)
    assert len(response.itens)==len(initial) and {i.attempt for i in capture.original_items}=={2}
    assert all(item.conteudo.front.startswith('Explique a ideia reparada') for item in response.itens)
    with sandbox.engine.connect() as c:
        repo=ChatLineageRepository(c)
        for item in response.itens:
            assert repo.get_item_lineage('owner',item.item_id)['spans']
            assert [h['attempt'] for h in repo.get_generation_attempts('owner',item.item_id)]==[1,2]


def _cli_harness(sandbox,qdrant_url,monkeypatch,*,classify=None,reranker_error=None):
    """CLI real com PG/Qdrant descartáveis; só modelos/SDK externos são dublês de teste."""
    import training_generation.cli as cli
    from google import genai
    closed=[]
    class Provider(cli.TracerProvider):
        def shutdown(self):
            closed.append('provider');return super().shutdown()
    class SdkClient:
        def __init__(self,**kwargs): self.kwargs=kwargs
        def close(self): closed.append('genai')
    class ModelClient(Embeddings):
        client=SdkClient()
    engine=create_engine(sandbox.engine.url,hide_parameters=True)
    original_dispose=engine.dispose
    engine.dispose=lambda *a,**k:(closed.append('engine'),original_dispose(*a,**k))[1]
    qdrant=QdrantClient(url=qdrant_url,cloud_inference=True)
    original_close=qdrant.close
    qdrant.close=lambda *a,**k:(closed.append('qdrant'),original_close(*a,**k))[1]
    index=QdrantIndex(qdrant,ModelSettings(qdrant_url=qdrant_url))
    apis=sandbox.apis
    if classify: apis.classify=classify
    monkeypatch.setattr(cli.GenerationSettings,'from_env',staticmethod(lambda path:SimpleNamespace(max_items=20,gemini_api_key='test-secret-key-123')))
    monkeypatch.setattr(cli,'create_lineage_engine',lambda path:engine)
    monkeypatch.setattr(cli.IndexingPipeline,'from_env',staticmethod(lambda path:SimpleNamespace(index=index,model_client=ModelClient())))
    monkeypatch.setattr(genai,'Client',SdkClient)
    monkeypatch.setattr(cli,'TracerProvider',Provider)
    monkeypatch.setattr(cli,'GenerationService',lambda settings,tracer_provider=None:apis)
    def reranker(path):
        if reranker_error: raise RuntimeError(reranker_error)
        return Reranker()
    monkeypatch.setattr(cli,'LocalReranker',reranker)
    args=['--owner','owner','--session','cli-session','--version',sandbox.version,'--message','Gere flashcards',
        '--count','1','--calibration','unused-by-test','--request-interval','0']
    return cli,args,closed


def test_cli_sigint_cancels_turn_exit_130_sanitized_and_closes_everything(sandbox,qdrant_url,monkeypatch,capsys):
    import signal,json
    async def classify(prompt):
        sandbox.apis.calls.append('E3')
        signal.raise_signal(signal.SIGINT)  # Ctrl+C real durante o E3; asyncio.run cancela o turno
        await asyncio.sleep(30)
    cli,args,closed=_cli_harness(sandbox,qdrant_url,monkeypatch,classify=classify)
    result=cli.main(args)
    out,err=capsys.readouterr()
    assert result==130 and out==''
    assert json.loads(err.strip().splitlines()[-1])=={'status':'erro','mensagem':'Turno cancelado.'}
    assert 'Traceback' not in err and 'test-secret-key-123' not in err
    # Dois clientes genai reais da CLI: o original (fechado na troca) e o substituto sem retentativa.
    assert sorted(closed)==['engine','genai','genai','provider','qdrant']
    assert 'E4' not in sandbox.apis.calls
    with sandbox.engine.connect() as c:
        turn=c.execute(select(t.chat_turn.c.record,t.chat_turn.c.response).where(t.chat_turn.c.session_id=='cli-session')).one()
        assert turn.record['status']=='erro' and turn.response['mensagem']=='Turno cancelado.' and turn.response['itens']==[]


def test_cli_infrastructure_error_exit_2_sanitized_and_closes_everything(sandbox,qdrant_url,monkeypatch,capsys):
    import json
    cli,args,closed=_cli_harness(sandbox,qdrant_url,monkeypatch,reranker_error='token=test-secret-key-123 at /secret/path')
    result=cli.main(args)
    out,err=capsys.readouterr()
    assert result==2 and out==''
    assert json.loads(err.strip())=={'status':'erro','mensagem':'Configuração ou infraestrutura indisponível.'}
    assert 'secret' not in err and 'Traceback' not in err
    # Dois clientes genai reais da CLI: o original (fechado na troca) e o substituto sem retentativa.
    assert sorted(closed)==['engine','genai','genai','provider','qdrant']
    with sandbox.engine.connect() as c:
        assert not c.execute(select(t.chat_turn)).all()

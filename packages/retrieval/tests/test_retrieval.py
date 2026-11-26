"""T018 cases written before implementation; real storage and one real CPU model.

External embedding/judge doubles live only here. They are not an embedding proof.
"""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import json
import subprocess
import time
from urllib.request import urlopen

import pytest
from qdrant_client import QdrantClient, models
from sqlalchemy import create_engine, text
from training_contracts.agentes import Classification, SearchPlan
from training_contracts.linhagem import ChunkRecord
from training_ingestion import Ingestor, DoclingParser, ChunkConfig
from training_ingestion.indexing import QdrantIndex, IndexingPipeline
from training_ingestion.reconciliation import Reconciler
from training_ingestion.retrieval.eligibility import SafeRetriever, EligibilityUnavailable
from training_lineage import LineageRepository, create_lineage_engine, migrate
from training_llm import ModelSettings, InjectionVerdict
from training_retrieval import (
    RetrievalService, RetrievalScope, RetrievalSettings, ScopeError, ContinuationError,
    LocalReranker, CalibrationError, largest_remainder,
)

PLAN = SearchPlan(objetivos=['Compreender membranas celulares'], bloom_level='compreender',
                  consultas=['Qual é a função da membrana celular?', 'membranas e transporte'])
QUERY = Classification(intencao='gerar_flashcards', modo='consulta')
COVERAGE = Classification(intencao='gerar_flashcards', modo='cobertura')


class ExternalModels:
    settings = ModelSettings(judge_model='gemini/test-boundary')
    def injection_judge_spec(self):
        return dict(provider='google', model='test-boundary', config_version='test',
                    config_sha256='a'*64, prompt_version='test', prompt_sha256='b'*64)
    def judge_injection(self, value):
        return InjectionVerdict(injection=False, reason='test-only boundary')
    def embed_document(self, title, section_path, value):
        return [1.] + [0.] * 3071
    def embed_query(self, query):
        return [1.] + [0.] * 3071


class TestReranker:
    __test__ = False
    cutoff = 0.5
    def score(self, queries, chunks):
        return [0.9 - i * .01 for i, _ in enumerate(chunks)]


@pytest.fixture(scope='module')
def qdrant_url():
    name = 'test-t018-' + uuid4().hex
    subprocess.run(['docker','run','--pull=never','--rm','-d','--name',name,
                    '-p','127.0.0.1::6333','-e','QDRANT__TELEMETRY_DISABLED=true',
                    'qdrant/qdrant:v1.20.2'], check=True, capture_output=True)
    try:
        url = 'http://' + subprocess.check_output(['docker','port',name,'6333/tcp'],text=True).strip()
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen(url + '/readyz', timeout=1) as r:
                    assert r.status == 200
                break
            except (OSError, AssertionError):
                if time.monotonic() > deadline:
                    raise RuntimeError('Disposable Qdrant unavailable') from None
                time.sleep(.2)
        yield url
    finally:
        subprocess.run(['docker','rm','-f',name],check=True,capture_output=True)


@pytest.fixture(scope='module')
def engine():
    admin = create_lineage_engine(); name = 'test_t018_' + uuid4().hex
    with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
        c.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
    db = create_engine(admin.url.set(database=name), hide_parameters=True)
    migrate(db)
    try:
        yield db
    finally:
        db.dispose()
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
            c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope='module')
def corpus(engine, qdrant_url, tmp_path_factory):
    client = QdrantClient(url=qdrant_url, cloud_inference=True)
    index = QdrantIndex(client, ModelSettings(qdrant_url=qdrant_url))
    processor = IndexingPipeline(ExternalModels(), index)
    ingestor = Ingestor(engine, parser=DoclingParser(), processor=processor,
                        config=ChunkConfig(target_tokens=18,max_tokens=24,overlap_tokens=0))
    root = tmp_path_factory.mktemp('t018')
    versions = []
    for owner in ['owner', 'foreign']:
        source = root / (owner + '.md')
        source.write_text('# Biologia\n\n## Membrana\n\nA membrana celular delimita a célula e controla a passagem de substâncias.\n\n'
                          '## Transporte\n\nA difusão transporta moléculas a favor do gradiente de concentração.\n\n'
                          '## Energia\n\nAs mitocôndrias produzem ATP pela respiração celular.\n',encoding='utf-8')
        result = ingestor.ingest(source, owner)
        assert Reconciler(engine,processor).reconcile_version(owner,result.version.document_version_id)['state']=='complete'
        versions.append((result.version, result.chunks))
    try:
        yield SimpleNamespace(index=index, processor=processor, versions=versions)
    finally:
        client.delete_collection(index.collection_name); client.close()


def service(engine, corpus, reranker=None, **settings):
    return RetrievalService(engine, corpus.index, corpus.processor.model_client,
                            reranker or TestReranker(), chat_turn_id='turn-t018',
                            settings=RetrievalSettings(**settings))


def scope(corpus, **kwargs):
    return RetrievalScope((corpus.versions[0][0].document_version_id,), **kwargs)


def test_largest_remainder_and_ties():
    assert largest_remainder([3, 1], 8) == [6, 2]
    assert largest_remainder([1, 1, 1], 2) == [1, 1, 0]
    assert largest_remainder([0, 1], 5) == [0, 5]
    with pytest.raises(ValueError): largest_remainder([], 2)


def test_hybrid_real_rrf_two_prefetch_owner_scope_and_union(engine, corpus, monkeypatch):
    client = corpus.index.client; original = client.query_points; calls = []
    def query(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(client, 'query_points', query)
    result = service(engine, corpus).retrieve('owner', PLAN, QUERY, scope(corpus), 8)
    assert result.context is not None
    assert len(calls) == 2
    for call in calls:
        assert len(call['prefetch']) == 2
        assert {p.using for p in call['prefetch']} == {'dense', 'bm25'}
        assert call['query'].fusion == models.Fusion.RRF
        assert all(p.filter == call['query_filter'] for p in call['prefetch'])
    assert len(result.chunks) <= 5
    assert len({c.chunk_id for c in result.run.candidates}) == len(result.run.candidates)
    assert all(c.document_version_id == corpus.versions[0][0].document_version_id for c in result.chunks)
    assert result.run.selected == [c.chunk_id for c in result.chunks]
    prefix=tuple(result.chunks[0].section_path)
    narrowed=service(engine,corpus).retrieve('owner',PLAN,QUERY,scope(corpus,section_prefix=prefix),8)
    assert narrowed.context is not None
    assert all(tuple(c.section_path[:len(prefix)])==prefix for c in narrowed.chunks)


def test_scope_foreign_missing_and_section_prefix_fail(engine, corpus):
    svc = service(engine, corpus)
    with pytest.raises(ScopeError): svc.retrieve('foreign', PLAN, QUERY, scope(corpus), 8)
    with pytest.raises(ScopeError): svc.retrieve('owner', PLAN, QUERY, RetrievalScope(('missing',)), 8)
    with pytest.raises(ScopeError): svc.retrieve('owner', PLAN, QUERY, scope(corpus,section_prefix=('missing',)), 8)
    with pytest.raises(ValueError): RetrievalScope(())
    with pytest.raises(ValueError): svc.retrieve('', PLAN, QUERY, scope(corpus), 8)


def test_coverage_reading_hierarchy_allocation_and_continuation(engine, corpus):
    svc = service(engine, corpus, top_n=2)
    first = svc.retrieve('owner', PLAN, COVERAGE, scope(corpus), 2)
    assert first.context is not None
    assert sum(a.count for a in first.allocations) == 2
    assert [c.start_offset for c in first.chunks] == sorted(c.start_offset for c in first.chunks)
    assert len(first.chunks)<=2
    next_scope = scope(corpus, continuation=first.continuation)
    second = svc.retrieve('owner', PLAN, COVERAGE, next_scope, 2)
    assert not ({c.chunk_id for c in first.chunks} & {c.chunk_id for c in second.chunks})
    assert second.continuation['remaining'] < first.continuation['remaining']
    with pytest.raises(ContinuationError):
        svc.retrieve('foreign', PLAN, COVERAGE, next_scope, 8)


def test_insufficiency_no_fabricated_context(engine, corpus):
    class LowScores(TestReranker):
        def score(self, queries, chunks): return [0.1] * len(chunks)
    result = service(engine, corpus, LowScores()).retrieve('owner', PLAN, QUERY, scope(corpus), 8)
    assert result.context is None and result.chunks == [] and not result.run.selected
    assert result.run.cutoff == .5


def test_shared_barrier_payload_tamper_and_missing_ack(engine, corpus):
    target = corpus.versions[0][1][0]
    point = corpus.index.client.scroll(corpus.index.collection_name,scroll_filter=models.Filter(must=[
        models.FieldCondition(key='chunk_id',match=models.MatchValue(value=target.chunk_id))]),limit=1)[0][0]
    old = dict(point.payload)
    try:
        corpus.index.client.set_payload(corpus.index.collection_name, {'input_sha256':'f'*64}, points=[point.id])
        assert target.chunk_id not in {c.chunk_id for c in SafeRetriever(engine,corpus.processor).search('owner','membrana')}
        assert target.chunk_id not in {c.chunk_id for c in service(engine,corpus).retrieve('owner',PLAN,QUERY,scope(corpus),8).chunks}
    finally:
        corpus.index.client.overwrite_payload(corpus.index.collection_name,old,points=[point.id])
    from training_lineage.tables import index_ack
    with engine.begin() as c:
        c.execute(index_ack.delete().where(index_ack.c.chunk_id==target.chunk_id))
    try:
        assert SafeRetriever(engine,corpus.processor).search('owner','membrana') == []
        assert service(engine,corpus).retrieve('owner',PLAN,QUERY,scope(corpus),8).context is None
    finally:
        assert Reconciler(engine,corpus.processor).reconcile_version(
            'owner',target.document_version_id)['state']=='complete'


def test_storage_unavailable_sanitized(engine, corpus, monkeypatch):
    def fail(*args, **kwargs): raise RuntimeError('sensitive sentinel')
    monkeypatch.setattr(LineageRepository,'authorize_candidates', fail)
    with pytest.raises(EligibilityUnavailable, match='Linhagem indisponível'):
        service(engine,corpus).retrieve('owner',PLAN,QUERY,scope(corpus),8)


def test_calibration_required_and_invalid(tmp_path):
    with pytest.raises(CalibrationError): LocalReranker(tmp_path/'missing.json')
    path = tmp_path/'invalid.json'; path.write_text('{}')
    with pytest.raises(CalibrationError): LocalReranker(path)


def test_real_cpu_crossencoder_calibration_separate_dataset(engine, corpus):
    from training_retrieval.reranker import CALIBRATION_PAIRS
    path = Path(__file__).parents[1] / 'calibration' / 'bge-v1.json'
    manifest = json.loads(path.read_text())
    reranker = LocalReranker(path)
    chunks = [ChunkRecord(chunk_id=f'real-{i}',document_version_id='real-version',chunker_version='real-v1',
                          start_offset=0,end_offset=len(p['passage']),text=p['passage'])
              for i,p in enumerate(CALIBRATION_PAIRS[:2])]
    scores = reranker.score([CALIBRATION_PAIRS[0]['query']], chunks)
    assert scores[0] > manifest['cutoff'] > scores[1]
    assert manifest['status'] == 'Passou'
    assert manifest['dataset_id'] != 'ressalvas-1.0.1'
    result=service(engine,corpus,reranker).retrieve('owner',PLAN,QUERY,scope(corpus),8)
    assert result.context is not None
    assert result.run.cutoff==manifest['cutoff']
    assert all(c.rerank_score>manifest['cutoff'] for c in result.run.candidates if c.selected)


@pytest.fixture
def proportional_corpus(engine, qdrant_url, tmp_path, request):
    """Real protocol, canonical provenance and overlapping chunks; parser double only here.

    Twelve 25-codepoint paragraphs in H1/H2/H3 A versus four in H1/H2/H3 B.
    Fifty-codepoint windows overlap by 25 and one window crosses A/B: its single
    chunk.section_path cannot represent its two canonical subsection contributions.
    """
    from training_contracts.ingestao import CanonicalTextRecord, CanonicalSpan
    from training_ingestion.core import ParsedDocument

    layout=getattr(request,'param',[('Grande',12),('Pequena',4)])
    paths=[name for name,count in layout for _ in range(count)]

    class ExactParser:
        version='test-canonical-proportions-v1'
        def parse(self, data, filename, version_id):
            value=data.decode(); cid='canonical-'+version_id
            canonical=CanonicalTextRecord(canonical_id=cid,document_version_id=version_id,
                canonical_version='test-exact-v1',parser_version=self.version,text=value,
                text_sha256=sha256(data).hexdigest(),created_at=datetime.now(timezone.utc))
            spans=tuple(CanonicalSpan(span_id=f'{cid}-{i}',canonical_id=cid,start=i*25,end=(i+1)*25,
                page=None,section_path=('Livro','Capítulo',paths[i]),
                h1_id=cid+'-h1',kind='paragraph',provenance='exact') for i in range(len(paths)))
            return ParsedDocument(canonical,spans,title='Proporção canônica')

    client=QdrantClient(url=qdrant_url,cloud_inference=True)
    index=QdrantIndex(client,ModelSettings(qdrant_url=qdrant_url))
    processor=IndexingPipeline(ExternalModels(),index)
    ingestor=Ingestor(engine,parser=ExactParser(),processor=processor,
        config=ChunkConfig(target_tokens=12,max_tokens=20,overlap_tokens=7))
    path=tmp_path/'proportions.md'
    path.write_text('A membrana envolve célula'*len(paths),encoding='utf-8')
    # ExactParser partitions codepoints, not UTF-8 bytes.
    assert len(path.read_text())==25*len(paths)
    result=ingestor.ingest(path,'proportional-owner')
    assert Reconciler(engine,processor).reconcile_version('proportional-owner',
        result.version.document_version_id)['state']=='complete'
    try:
        yield SimpleNamespace(index=index,processor=processor,versions=[(result.version,result.chunks)])
    finally:
        client.close()


def test_coverage_proportional_full_canonical_subsections_not_top_n(engine,proportional_corpus):
    corpus=proportional_corpus
    class AllRelevant(TestReranker):
        def score(self,queries,chunks):
            raise AssertionError('Coverage must not discard subsections by thematic relevance')
    svc=service(engine,corpus,AllRelevant(),top_n=5)
    result=svc.retrieve('proportional-owner',PLAN,COVERAGE,scope(corpus),8)
    assert len(result.chunks)>5  # coverage is paged by item budget, never thematic top_n
    assert {a.section_path:a.count for a in result.allocations}=={
        ('Livro','Capítulo','Grande'):6,('Livro','Capítulo','Pequena'):2}
    assert {a.section_path:a.canonical_size for a in result.allocations}=={
        ('Livro','Capítulo','Grande'):300,('Livro','Capítulo','Pequena'):100}
    # Overlap is real, so summing chunk lengths cannot be a valid size estimator.
    chunks=corpus.versions[0][1]
    assert sum(len(c.text) for c in chunks)>400
    assert any(c.start_offset<300<c.end_offset for c in chunks)
    assert len(result.chunks)<=8
    assert result.run.cutoff is None
    assert all(c.rerank_score is None for c in result.run.candidates)
    allowed={c.chunk_id for c in chunks}
    assert {c.chunk_id for c in result.run.candidates}<=allowed
    seen={c.chunk_id for c in result.chunks}
    while result.continuation['remaining']:
        result=svc.retrieve('proportional-owner',PLAN,COVERAGE,
            scope(corpus,continuation=result.continuation),8)
        assert not seen & {c.chunk_id for c in result.chunks}
        assert [c.start_offset for c in result.chunks]==sorted(c.start_offset for c in result.chunks)
        seen.update(c.chunk_id for c in result.chunks)
        for allocation in result.allocations:
            assert allocation.canonical_size==({'Grande':300,'Pequena':100}[allocation.section_path[-1]])
    assert seen==allowed
    small=svc.retrieve('proportional-owner',PLAN,COVERAGE,
        scope(corpus,section_prefix=('Livro','Capítulo','Pequena')),8)
    assert [(a.section_path,a.count,a.canonical_size) for a in small.allocations]==[
        (('Livro','Capítulo','Pequena'),8,100)]
    assert any(c.start_offset<300<c.end_offset for c in small.chunks)
    with pytest.raises((ScopeError,ContinuationError)):
        svc.retrieve('foreign',PLAN,COVERAGE,scope(corpus,continuation=result.continuation),8)


@pytest.mark.parametrize('proportional_corpus',
    [[*(('Pequena'+str(i),2) for i in range(6)),('Grande',36)]],indirect=True)
def test_coverage_large_subsection_beyond_first_top_n_is_not_lost(engine,proportional_corpus):
    corpus=proportional_corpus
    class NoThematicFilter(TestReranker):
        def score(self,queries,chunks):
            raise AssertionError('No thematic score may exclude a coverage subsection')
    ordered=sorted(corpus.versions[0][1],key=lambda c:c.start_offset)
    assert all(c.end_offset<=300 for c in ordered[:5])
    result=service(engine,corpus,NoThematicFilter(),top_n=5).retrieve(
        'proportional-owner',PLAN,COVERAGE,scope(corpus),8)
    allocations={a.section_path[-1]:a for a in result.allocations}
    assert allocations['Grande'].canonical_size==900
    assert allocations['Grande'].count==6
    assert len(allocations)==7  # zero-share subsections stay explicit, for continuation
    assert sum(a.count for key,a in allocations.items() if key!='Grande')==2
    assert all(a.canonical_size==50 for key,a in allocations.items() if key!='Grande')
    assert len(result.chunks)<=8 and result.run.cutoff is None
    assert any(c.start_offset>=300 for c in result.chunks)
    assert allocations['Grande'].chunk_ids
    assert result.continuation['remaining']>0

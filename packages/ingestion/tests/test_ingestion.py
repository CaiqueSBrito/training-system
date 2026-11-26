from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from qdrant_client import QdrantClient
from training_lineage import create_lineage_engine, migrate, LineageRepository
from training_llm import InjectionVerdict, ModelSettings
from training_ingestion import (
    IngestionError, DoclingParser, Chunker, ChunkConfig, ParsedDocument,
    count_tokens, Ingestor,
)
from training_ingestion.indexing import IndexingPipeline, QdrantIndex
from test_ressalvas_parse import document as canonical_document

FIXTURES = Path(__file__).parent / "fixtures"


class _ExternalModels:
    """Dublê da fronteira externa exclusivo dos testes de ingestão."""
    def __init__(self, settings):
        self.settings=settings
    def injection_judge_spec(self):
        return {'provider':'google','model':'test-model','config_version':'test-config',
                'config_sha256':'a'*64,'prompt_version':'test-prompt','prompt_sha256':'b'*64}
    def judge_injection(self, text):
        return InjectionVerdict(injection=False,reason='test-safe')
    def embed_document(self, title, section_path, text):
        return [1.0]+[0.0]*3071
    def embed_query(self, query):
        return [1.0]+[0.0]*3071


@pytest.fixture(scope='module')
def qdrant_url():
    name='test-ingestion-qdrant-'+uuid4().hex
    subprocess.run(['docker','run','--pull=never','--rm','-d','--name',name,
        '-p','127.0.0.1::6333','-e','QDRANT__TELEMETRY_DISABLED=true',
        'qdrant/qdrant:v1.20.2'],check=True,capture_output=True)
    try:
        url='http://'+subprocess.check_output(['docker','port',name,'6333/tcp'],text=True).strip()
        until=time.monotonic()+30
        while True:
            try:
                with urlopen(url+'/readyz',timeout=1) as response:
                    assert response.status==200
                break
            except (OSError,AssertionError):
                if time.monotonic()>until: raise RuntimeError('Qdrant isolado indisponível') from None
                time.sleep(.2)
        yield url
    finally:
        subprocess.run(['docker','rm','-f',name],check=True,capture_output=True)


@pytest.fixture(scope='module')
def processor(qdrant_url):
    settings=ModelSettings(qdrant_url=qdrant_url)
    client=QdrantClient(url=qdrant_url,cloud_inference=True)
    index=QdrantIndex(client,settings)
    pipeline=IndexingPipeline(_ExternalModels(settings),index)
    try:
        yield pipeline
    finally:
        client.delete_collection(settings.embedding_key)
        client.close()


@pytest.fixture(scope="module")
def engine():
    admin = create_lineage_engine()
    name = f"test_ingestion_{uuid4().hex}"
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
    db = create_engine(admin.url.set(database=name), hide_parameters=True)
    migrate(db, "head")
    try:
        yield db
    finally:
        db.dispose()
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="module")
def parser():
    return DoclingParser()


@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
def test_ING_FMT_001_ING_CHK_001_real_docling_formats(parser, extension):
    parsed = parser.parse((FIXTURES / f"study.{extension}").read_bytes(), f"study.{extension}", "version")
    assert "Cells contain genetic material." in parsed.text
    assert "Atoms form molecules." in parsed.text
    assert any(b.section_path and "Biology" in b.section_path for b in parsed.spans)
    tables = [b for b in parsed.spans if b.kind == "table"]
    assert tables and "Nucleus" in parsed.text[tables[0].start:tables[0].end] and "DNA" in parsed.text[tables[0].start:tables[0].end]
    assert parsed.canonical.text_sha256 == sha256(parsed.text.encode()).hexdigest()
    if extension == "pdf":
        assert {b.page for b in parsed.spans} >= {1, 2}
    else:
        assert all(b.page is None for b in parsed.spans)
    chunks = Chunker().split(parsed, "version")
    assert chunks
    assert all(not ("Cells" in c.text and "Atoms" in c.text) for c in chunks)
    assert all(parsed.text[c.start_offset:c.end_offset] == c.text for c in chunks)


def test_ING_FMT_001_refuses_before_write(engine, processor, tmp_path):
    path = tmp_path / "bad.xlsx"
    path.write_bytes(b"not supported")
    with engine.connect() as conn:
        before = conn.execute(text("SELECT count(*) FROM document_version")).scalar_one()
    with pytest.raises(IngestionError, match="formato_nao_suportado"):
        Ingestor(engine, processor=processor).ingest(path, "refusal-owner")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM document_version")).scalar_one() == before


def test_ING_REC_001_reuse_without_parsing_and_owner_isolation(engine, parser, processor, monkeypatch):
    ingestor = Ingestor(engine, parser=parser, processor=processor)
    first = ingestor.ingest(FIXTURES / "study.md", "owner-a")
    other = ingestor.ingest(FIXTURES / "study.md", "owner-b")
    assert not first.reused and not other.reused
    assert other.version.document_version_id != first.version.document_version_id
    def forbidden(*args):
        pytest.fail("duplicate must not parse")
    monkeypatch.setattr(parser, "parse", forbidden)
    second = ingestor.ingest(FIXTURES / "study.md", "owner-a")
    assert second.reused and second.version == first.version
    assert second.chunks == first.chunks
    assert first.version.sha256 == sha256((FIXTURES / "study.md").read_bytes()).hexdigest()
    with engine.connect() as conn:
        repo = LineageRepository(conn)
        assert repo.get_document_version(first.version.document_version_id) == first.version
        assert repo.list_chunks(first.version.document_version_id) == first.chunks


def document(parts):
    return canonical_document([(value, section, page, kind, 'h1-'+section[0] if section else None)
                               for value, section, page, kind in parts], version='v1')


def test_ING_CHK_002_target_ceiling_and_paragraph_overlap():
    parsed = document([(f"Paragraph {i} " + "cell " * 15, ["Biology"], 1, "paragraph") for i in range(12)])
    chunker = Chunker(ChunkConfig(target_tokens=65, max_tokens=100))
    chunks = chunker.split(parsed, "v1")
    assert len(chunks) > 2
    assert all(count_tokens(c.text) <= 100 for c in chunks)
    for left, right in zip(chunks, chunks[1:]):
        assert left.end_offset > right.start_offset
        assert left.text[right.start_offset-left.start_offset:] == right.text[:left.end_offset-right.start_offset]
    assert ChunkConfig().target_tokens == 800
    assert ChunkConfig().max_tokens == 1500


def test_ING_CHK_001_determinism_sections_pages_and_config():
    parsed = document([("same text", ["A"], 1, "paragraph"), ("same text", ["B"], 2, "paragraph")])
    chunker = Chunker()
    first = chunker.split(parsed, "v1")
    assert first == chunker.split(parsed, "v1")
    assert len(first) == 2 and len({c.chunk_id for c in first}) == 2
    assert [c.page for c in first] == [1, 2]
    assert [c.section_path for c in first] == [["A"], ["B"]]
    other = canonical_document([('same text', ['A'], 1, 'paragraph', 'h1-A'),
                                ('same text', ['B'], 2, 'paragraph', 'h1-B')], version='v2')
    assert first[0].chunk_id != chunker.split(other, "v2")[0].chunk_id
    assert first[0].chunk_id != Chunker(ChunkConfig(target_tokens=20, max_tokens=40)).split(parsed, "v1")[0].chunk_id


def test_ING_CHK_002_large_paragraph_bounded_and_lossless():
    value = "genetic material " * 1000
    parsed = document([(value, ["A"], None, "paragraph")])
    chunks = Chunker(ChunkConfig(target_tokens=40, max_tokens=60)).split(parsed, "v1")
    assert len(chunks) > 1
    assert all(count_tokens(c.text) <= 60 for c in chunks)
    assert chunks[0].start_offset == 0 and chunks[-1].end_offset == len(value)
    assert all(left.end_offset >= right.start_offset for left, right in zip(chunks, chunks[1:]))
    assert all(value[c.start_offset:c.end_offset] == c.text for c in chunks)


def test_ING_CHK_002_table_atomic_or_explicit_failure():
    parsed = document([("| A | B |\n| --- | --- |\n| cell | DNA |", ["A"], None, "table")])
    assert len(Chunker().split(parsed, "v1")) == 1
    with pytest.raises(IngestionError, match="tabela_excede_teto"):
        Chunker(ChunkConfig(target_tokens=1, max_tokens=2)).split(parsed, "v1")


def test_ING_FMT_001_parser_failure_writes_nothing(engine, processor, tmp_path):
    path = tmp_path / "corrupt.docx"
    path.write_bytes(b"invalid zip")
    with pytest.raises(IngestionError):
        Ingestor(engine, processor=processor).ingest(path, "bad-docx")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM document_version WHERE owner_id='bad-docx'")).scalar_one() == 0


def test_ING_REC_001_concurrent_duplicate(engine, processor):
    def run(_):
        try:
            return Ingestor(engine, processor=processor).ingest(FIXTURES / "study.md", "concurrent-owner")
        except Exception as exc:
            from training_ingestion.protocol import IngestionBusy
            if isinstance(exc, IngestionBusy):
                return None
            raise
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    completed=[result for result in results if result is not None]
    assert completed
    assert len({result.version.document_version_id for result in completed})==1
    assert sorted(result.reused for result in completed) in ([False],[False,True])


@pytest.mark.parametrize("kwargs", [{"target_tokens": 0}, {"max_tokens": 0}, {"target_tokens": 20, "max_tokens": 10}])
def test_ING_CHK_002_invalid_config(kwargs):
    with pytest.raises(ValueError):
        ChunkConfig(**kwargs)


@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
def test_ING_FMT_001_ING_CHK_001_persist_real_formats(engine, parser, processor, extension):
    result = Ingestor(engine, parser=parser, processor=processor).ingest(FIXTURES / f"study.{extension}", f"persist-{extension}")
    assert result.chunks
    with engine.connect() as conn:
        repo = LineageRepository(conn)
        assert repo.get_document_version(result.version.document_version_id) == result.version
        assert repo.list_chunks(result.version.document_version_id) == result.chunks
    assert all(c.document_version_id == result.version.document_version_id for c in result.chunks)
    assert all(c.section_path for c in result.chunks)
    assert all((c.page is None) == (extension != "pdf") for c in result.chunks)


def test_ING_REC_001_limits_and_chunk_failure_are_atomic(engine, processor):
    path = FIXTURES / "study.md"
    for ingestor, reason in [
        (Ingestor(engine, processor=processor, max_file_bytes=10), "tamanho_excedido"),
        (Ingestor(engine, processor=processor, config=ChunkConfig(target_tokens=1, max_tokens=2)), "tabela_excede_teto"),
    ]:
        with pytest.raises(IngestionError, match=reason):
            ingestor.ingest(path, "atomic-owner")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM document_version WHERE owner_id='atomic-owner'")).scalar_one() == 0


def test_ING_CHK_002_two_large_paragraphs_keep_ceiling_and_overlap():
    parsed = document([("A " * 150, ["A"], None, "paragraph"), ("B " * 150, ["A"], None, "paragraph")])
    chunks = Chunker(ChunkConfig(target_tokens=70, max_tokens=100)).split(parsed, "v1")
    assert len(chunks) > 1
    assert all(count_tokens(c.text) <= 100 for c in chunks)
    assert all(left.end_offset <= right.end_offset and left.start_offset < right.start_offset
               for left, right in zip(chunks, chunks[1:]))
    assert any(left.end_offset > right.start_offset for left, right in zip(chunks, chunks[1:]))


def test_ING_CHK_001_changing_subsection_and_page_preserves_origins():
    parsed = document([("First paragraph", ["A", "One"], 1, "paragraph"),
                       ("Second paragraph", ["A", "Two"], 2, "paragraph")])
    chunks = Chunker().split(parsed, "v1")
    assert len(chunks) == 1
    chunks, maps = Chunker().split_with_provenance(parsed, 'v1')
    assert {s.section_path for s in parsed.spans if s.span_id in maps[0].span_ids} >= {('A','One'),('A','Two')}
    assert {s.page for s in parsed.spans if s.span_id in maps[0].span_ids} >= {1,2}


def test_ING_FMT_001_fixtures_are_byte_reproducible(tmp_path):
    import runpy
    import shutil
    paths = [FIXTURES / f"study.{ext}" for ext in ("pdf", "docx", "md")]
    before = [p.read_bytes() for p in paths]
    generator = tmp_path / "generate.py"
    shutil.copyfile(FIXTURES / "generate.py", generator)
    runpy.run_path(str(generator))
    assert [(tmp_path / p.name).read_bytes() for p in paths] == before
    assert [p.read_bytes() for p in paths] == before


def test_single_unit_not_overlapped():
    parsed = document([('parágrafo com muitas palavras ' * 3, ['A'], None, 'paragraph')] * 3)
    chunks = Chunker(ChunkConfig(target_tokens=5, max_tokens=100)).split(parsed, 'v1')
    assert len(chunks) == 3
    assert all(a.end_offset == b.start_offset for a,b in zip(chunks, chunks[1:]))


def test_fragments_respect_words():
    text = 'membranas protegem células. ' * 100
    parsed = document([(text, ['A'], None, 'paragraph')])
    chunks = Chunker(ChunkConfig(target_tokens=20, max_tokens=40)).split(parsed, 'v1')
    assert all(parsed.text[c.start_offset:c.end_offset] == c.text for c in chunks)
    assert all(c.text[-1].isspace() or text[c.end_offset:c.end_offset+1].isspace() for c in chunks[:-1])


def test_equivalent_section_paths(parser):
    for ext in ('md', 'docx', 'pdf'):
        parsed = parser.parse((FIXTURES / f'study.{ext}').read_bytes(), f'study.{ext}', "version")
        assert next(b for b in parsed.spans if parsed.text[b.start:b.end] == 'Details').section_path == ('Biology', 'Details')
        assert parsed.title == 'Biology'


def test_public_ingestor_requires_finalization():
    from training_ingestion import Ingestor as PublicIngestor
    with pytest.raises(ValueError, match='processor'):
        PublicIngestor(None)


def test_unbreakable_word_fragments_losslessly():
    parsed = document([('A'*300, ['A'], None, 'paragraph')])
    chunks, maps = Chunker(ChunkConfig(target_tokens=20, max_tokens=40)).split_with_provenance(parsed, 'v1')
    from test_ressalvas_parse import assert_reconstruction
    assert_reconstruction(parsed, chunks, maps)
    assert all(count_tokens(c.text) <= 40 for c in chunks)

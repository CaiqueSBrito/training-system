"""ING-LIN-001: integração real, em bancos temporários exclusivos dos testes."""

from datetime import datetime, timezone, timedelta
from uuid import uuid4

from training_lineage import LineageRepository, create_lineage_engine, database_url, migrate
from training_lineage.tables import chunk, document_version

import pytest
from sqlalchemy import create_engine, inspect, text, update
from sqlalchemy.exc import IntegrityError, DBAPIError
from pydantic import ValidationError
from training_contracts.linhagem import DocumentVersionRecord, ChunkRecord


@pytest.fixture
def empty_database():
    admin = create_lineage_engine()
    name = f"test_lineage_{uuid4().hex}"
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
    engine = create_engine(admin.url.set(database=name), hide_parameters=True)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def engine(empty_database):
    migrate(empty_database, "head")
    return empty_database


def version(**changes):
    return DocumentVersionRecord(**(dict(
        document_version_id="v1", document_id="doc1", owner_id="owner1",
        sha256="a" * 64, parser_version="docling-v1",
        created_at=datetime(2026, 11, 24, 10, tzinfo=timezone(timedelta(hours=-3))),
    ) | changes))


def record(**changes):
    return ChunkRecord(**(dict(
        chunk_id="c1", document_version_id="v1", chunker_version="chunker-v1",
        page=1, section_path=["Capítulo 1", "Introdução"], start_offset=0,
        end_offset=20, text="Um conteúdo de estudo.",
    ) | changes))


def test_ING_LIN_001_roundtrip_contracts(engine):
    document, original = version(), record(injection_flagged=True, injection_reason="instrução")
    with engine.begin() as connection:
        repo = LineageRepository(connection)
        repo.insert_document_version(document)
        repo.insert_chunk(original)
    with engine.connect() as connection:
        repo = LineageRepository(connection)
        assert repo.get_document_version("v1") == document
        assert repo.get_chunk("c1") == original
        assert DocumentVersionRecord.model_validate(repo.get_document_version("v1").model_dump()) == document
        assert ChunkRecord.model_validate(repo.get_chunk("c1").model_dump()) == original
        assert repo.find_document_version("owner1", "a" * 64) == document
        assert repo.find_document_version("other-owner", "a" * 64) is None
        assert repo.get_chunk("missing") is None
        assert repo.get_document_version("missing") is None


def test_ING_LIN_001_nullable_page_and_empty_sections(engine):
    with engine.begin() as connection:
        repo = LineageRepository(connection)
        repo.insert_document_version(version())
        original = record(page=None, section_path=[])
        repo.insert_chunk(original)
        assert repo.get_chunk(original.chunk_id) == original


def test_ING_LIN_001_foreign_key_rejects_unknown_version(engine):
    with pytest.raises(IntegrityError), engine.begin() as connection:
        LineageRepository(connection).insert_chunk(record(document_version_id="missing"))


def test_ING_LIN_001_chunks_belong_to_their_version(engine):
    with engine.begin() as connection:
        repo = LineageRepository(connection)
        repo.insert_document_version(version())
        repo.insert_document_version(version(document_version_id="v2", sha256="b" * 64))
        repo.insert_chunk(record())
        repo.insert_chunk(record(chunk_id="c2", document_version_id="v2"))
        assert [c.chunk_id for c in repo.list_chunks("v1")] == ["c1"]
        assert [c.chunk_id for c in repo.list_chunks("v2")] == ["c2"]
        assert repo.list_chunks("missing") == []


@pytest.mark.parametrize("statement", [
    "UPDATE chunk SET text = 'alterado' WHERE chunk_id = 'c1'",
    "UPDATE chunk SET start_offset = start_offset WHERE chunk_id = 'c1'",
    "DELETE FROM chunk WHERE chunk_id = 'c1'",
    "TRUNCATE chunk",
    "TRUNCATE chunk CASCADE",
])
def test_ING_LIN_001_chunk_immutable_in_database(engine, statement):
    with engine.begin() as connection:
        repo = LineageRepository(connection)
        repo.insert_document_version(version())
        repo.insert_chunk(record())
        assert connection.execute(text("""
            SELECT count(*) FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
            WHERE t.tgrelid = 'chunk'::regclass
              AND t.tgname = 'chunk_immutable_truncate'
              AND t.tgenabled = 'O' AND p.proname = 'reject_chunk_mutation'
        """)).scalar_one() == 1
    with pytest.raises(DBAPIError) as caught, engine.begin() as connection:
        connection.execute(text(statement))
    expected_code = "0A000" if statement == "TRUNCATE chunk" else "23514"
    assert caught.value.orig.sqlstate == expected_code
    with engine.connect() as connection:
        assert LineageRepository(connection).get_chunk("c1") == record()


def test_ING_LIN_001_reading_order_is_not_insertion_or_section_name_order(engine):
    chunks = [
        record(chunk_id="z", page=1, start_offset=0, section_path=["Z"]),
        record(chunk_id="b", page=1, start_offset=10, section_path=["A"]),
        record(chunk_id="a", page=1, start_offset=10, section_path=["A"]),
        record(chunk_id="p2", page=2, start_offset=0),
        record(chunk_id="no-page", page=None, start_offset=0),
    ]
    with engine.begin() as connection:
        repo = LineageRepository(connection)
        repo.insert_document_version(version())
        for item in reversed(chunks):
            repo.insert_chunk(item)
        assert [c.chunk_id for c in repo.list_chunks("v1")] == ["z", "a", "b", "p2", "no-page"]


def test_ING_LIN_001_unpaged_reading_order_by_offset(engine):
    with engine.begin() as connection:
        repo = LineageRepository(connection)
        repo.insert_document_version(version())
        for offset in (20, 0, 10):
            repo.insert_chunk(record(chunk_id=f"c{offset}", page=None, start_offset=offset, end_offset=offset + 10))
        assert [c.start_offset for c in repo.list_chunks("v1")] == [0, 10, 20]


def test_ING_LIN_001_duplicate_ids_and_owner_hash_rejected(engine):
    with engine.begin() as connection:
        LineageRepository(connection).insert_document_version(version())
    with pytest.raises(IntegrityError), engine.begin() as connection:
        LineageRepository(connection).insert_document_version(version())
    with pytest.raises(IntegrityError), engine.begin() as connection:
        LineageRepository(connection).insert_document_version(version(document_version_id="v2"))
    with engine.begin() as connection:
        LineageRepository(connection).insert_document_version(version(document_version_id="v3", owner_id="other"))
        LineageRepository(connection).insert_chunk(record())
    with pytest.raises(IntegrityError), engine.begin() as connection:
        LineageRepository(connection).insert_chunk(record())


def test_ING_LIN_001_transaction_rolls_back_partial_ingestion(engine):
    with pytest.raises(IntegrityError), engine.begin() as connection:
        repo = LineageRepository(connection)
        repo.insert_document_version(version())
        repo.insert_chunk(record(document_version_id="missing"))
    with engine.connect() as connection:
        assert LineageRepository(connection).get_document_version("v1") is None


def test_ING_LIN_001_revalidate_records_before_writing(engine):
    with engine.begin() as connection:
        repo = LineageRepository(connection)
        with pytest.raises(ValidationError):
            repo.insert_document_version(version().model_copy(update={"sha256": "invalid"}))
        repo.insert_document_version(version())
        with pytest.raises(ValidationError):
            repo.insert_chunk(record().model_copy(update={"end_offset": 0}))


@pytest.mark.parametrize("changes", [
    {"end_offset": 0}, {"page": 0}, {"text": "   "},
    {"injection_flagged": True}, {"injection_reason": "reason"},
])
def test_ING_LIN_001_database_constraints(engine, changes):
    with engine.begin() as connection:
        LineageRepository(connection).insert_document_version(version())
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(chunk.insert().values(**(record().model_dump() | changes)))


def test_ING_LIN_001_migration_upgrade_downgrade_empty_database(empty_database):
    assert inspect(empty_database).get_table_names() == []
    migrate(empty_database, "head")
    assert {"document_version", "chunk", "alembic_version", "canonical_text", "canonical_span",
            "chunk_provenance", "embedding_input", "ingestion_audit_event",
            "effective_chunk_decision", "ingestion_reservation", "reconciliation_job",
            "index_ack"} == set(inspect(empty_database).get_table_names())
    with empty_database.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0003"
    migrate(empty_database, "base")
    assert set(inspect(empty_database).get_table_names()) <= {"alembic_version"}
    with empty_database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM pg_proc WHERE proname = 'reject_chunk_mutation'")).scalar_one() == 0
    migrate(empty_database, "head")
    assert "chunk" in inspect(empty_database).get_table_names()


def test_ING_LIN_001_configuration_from_env_file_without_logging_secrets(tmp_path, monkeypatch):
    for key in ("HOST", "PORT", "DB", "USER", "PASSWORD"):
        monkeypatch.delenv(f"APP_POSTGRES_{key}", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('APP_POSTGRES_DB=lineage\nAPP_POSTGRES_USER=app\nAPP_POSTGRES_PASSWORD="p@ss:# word"\nAPP_POSTGRES_PORT=5439\n')
    url = database_url(env_file)
    assert url.drivername == "postgresql+psycopg"
    assert url.password == "p@ss:# word"
    assert url.host == "127.0.0.1"
    assert url.port == 5439
    assert "p@ss" not in str(url)
    monkeypatch.setenv("APP_POSTGRES_PORT", "5440")
    assert database_url(env_file).port == 5440
    env_file.write_text('APP_POSTGRES_DB=lineage\n')
    with pytest.raises(ValueError, match="APP_POSTGRES_USER"):
        database_url(env_file)


def test_ING_LIN_001_naive_timestamp_normalized_to_utc(engine):
    original = version(created_at=datetime(2026, 11, 24, 10))
    with engine.begin() as connection:
        repo = LineageRepository(connection)
        repo.insert_document_version(original)
        assert repo.get_document_version("v1").created_at == original.created_at.replace(tzinfo=timezone.utc)


@pytest.mark.parametrize('statement', [
    "UPDATE document_version SET sha256 = repeat('b',64)",
    "UPDATE document_version SET owner_id = 'other'",
    "UPDATE document_version SET parser_version = 'other'",
    "DELETE FROM document_version", "TRUNCATE document_version CASCADE",
])
def test_document_version_immutable(engine, statement):
    with engine.begin() as c:
        LineageRepository(c).insert_document_version(version())
    with pytest.raises(DBAPIError, match='document_version is immutable'), engine.begin() as c:
        c.execute(text(statement))


def test_migrated_schema_matches_metadata(engine):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from training_lineage.tables import metadata
    with engine.connect() as c:
        assert compare_metadata(MigrationContext.configure(c), metadata) == []


def test_ING_LIN_001_intermediate_roundtrip_and_trigger_lifecycle(empty_database):
    migrate(empty_database, '0001')  # Já funcionava; a lacuna era descer até 0001.
    with empty_database.begin() as c:
        repo = LineageRepository(c)
        repo.insert_document_version(version())
        repo.insert_chunk(record())
    migrate(empty_database, '0002')
    for table, key in [('document_version', 'document_version_id'), ('chunk', 'chunk_id')]:
        for statement in (f'UPDATE {table} SET {key} = {key}', f'DELETE FROM {table}',
                          f'TRUNCATE {table} CASCADE'):
            with pytest.raises(DBAPIError, match=f'{table} is immutable'), empty_database.begin() as c:
                c.execute(text(statement))
    migrate(empty_database, '0001')
    with empty_database.begin() as c:
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0001'
        assert c.execute(text("SELECT count(*) FROM pg_proc WHERE proname = 'reject_document_version_mutation'")).scalar_one() == 0
        assert c.execute(text("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'document_version_immutable_%'")).scalar_one() == 0
        assert c.execute(text("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'chunk_immutable_%'")).scalar_one() == 2
        c.execute(text("UPDATE document_version SET parser_version = 'roundtrip'"))
        assert LineageRepository(c).get_chunk('c1') == record()
    with pytest.raises(DBAPIError, match='chunk is immutable'), empty_database.begin() as c:
        c.execute(text('DELETE FROM chunk'))
    migrate(empty_database, 'head')
    migrate(empty_database, '0003')  # Mesmo alvo: nenhuma mutação.
    with empty_database.connect() as c:
        assert LineageRepository(c).get_document_version('v1').parser_version == 'roundtrip'
    migrate(empty_database, 'base')
    with empty_database.connect() as c:
        assert c.execute(text("SELECT count(*) FROM pg_proc WHERE proname IN ('reject_chunk_mutation', 'reject_document_version_mutation', 't011_immutable')")).scalar_one() == 0
        assert c.execute(text("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'chunk_immutable_%' OR tgname LIKE 'document_version_immutable_%'")).scalar_one() == 0


@pytest.mark.parametrize('target', ['unknown', '000', '-1', '+1', '0001:0002', 'head;DROP TABLE chunk', '', None])
def test_ING_LIN_001_invalid_target_preserves_data(engine, target):
    with engine.begin() as c:
        LineageRepository(c).insert_document_version(version())
        LineageRepository(c).insert_chunk(record())
    with pytest.raises(ValueError, match='Revisão de migração inválida'):
        migrate(engine, target)
    with engine.connect() as c:
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0003'
        assert LineageRepository(c).get_document_version('v1') == version()
        assert LineageRepository(c).get_chunk('c1') == record()


@pytest.mark.parametrize('table_name,changes,constraint', [
    ('document_version', {'document_version_id': ''}, 'document_version_identifiers'),
    ('document_version', {'document_id': ''}, 'document_version_identifiers'),
    ('document_version', {'owner_id': ''}, 'document_version_identifiers'),
    ('document_version', {'parser_version': ''}, 'document_version_identifiers'),
    ('document_version', {'sha256': 'INVALID'}, 'document_version_sha256'),
    ('chunk', {'chunk_id': ''}, 'chunk_identifiers'),
    ('chunk', {'chunker_version': ''}, 'chunk_identifiers'),
    ('chunk', {'page': 0}, 'chunk_page'),
    ('chunk', {'start_offset': -1}, 'chunk_offsets'),
    ('chunk', {'end_offset': 0}, 'chunk_offsets'),
    ('chunk', {'text': ' \n'}, 'chunk_text'),
    ('chunk', {'injection_flagged': True, 'injection_reason': ' '}, 'chunk_injection'),
    ('chunk', {'injection_flagged': False, 'injection_reason': 'motivo'}, 'chunk_injection'),
    ('chunk', {'section_path': [None]}, 'chunk_section_path'),
])
def test_ING_LIN_001_each_migrated_check(engine, table_name, changes, constraint):
    if table_name == 'chunk':
        with engine.begin() as c:
            LineageRepository(c).insert_document_version(version())
    table = document_version if table_name == 'document_version' else chunk
    values = version().model_dump() if table_name == 'document_version' else record().model_dump()
    with pytest.raises(IntegrityError) as caught, engine.begin() as c:
        c.execute(table.insert().values(**(values | changes)))
    assert caught.value.orig.diag.constraint_name == constraint

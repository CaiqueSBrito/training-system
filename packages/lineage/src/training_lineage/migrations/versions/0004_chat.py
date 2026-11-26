"""E12: snapshot aditivo; migrações 0001–0003 permanecem intactas."""
from alembic import op
revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

DDL = [
r"""
CREATE TABLE chat_turn (
	chat_turn_id TEXT NOT NULL, 
	owner_id TEXT NOT NULL, 
	session_id TEXT NOT NULL, 
	trace_id TEXT NOT NULL, 
	record JSONB NOT NULL, 
	response JSONB NOT NULL, 
	PRIMARY KEY (chat_turn_id), 
	CONSTRAINT chat_turn_owner_session UNIQUE (chat_turn_id, owner_id, session_id), 
	CONSTRAINT chat_turn_owner UNIQUE (chat_turn_id, owner_id), 
	CONSTRAINT chat_turn_ids CHECK (chat_turn_id ~ '[^[:space:]]' AND owner_id ~ '[^[:space:]]' AND session_id ~ '[^[:space:]]' AND trace_id ~ '[^[:space:]]'), 
	CONSTRAINT chat_turn_envelope CHECK (record->>'chat_turn_id'=chat_turn_id AND record->>'user_id'=owner_id AND record->>'session_id'=session_id AND record->>'trace_id'=trace_id AND response->>'chat_turn_id'=chat_turn_id AND response->>'trace_id'=trace_id AND record->>'status'=response->>'status'), 
	CONSTRAINT chat_turn_status CHECK (record->>'status' IN ('ok','recusa','fora_de_escopo','conteudo_insuficiente','erro')), 
	CONSTRAINT chat_turn_e1 CHECK (COALESCE(record->>'motivo_recusa','') NOT IN ('tamanho_excedido','limite_de_taxa') OR ((record->>'intencao') IS NULL AND (record->>'modo') IS NULL AND (record->>'input_verdict') IS NULL)), 
	CONSTRAINT chat_turn_record_keys CHECK (jsonb_typeof(record)='object' AND record ? 'chat_turn_id' AND record->>'chat_turn_id' IS NOT NULL AND record ? 'user_id' AND record->>'user_id' IS NOT NULL AND record ? 'session_id' AND record->>'session_id' IS NOT NULL AND record ? 'trace_id' AND record->>'trace_id' IS NOT NULL AND record ? 'status' AND record->>'status' IS NOT NULL AND record ? 'created_at' AND record->>'created_at' IS NOT NULL), 
	CONSTRAINT chat_response_keys CHECK (response ? 'chat_turn_id' AND response->>'chat_turn_id' IS NOT NULL AND response ? 'trace_id' AND response->>'trace_id' IS NOT NULL AND response ? 'status' AND response->>'status' IS NOT NULL)
)

""",
r"""
CREATE TABLE chat_plan (
	search_plan_id TEXT NOT NULL, 
	chat_turn_id TEXT NOT NULL, 
	record JSONB NOT NULL, 
	PRIMARY KEY (search_plan_id), 
	CONSTRAINT chat_plan_ids CHECK (record->>'search_plan_id'=search_plan_id AND record->>'chat_turn_id'=chat_turn_id), 
	UNIQUE (chat_turn_id), 
	FOREIGN KEY(chat_turn_id) REFERENCES chat_turn (chat_turn_id), 
	CONSTRAINT chat_plan_record_keys CHECK (jsonb_typeof(record)='object' AND record ? 'search_plan_id' AND record->>'search_plan_id' IS NOT NULL AND record ? 'chat_turn_id' AND record->>'chat_turn_id' IS NOT NULL AND record ? 'objetivos' AND record->>'objetivos' IS NOT NULL AND record ? 'bloom_level' AND record->>'bloom_level' IS NOT NULL AND record ? 'consultas' AND record->>'consultas' IS NOT NULL)
)

""",
r"""
CREATE TABLE chat_retrieval (
	retrieval_run_id TEXT NOT NULL, 
	chat_turn_id TEXT NOT NULL, 
	record JSONB NOT NULL, 
	PRIMARY KEY (retrieval_run_id), 
	CONSTRAINT chat_retrieval_ids CHECK (record->>'retrieval_run_id'=retrieval_run_id AND record->>'chat_turn_id'=chat_turn_id), 
	UNIQUE (chat_turn_id), 
	FOREIGN KEY(chat_turn_id) REFERENCES chat_turn (chat_turn_id), 
	CONSTRAINT chat_retrieval_record_keys CHECK (jsonb_typeof(record)='object' AND record ? 'retrieval_run_id' AND record->>'retrieval_run_id' IS NOT NULL AND record ? 'chat_turn_id' AND record->>'chat_turn_id' IS NOT NULL AND record ? 'modo' AND record->>'modo' IS NOT NULL AND record ? 'candidates' AND record->>'candidates' IS NOT NULL)
)

""",
r"""
CREATE TABLE chat_candidate (
	chat_turn_id TEXT NOT NULL, 
	chunk_id TEXT NOT NULL, 
	owner_id TEXT NOT NULL, 
	selected BOOLEAN NOT NULL, 
	PRIMARY KEY (chat_turn_id, chunk_id), 
	FOREIGN KEY(chat_turn_id, owner_id) REFERENCES chat_turn (chat_turn_id, owner_id), 
	FOREIGN KEY(chat_turn_id) REFERENCES chat_retrieval (chat_turn_id), 
	FOREIGN KEY(chunk_id) REFERENCES chunk (chunk_id)
)

""",
r"""
CREATE TABLE chat_item (
	item_id TEXT NOT NULL, 
	chat_turn_id TEXT NOT NULL, 
	record JSONB NOT NULL, 
	PRIMARY KEY (item_id), 
	CONSTRAINT chat_item_turn UNIQUE (item_id, chat_turn_id), 
	CONSTRAINT chat_item_ids CHECK (record->>'item_id'=item_id AND record->>'chat_turn_id'=chat_turn_id), 
	FOREIGN KEY(chat_turn_id) REFERENCES chat_turn (chat_turn_id), 
	CONSTRAINT chat_item_record_keys CHECK (jsonb_typeof(record)='object' AND record ? 'item_id' AND record->>'item_id' IS NOT NULL AND record ? 'chat_turn_id' AND record->>'chat_turn_id' IS NOT NULL AND record ? 'raw_content' AND record->>'raw_content' IS NOT NULL AND record ? 'status' AND record->>'status' IS NOT NULL), 
	CONSTRAINT chat_item_status CHECK (record->>'status' IN ('entregue','omitido') AND ((record->>'status'='entregue')=(record->>'motivo' IS NULL)) AND (record->>'status'<>'entregue' OR record->>'content' IS NOT NULL))
)

""",
r"""
CREATE TABLE chat_citation (
	citation_id TEXT NOT NULL, 
	item_id TEXT NOT NULL, 
	chat_turn_id TEXT NOT NULL, 
	owner_id TEXT NOT NULL, 
	chunk_id TEXT NOT NULL, 
	record JSONB NOT NULL, 
	selection JSONB NOT NULL, 
	spans JSONB NOT NULL, 
	PRIMARY KEY (citation_id), 
	FOREIGN KEY(item_id, chat_turn_id) REFERENCES chat_item (item_id, chat_turn_id), 
	FOREIGN KEY(chat_turn_id, owner_id) REFERENCES chat_turn (chat_turn_id, owner_id), 
	FOREIGN KEY(chat_turn_id, chunk_id) REFERENCES chat_candidate (chat_turn_id, chunk_id), 
	CONSTRAINT chat_citation_ids CHECK (record->>'citation_id'=citation_id AND record->>'item_id'=item_id AND record->>'chunk_id'=chunk_id AND selection->>'chunk_id'=chunk_id AND selection->>'evidence'=record->>'evidence'), 
	CONSTRAINT chat_citation_spans CHECK (jsonb_typeof(spans)='array' AND jsonb_array_length(spans)>0), 
	FOREIGN KEY(chunk_id) REFERENCES chunk (chunk_id), 
	CONSTRAINT chat_citation_record_keys CHECK (jsonb_typeof(record)='object' AND record ? 'citation_id' AND record->>'citation_id' IS NOT NULL AND record ? 'item_id' AND record->>'item_id' IS NOT NULL AND record ? 'chunk_id' AND record->>'chunk_id' IS NOT NULL AND record ? 'chunk_ref' AND record->>'chunk_ref' IS NOT NULL AND record ? 'evidence' AND record->>'evidence' IS NOT NULL)
)

""",
r"""
CREATE TABLE chat_verdict (
	verdict_id TEXT NOT NULL, 
	item_id TEXT NOT NULL, 
	record JSONB NOT NULL, 
	PRIMARY KEY (verdict_id), 
	CONSTRAINT chat_verdict_ids CHECK (record->>'verdict_id'=verdict_id AND record->>'item_id'=item_id), 
	FOREIGN KEY(item_id) REFERENCES chat_item (item_id), 
	CONSTRAINT chat_verdict_record_keys CHECK (jsonb_typeof(record)='object' AND record ? 'verdict_id' AND record->>'verdict_id' IS NOT NULL AND record ? 'item_id' AND record->>'item_id' IS NOT NULL AND record ? 'origem' AND record->>'origem' IS NOT NULL AND record ? 'tentativa' AND record->>'tentativa' IS NOT NULL AND record ? 'aprovado' AND record->>'aprovado' IS NOT NULL AND record ? 'created_at' AND record->>'created_at' IS NOT NULL), 
	CONSTRAINT chat_verdict_status CHECK (record->>'origem' IN ('E9','E10') AND record->>'tentativa' IN ('1','2') AND record->>'aprovado' IN ('true','false') AND ((record->>'aprovado'='true')=(record->>'motivo' IS NULL)))
)

""",
r"""
CREATE TABLE chat_continuation (
	owner_id TEXT NOT NULL, 
	session_id TEXT NOT NULL, 
	chat_turn_id TEXT NOT NULL, 
	state JSONB NOT NULL, 
	PRIMARY KEY (owner_id, session_id), 
	FOREIGN KEY(chat_turn_id, owner_id, session_id) REFERENCES chat_turn (chat_turn_id, owner_id, session_id)
)

""",
r"""
CREATE TABLE chat_rate_event (
	event_id TEXT NOT NULL, 
	owner_id TEXT NOT NULL, 
	acquired_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (event_id), 
	CONSTRAINT chat_rate_owner CHECK (owner_id ~ '[^[:space:]]')
)

""",
r"""CREATE INDEX chat_rate_owner_time ON chat_rate_event (owner_id, acquired_at)""",
]

HISTORY = ('chat_turn','chat_plan','chat_retrieval','chat_candidate','chat_item','chat_citation','chat_verdict')
ALL_TABLES = HISTORY + ('chat_continuation','chat_rate_event')


def upgrade():
    for statement in DDL:
        op.execute(statement)
    op.execute("""CREATE FUNCTION chat_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'história de chat imutável' USING ERRCODE='23514'; END; $$""")
    for table in HISTORY:
        op.execute(f'CREATE TRIGGER {table}_immutable_rows BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION chat_reject_mutation()')
        op.execute(f'CREATE TRIGGER {table}_immutable_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION chat_reject_mutation()')
    op.execute("""CREATE FUNCTION chat_check_owner() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM chunk c JOIN document_version d USING (document_version_id)
              WHERE c.chunk_id=NEW.chunk_id AND d.owner_id=NEW.owner_id) THEN
            RAISE EXCEPTION 'chunk de outro owner' USING ERRCODE='23503';
          END IF;
          RETURN NEW;
        END; $$""")
    for table in ('chat_candidate','chat_citation'):
        op.execute(f'CREATE TRIGGER {table}_owned BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION chat_check_owner()')
    op.execute("""CREATE FUNCTION chat_check_citation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM chunk_provenance p
             JOIN chat_candidate cc ON cc.chunk_id=p.chunk_id
             WHERE p.chunk_id=NEW.chunk_id AND p.mapping_status='exact'
             AND cc.chat_turn_id=NEW.chat_turn_id AND cc.selected
             AND p.canonical_id=NEW.selection->>'canonical_id'
             AND (NEW.selection->>'start')::bigint >= p.start
             AND (NEW.selection->>'end')::bigint <= p.end
             AND (NEW.selection->>'start')::bigint < (NEW.selection->>'end')::bigint) THEN
            RAISE EXCEPTION 'seleção canônica inválida' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END; $$""")
    op.execute('CREATE TRIGGER chat_citation_canonical BEFORE INSERT ON chat_citation FOR EACH ROW EXECUTE FUNCTION chat_check_citation()')


def downgrade():
    # Antes de qualquer DROP: nenhum histórico ou limite durável é descartado.
    for table in ALL_TABLES:
        op.execute(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM {table}) THEN RAISE EXCEPTION 'história de chat existente impede downgrade' USING ERRCODE='23514'; END IF; END $$")
    for table in reversed(ALL_TABLES):
        op.execute(f'DROP TABLE {table}')
    op.execute('DROP FUNCTION chat_check_citation()')
    op.execute('DROP FUNCTION chat_check_owner()')
    op.execute('DROP FUNCTION chat_reject_mutation()')

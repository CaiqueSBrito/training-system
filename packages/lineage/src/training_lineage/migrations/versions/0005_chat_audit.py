"""E1/E8 auditoria aditiva do chat (T-021, correção da revisão Anthropic).

- chat_input_audit: tamanho e sha256 do prompt bruto; o turno guarda só um prefixo
  limitado quando E1 recusa por tamanho (S2 da revisão de segurança).
- chat_generation_call: cada chamada E8 (inicial/reparo, concluída/falha, categoria da
  falha, chamada falha reparada) com resumo do contexto exato (ref, chunk_id, sha256 e
  tamanho do texto entregue), sem duplicar o texto das fontes.
- chat_generation_attempt: bruto de cada tentativa de item, ligado à chamada concluída
  que o produziu; attempt=2 se e só se a chamada é de reparo (contrato de captura T-022).
Migrações 0001–0004 permanecem intactas. Gerado de tables.py (compare_metadata).
"""
from alembic import op
revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

DDL = [
r"""
CREATE TABLE chat_input_audit (
	chat_turn_id TEXT NOT NULL, 
	raw_length BIGINT NOT NULL, 
	raw_sha256 TEXT NOT NULL, 
	stored_length BIGINT NOT NULL, 
	PRIMARY KEY (chat_turn_id), 
	CONSTRAINT chat_input_audit_values CHECK (raw_length >= 0 AND stored_length >= 0 AND stored_length <= raw_length AND raw_sha256 ~ '^[0-9a-f]{64}$'), 
	FOREIGN KEY(chat_turn_id) REFERENCES chat_turn (chat_turn_id)
)
""",
r"""
CREATE TABLE chat_generation_call (
	call_id TEXT NOT NULL, 
	chat_turn_id TEXT NOT NULL, 
	kind TEXT NOT NULL, 
	status TEXT NOT NULL, 
	failure_kind TEXT, 
	repairs_call_id TEXT, 
	context JSONB NOT NULL, 
	PRIMARY KEY (call_id), 
	CONSTRAINT chat_generation_call_values CHECK (call_id ~ '[^[:space:]]' AND kind IN ('inicial','reparo') AND status IN ('completed','failed') AND ((status='failed')=(failure_kind IS NOT NULL)) AND (kind='reparo' OR repairs_call_id IS NULL) AND jsonb_typeof(context)='object' AND jsonb_typeof(context->'chunks')='array'), 
	FOREIGN KEY(chat_turn_id) REFERENCES chat_turn (chat_turn_id), 
	FOREIGN KEY(repairs_call_id) REFERENCES chat_generation_call (call_id)
)
""",
r"""
CREATE TABLE chat_generation_attempt (
	item_id TEXT NOT NULL, 
	attempt BIGINT NOT NULL, 
	chat_turn_id TEXT NOT NULL, 
	call_id TEXT NOT NULL, 
	generation_item_index BIGINT NOT NULL, 
	raw_content JSONB NOT NULL, 
	PRIMARY KEY (item_id, attempt), 
	FOREIGN KEY(item_id, chat_turn_id) REFERENCES chat_item (item_id, chat_turn_id), 
	CONSTRAINT chat_generation_attempt_call_index UNIQUE (call_id, generation_item_index), 
	CONSTRAINT chat_generation_attempt_values CHECK (attempt IN (1, 2) AND generation_item_index >= 0 AND jsonb_typeof(raw_content)='object'), 
	FOREIGN KEY(call_id) REFERENCES chat_generation_call (call_id)
)
""",
]

TABLES = ('chat_input_audit', 'chat_generation_call', 'chat_generation_attempt')


def upgrade():
    for statement in DDL:
        op.execute(statement)
    for table in TABLES:
        # Função de 0004: histórico de chat é imutável.
        op.execute(f'CREATE TRIGGER {table}_immutable_rows BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION chat_reject_mutation()')
        op.execute(f'CREATE TRIGGER {table}_immutable_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION chat_reject_mutation()')


def downgrade():
    for table in TABLES:
        op.execute(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM {table}) THEN RAISE EXCEPTION 'história de chat existente impede downgrade' USING ERRCODE='23514'; END IF; END $$")
    for table in reversed(TABLES):
        op.execute(f'DROP TABLE {table}')

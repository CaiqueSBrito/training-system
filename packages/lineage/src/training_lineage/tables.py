"""Esquema relacional SQLAlchemy 2 da ingestão (ING-LIN-001)."""

from sqlalchemy import (
    MetaData, Table, Column, Text, DateTime, BigInteger, Boolean, CheckConstraint,
    ForeignKey, ForeignKeyConstraint, UniqueConstraint, Index, String, Uuid, func, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

metadata = MetaData()

# ADR-0017: autenticação compartilha o banco, não o pool nem as transações.
app_user = Table(
    'app_user', metadata,
    Column('id', Uuid, primary_key=True),
    Column('email', String(320), nullable=False),
    Column('hashed_password', String(1024), nullable=False),
    Column('is_active', Boolean, nullable=False, server_default=text('true')),
    Column('is_superuser', Boolean, nullable=False, server_default=text('false')),
    Column('is_verified', Boolean, nullable=False, server_default=text('false')),
    Column('created_at', DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("hashed_password LIKE '$argon2id$%'", name='app_user_argon2id'),
)
Index('app_user_email_lower_unique', func.lower(app_user.c.email), unique=True)
auth_access_token = Table(
    'auth_access_token', metadata,
    Column('token', String(64), primary_key=True),
    Column('user_id', Uuid, ForeignKey('app_user.id', ondelete='CASCADE'), nullable=False),
    Column('csrf_token', String(64), nullable=False, unique=True),
    Column('created_at', DateTime(timezone=True), nullable=False, server_default=func.now()),
)
auth_access_token.append_column(Column('last_seen_at', DateTime(timezone=True), nullable=False, server_default=func.now()))
Index('auth_access_token_created_at', auth_access_token.c.created_at)
auth_attempt = Table(
    'auth_attempt', metadata,
    Column('id', BigInteger, primary_key=True),
    Column('kind', Text, nullable=False),
    Column('scope', Text, nullable=False),
    Column('key', Text, nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("kind IN ('login_failure','register')", name='auth_attempt_kind'),
    CheckConstraint("scope IN ('account','origin')", name='auth_attempt_scope'),
    CheckConstraint("key ~ '^[0-9a-f]{64}$'", name='auth_attempt_key_sha256'),
)
Index('auth_attempt_window', auth_attempt.c.kind, auth_attempt.c.scope,
      auth_attempt.c.key, auth_attempt.c.created_at)

document_version = Table(
    "document_version", metadata,
    Column("document_version_id", Text, primary_key=True),
    Column("document_id", Text, nullable=False),
    Column("owner_id", Text, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("parser_version", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("length(document_version_id) > 0 AND length(document_id) > 0 AND length(owner_id) > 0 AND length(parser_version) > 0", name="document_version_identifiers"),
    CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="document_version_sha256"),
    UniqueConstraint("owner_id", "sha256", name="document_version_owner_sha256"),
)

chunk = Table(
    "chunk", metadata,
    Column("chunk_id", Text, primary_key=True),
    Column("document_version_id", Text, ForeignKey("document_version.document_version_id", ondelete="RESTRICT"), nullable=False),
    Column("chunker_version", Text, nullable=False),
    Column("page", BigInteger),
    Column("section_path", ARRAY(Text), nullable=False),
    Column("start_offset", BigInteger, nullable=False),
    Column("end_offset", BigInteger, nullable=False),
    Column("text", Text, nullable=False),
    Column("injection_flagged", Boolean, nullable=False),
    Column("injection_reason", Text),
    CheckConstraint("length(chunk_id) > 0 AND length(chunker_version) > 0", name="chunk_identifiers"),
    CheckConstraint("page IS NULL OR page >= 1", name="chunk_page"),
    CheckConstraint("start_offset >= 0 AND end_offset > start_offset", name="chunk_offsets"),
    CheckConstraint("text ~ '[^[:space:]]'", name="chunk_text"),
    CheckConstraint("injection_flagged = (injection_reason IS NOT NULL) AND (injection_reason IS NULL OR injection_reason ~ '[^[:space:]]')", name="chunk_injection"),
    CheckConstraint("array_position(section_path, NULL) IS NULL", name="chunk_section_path"),
)
Index("chunk_version_reading_order", chunk.c.document_version_id, chunk.c.page, chunk.c.start_offset, chunk.c.end_offset, chunk.c.chunk_id)

# ADR-0015 / T-011. Sem defaults/backfill sobre a história anterior.
canonical_text = Table('canonical_text', metadata,
    Column('canonical_id', Text, primary_key=True),
    Column('document_version_id', Text, ForeignKey('document_version.document_version_id'), nullable=False),
    Column('canonical_version', Text, nullable=False), Column('parser_version', Text, nullable=False),
    Column('text', Text, nullable=False), Column('text_sha256', Text, nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False),
    UniqueConstraint('document_version_id', 'canonical_version', name='canonical_recipe_unique'),
    CheckConstraint("canonical_id ~ '[^[:space:]]' AND canonical_version ~ '[^[:space:]]' AND parser_version ~ '[^[:space:]]' AND length(text)>0", name='canonical_identifiers'),
    CheckConstraint("text_sha256 = encode(sha256(convert_to(text,'UTF8')),'hex')", name='canonical_digest'),
)
canonical_span = Table('canonical_span', metadata,
    Column('span_id', Text, primary_key=True),
    Column('canonical_id', Text, ForeignKey('canonical_text.canonical_id'), nullable=False),
    Column('start', BigInteger, nullable=False), Column('end', BigInteger, nullable=False),
    Column('page', BigInteger), Column('section_path', ARRAY(Text), nullable=False), Column('h1_id', Text),
    Column('kind', Text, nullable=False), Column('provenance', Text, nullable=False),
    UniqueConstraint('canonical_id', 'start', name='canonical_span_start_unique'),
    CheckConstraint('start >= 0 AND "end" > start', name='canonical_span_range'),
    CheckConstraint("kind IN ('heading','paragraph','table','separator') AND provenance IN ('exact','unknown')", name='canonical_span_enums'),
    CheckConstraint("page IS NULL OR (page >= 1 AND provenance='exact' AND kind<>'separator')", name='canonical_span_page'),
    CheckConstraint("span_id ~ '[^[:space:]]' AND (h1_id IS NULL OR h1_id ~ '[^[:space:]]') AND array_position(section_path,NULL) IS NULL", name='canonical_span_ids'),
)
chunk_provenance = Table('chunk_provenance', metadata,
    Column('chunk_id', Text, ForeignKey('chunk.chunk_id'), primary_key=True),
    Column('canonical_id', Text, ForeignKey('canonical_text.canonical_id')),
    Column('start', BigInteger), Column('end', BigInteger),
    Column('span_ids', ARRAY(Text), nullable=False), Column('overlap_ranges', JSONB, nullable=False),
    Column('mapping_status', Text, nullable=False), Column('created_at', DateTime(timezone=True), nullable=False),
    CheckConstraint("(mapping_status='exact' AND canonical_id IS NOT NULL AND start IS NOT NULL AND \"end\" IS NOT NULL AND start>=0 AND \"end\">start AND cardinality(span_ids)>0) OR (mapping_status='insufficient' AND canonical_id IS NULL AND start IS NULL AND \"end\" IS NULL AND cardinality(span_ids)=0 AND overlap_ranges='[]'::jsonb)", name='chunk_provenance_state'),
    CheckConstraint("array_position(span_ids,NULL) IS NULL AND jsonb_typeof(overlap_ranges)='array'", name='chunk_provenance_arrays'),
)
embedding_input = Table('embedding_input', metadata,
    Column('input_id', Text, primary_key=True), Column('chunk_id', Text, ForeignKey('chunk.chunk_id'), nullable=False),
    Column('title', Text, nullable=False), Column('section_context', ARRAY(Text), nullable=False),
    Column('text_sha256', Text, nullable=False), Column('format_version', Text, nullable=False),
    Column('embedding_key', Text, nullable=False), Column('input_sha256', Text, nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False),
    UniqueConstraint('chunk_id', 'embedding_key', name='embedding_chunk_key_unique'),
    CheckConstraint("input_id ~ '[^[:space:]]' AND format_version ~ '[^[:space:]]' AND embedding_key ~ '[^[:space:]]' AND array_position(section_context,NULL) IS NULL", name='embedding_input_ids'),
    CheckConstraint("text_sha256 ~ '^[0-9a-f]{64}$' AND input_sha256 ~ '^[0-9a-f]{64}$'", name='embedding_input_hashes'),
)
_errors = "('provider','invalid_output','timeout','configuration','storage','conflict','stale_generation','legacy_insufficient','index_divergent')"
ingestion_reservation = Table('ingestion_reservation', metadata,
    Column('reservation_id', Text, primary_key=True), Column('owner_id', Text, nullable=False),
    Column('source_sha256', Text, nullable=False), Column('document_version_id', Text, nullable=False, unique=True),
    Column('phase', Text, nullable=False), Column('generation', BigInteger, nullable=False),
    Column('lease_until', DateTime(timezone=True), nullable=False), Column('attempt_id', Text, nullable=False), Column('error_code', Text),
    UniqueConstraint('owner_id','source_sha256', name='reservation_owner_source_unique'),
    CheckConstraint("reservation_id ~ '[^[:space:]]' AND owner_id ~ '[^[:space:]]' AND document_version_id ~ '[^[:space:]]' AND attempt_id ~ '[^[:space:]]'", name='reservation_ids'),
    CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$' AND generation>=1", name='reservation_hash_generation'),
    CheckConstraint("phase IN ('reserved','parsing','persisted','indexing','ready','failed')", name='reservation_phase'),
    CheckConstraint(f"(phase='failed')=(error_code IS NOT NULL) AND (error_code IS NULL OR error_code IN {_errors})", name='reservation_error'),
)
ingestion_audit_event = Table('ingestion_audit_event', metadata,
    Column('event_id', Text, primary_key=True), Column('attempt_id', Text, nullable=False),
    Column('owner_id', Text, nullable=False),
    Column('document_version_id', Text, ForeignKey('document_version.document_version_id'), nullable=False),
    Column('chunk_id', Text, ForeignKey('chunk.chunk_id'), nullable=False),
    Column('sequence', BigInteger, nullable=False), Column('stage', Text, nullable=False),
    Column('policy_version', Text, nullable=False), Column('input_sha256', Text, nullable=False),
    Column('heuristic_version', Text, nullable=False), Column('heuristic_rule_ids', ARRAY(Text), nullable=False),
    Column('judge_metadata', JSONB(none_as_null=True)), Column('outcome', Text, nullable=False), Column('reason_code', Text, nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False),
    Column('triage_event_id', Text), Column('judge_event_id', Text), Column('supersedes_decision_id', Text),
    UniqueConstraint('chunk_id','sequence', name='audit_chunk_sequence_unique'),
    UniqueConstraint('chunk_id','attempt_id','stage', name='audit_attempt_stage_unique'),
    UniqueConstraint('event_id','chunk_id','owner_id','document_version_id', name='audit_scope_unique'),
    *(ForeignKeyConstraint([field,'chunk_id','owner_id','document_version_id'],
        ['ingestion_audit_event.'+col for col in ('event_id','chunk_id','owner_id','document_version_id')],
        name='audit_'+field+'_scope') for field in ('triage_event_id','judge_event_id','supersedes_decision_id')),
    CheckConstraint("sequence>=1 AND input_sha256 ~ '^[0-9a-f]{64}$'", name='audit_sequence_hash'),
    CheckConstraint("event_id ~ '[^[:space:]]' AND attempt_id ~ '[^[:space:]]' AND policy_version ~ '[^[:space:]]' AND heuristic_version ~ '[^[:space:]]' AND array_position(heuristic_rule_ids,NULL) IS NULL", name='audit_identifiers'),
    CheckConstraint("stage IN ('triage','judge','decision') AND outcome IN ('clean','suspect','safe','injection','error','allowed','blocked','unavailable')", name='audit_stage_outcome'),
    CheckConstraint("reason_code IN ('suspected','rule_block','judge_block','judge_release','no_suspicion','judge_unavailable','legacy_insufficient')", name='audit_reason'),
)
Index('audit_chunk_head', ingestion_audit_event.c.chunk_id, ingestion_audit_event.c.sequence)
effective_chunk_decision = Table('effective_chunk_decision', metadata,
    Column('chunk_id', Text, ForeignKey('chunk.chunk_id'), primary_key=True), Column('owner_id', Text, nullable=False),
    Column('document_version_id', Text, nullable=False), Column('decision_event_id', Text, nullable=False),
    Column('revision', BigInteger, nullable=False), Column('allowed', Boolean, nullable=False), Column('policy_version', Text, nullable=False),
    ForeignKeyConstraint(['decision_event_id','chunk_id','owner_id','document_version_id'],
        ['ingestion_audit_event.'+col for col in ('event_id','chunk_id','owner_id','document_version_id')], name='effective_event_scope'),
    CheckConstraint('revision>=1', name='effective_revision'),
)
reconciliation_job = Table('reconciliation_job', metadata,
    Column('job_id', Text, primary_key=True), Column('owner_id', Text, nullable=False),
    Column('document_version_id', Text, ForeignKey('document_version.document_version_id'), nullable=False),
    Column('embedding_key', Text, nullable=False), Column('desired_revision', BigInteger, nullable=False),
    Column('cursor', Text, ForeignKey('chunk.chunk_id')), Column('attempt', BigInteger, nullable=False),
    Column('state', Text, nullable=False), Column('error_code', Text), Column('created_at', DateTime(timezone=True), nullable=False),
    Column('reservation_id', Text, ForeignKey('ingestion_reservation.reservation_id'), nullable=False),
    Column('generation', BigInteger, nullable=False),
    UniqueConstraint('owner_id','document_version_id','embedding_key','desired_revision', name='job_target_unique'),
    CheckConstraint('desired_revision>=1 AND attempt>=1 AND generation>=1', name='job_revisions'),
    CheckConstraint("job_id ~ '[^[:space:]]' AND owner_id ~ '[^[:space:]]' AND embedding_key ~ '[^[:space:]]'", name='job_identifiers'),
    CheckConstraint("state IN ('pending','running','retry','complete','blocked')", name='job_state'),
    CheckConstraint(f"(state IN ('retry','blocked'))=(error_code IS NOT NULL) AND (error_code IS NULL OR error_code IN {_errors})", name='job_error'),
)
index_ack = Table('index_ack', metadata,
    Column('chunk_id', Text, ForeignKey('chunk.chunk_id'), primary_key=True),
    Column('embedding_key', Text, primary_key=True), Column('owner_id', Text, nullable=False),
    Column('document_version_id', Text, nullable=False), Column('decision_event_id', Text, nullable=False),
    Column('decision_revision', BigInteger, nullable=False), Column('generation', BigInteger, nullable=False),
    Column('reservation_id', Text, ForeignKey('ingestion_reservation.reservation_id'), nullable=False),
    Column('input_id', Text, ForeignKey('embedding_input.input_id')),
    Column('acknowledged_at', DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(['decision_event_id','chunk_id','owner_id','document_version_id'],
        ['ingestion_audit_event.'+col for col in ('event_id','chunk_id','owner_id','document_version_id')], name='ack_event_scope'),
    CheckConstraint('decision_revision>=1 AND generation>=1', name='ack_revisions'),
    CheckConstraint("embedding_key ~ '[^[:space:]]'", name='ack_embedding_key'),
)

# E12: contratos canônicos serializados sem perder campos; chaves relacionais
# independentes do JSON permitem integridade e navegação por owner.
chat_turn = Table(
    'chat_turn', metadata,
    Column('chat_turn_id', Text, primary_key=True),
    Column('owner_id', Text, nullable=False),
    Column('session_id', Text, nullable=False),
    Column('trace_id', Text, nullable=False),
    Column('record', JSONB, nullable=False),
    Column('response', JSONB, nullable=False),
    UniqueConstraint('chat_turn_id','owner_id','session_id', name='chat_turn_owner_session'),
    UniqueConstraint('chat_turn_id','owner_id', name='chat_turn_owner'),
    CheckConstraint("chat_turn_id ~ '[^[:space:]]' AND owner_id ~ '[^[:space:]]' AND session_id ~ '[^[:space:]]' AND trace_id ~ '[^[:space:]]'", name='chat_turn_ids'),
    CheckConstraint("record->>'chat_turn_id'=chat_turn_id AND record->>'user_id'=owner_id AND record->>'session_id'=session_id AND record->>'trace_id'=trace_id AND response->>'chat_turn_id'=chat_turn_id AND response->>'trace_id'=trace_id AND record->>'status'=response->>'status'", name='chat_turn_envelope'),
    CheckConstraint("record->>'status' IN ('ok','recusa','fora_de_escopo','conteudo_insuficiente','erro')",name='chat_turn_status'),
    CheckConstraint("COALESCE(record->>'motivo_recusa','') NOT IN ('tamanho_excedido','limite_de_taxa') OR ((record->>'intencao') IS NULL AND (record->>'modo') IS NULL AND (record->>'input_verdict') IS NULL)", name='chat_turn_e1'),
)
chat_plan = Table('chat_plan',metadata,
    Column('search_plan_id',Text,primary_key=True),
    Column('chat_turn_id',Text,ForeignKey('chat_turn.chat_turn_id'),nullable=False,unique=True),
    Column('record',JSONB,nullable=False),
    CheckConstraint("record->>'search_plan_id'=search_plan_id AND record->>'chat_turn_id'=chat_turn_id",name='chat_plan_ids'))
chat_retrieval = Table('chat_retrieval',metadata,
    Column('retrieval_run_id',Text,primary_key=True),
    Column('chat_turn_id',Text,ForeignKey('chat_turn.chat_turn_id'),nullable=False,unique=True),
    Column('record',JSONB,nullable=False),
    CheckConstraint("record->>'retrieval_run_id'=retrieval_run_id AND record->>'chat_turn_id'=chat_turn_id",name='chat_retrieval_ids'))
chat_candidate = Table('chat_candidate',metadata,
    Column('chat_turn_id',Text,ForeignKey('chat_retrieval.chat_turn_id'),primary_key=True),
    Column('chunk_id',Text,ForeignKey('chunk.chunk_id'),primary_key=True),
    Column('owner_id',Text,nullable=False),
    Column('selected',Boolean,nullable=False),
    ForeignKeyConstraint(['chat_turn_id','owner_id'],['chat_turn.chat_turn_id','chat_turn.owner_id']))
chat_item = Table('chat_item',metadata,
    Column('item_id',Text,primary_key=True),
    Column('chat_turn_id',Text,ForeignKey('chat_turn.chat_turn_id'),nullable=False),
    Column('record',JSONB,nullable=False),
    UniqueConstraint('item_id','chat_turn_id',name='chat_item_turn'),
    CheckConstraint("record->>'item_id'=item_id AND record->>'chat_turn_id'=chat_turn_id",name='chat_item_ids'))
chat_citation = Table('chat_citation',metadata,
    Column('citation_id',Text,primary_key=True),
    Column('item_id',Text,nullable=False),
    Column('chat_turn_id',Text,nullable=False),
    Column('owner_id',Text,nullable=False),
    Column('chunk_id',Text,ForeignKey('chunk.chunk_id'),nullable=False),
    Column('record',JSONB,nullable=False),
    Column('selection',JSONB,nullable=False),
    Column('spans',JSONB,nullable=False),
    ForeignKeyConstraint(['item_id','chat_turn_id'],['chat_item.item_id','chat_item.chat_turn_id']),
    ForeignKeyConstraint(['chat_turn_id','owner_id'],['chat_turn.chat_turn_id','chat_turn.owner_id']),
    ForeignKeyConstraint(['chat_turn_id','chunk_id'],['chat_candidate.chat_turn_id','chat_candidate.chunk_id']),
    CheckConstraint("record->>'citation_id'=citation_id AND record->>'item_id'=item_id AND record->>'chunk_id'=chunk_id AND selection->>'chunk_id'=chunk_id AND selection->>'evidence'=record->>'evidence'",name='chat_citation_ids'),
    CheckConstraint("jsonb_typeof(spans)='array' AND jsonb_array_length(spans)>0",name='chat_citation_spans'))
chat_verdict = Table('chat_verdict',metadata,
    Column('verdict_id',Text,primary_key=True),
    Column('item_id',Text,ForeignKey('chat_item.item_id'),nullable=False),
    Column('record',JSONB,nullable=False),
    CheckConstraint("record->>'verdict_id'=verdict_id AND record->>'item_id'=item_id",name='chat_verdict_ids'))
chat_continuation = Table('chat_continuation',metadata,
    Column('owner_id',Text,primary_key=True),
    Column('session_id',Text,primary_key=True),
    Column('chat_turn_id',Text,nullable=False),
    Column('state',JSONB,nullable=False),
    ForeignKeyConstraint(['chat_turn_id','owner_id','session_id'],['chat_turn.chat_turn_id','chat_turn.owner_id','chat_turn.session_id']))
chat_rate_event = Table('chat_rate_event',metadata,
    Column('event_id',Text,primary_key=True),
    Column('owner_id',Text,nullable=False),
    Column('acquired_at',DateTime(timezone=True),nullable=False),
    CheckConstraint("owner_id ~ '[^[:space:]]'",name='chat_rate_owner'))
Index('chat_rate_owner_time',chat_rate_event.c.owner_id,chat_rate_event.c.acquired_at)
# 0005: auditoria aditiva (tamanho/hash do prompt bruto; bruto de cada tentativa E8).
chat_input_audit = Table('chat_input_audit',metadata,
    Column('chat_turn_id',Text,ForeignKey('chat_turn.chat_turn_id'),primary_key=True),
    Column('raw_length',BigInteger,nullable=False),
    Column('raw_sha256',Text,nullable=False),
    Column('stored_length',BigInteger,nullable=False),
    CheckConstraint("raw_length >= 0 AND stored_length >= 0 AND stored_length <= raw_length AND raw_sha256 ~ '^[0-9a-f]{64}$'",name='chat_input_audit_values'))
chat_generation_call = Table('chat_generation_call',metadata,
    Column('call_id',Text,primary_key=True),
    Column('chat_turn_id',Text,ForeignKey('chat_turn.chat_turn_id'),nullable=False),
    Column('kind',Text,nullable=False),
    Column('status',Text,nullable=False),
    Column('failure_kind',Text),
    Column('repairs_call_id',Text,ForeignKey('chat_generation_call.call_id')),
    Column('context',JSONB,nullable=False),
    CheckConstraint("call_id ~ '[^[:space:]]' AND kind IN ('inicial','reparo') AND status IN ('completed','failed') AND ((status='failed')=(failure_kind IS NOT NULL)) AND (kind='reparo' OR repairs_call_id IS NULL) AND jsonb_typeof(context)='object' AND jsonb_typeof(context->'chunks')='array'",name='chat_generation_call_values'))
chat_generation_attempt = Table('chat_generation_attempt',metadata,
    Column('item_id',Text,primary_key=True),
    Column('attempt',BigInteger,primary_key=True),
    Column('chat_turn_id',Text,nullable=False),
    Column('call_id',Text,ForeignKey('chat_generation_call.call_id'),nullable=False),
    Column('generation_item_index',BigInteger,nullable=False),
    Column('raw_content',JSONB,nullable=False),
    ForeignKeyConstraint(['item_id','chat_turn_id'],['chat_item.item_id','chat_item.chat_turn_id']),
    UniqueConstraint('call_id','generation_item_index',name='chat_generation_attempt_call_index'),
    CheckConstraint("attempt IN (1, 2) AND generation_item_index >= 0 AND jsonb_typeof(raw_content)='object'",name='chat_generation_attempt_values'))
CHAT_TABLES = (chat_turn,chat_plan,chat_retrieval,chat_candidate,chat_item,chat_citation,
               chat_verdict,chat_continuation,chat_rate_event,chat_input_audit,chat_generation_call,chat_generation_attempt)

# CHECK returns TRUE for NULL in SQL: mandatory JSON keys are checked separately.
for _table, _keys in (
    (chat_turn, ('chat_turn_id','user_id','session_id','trace_id','status','created_at')),
    (chat_plan, ('search_plan_id','chat_turn_id','objetivos','bloom_level','consultas')),
    (chat_retrieval, ('retrieval_run_id','chat_turn_id','modo','candidates')),
    (chat_item, ('item_id','chat_turn_id','raw_content','status')),
    (chat_citation, ('citation_id','item_id','chunk_id','chunk_ref','evidence')),
    (chat_verdict, ('verdict_id','item_id','origem','tentativa','aprovado','created_at')),
):
    _table.append_constraint(CheckConstraint(
        "jsonb_typeof(record)='object' AND " + ' AND '.join(
            f"record ? '{key}' AND record->>'{key}' IS NOT NULL" for key in _keys),
        name=_table.name+'_record_keys'))
chat_turn.append_constraint(CheckConstraint(
    "response ? 'chat_turn_id' AND response->>'chat_turn_id' IS NOT NULL AND response ? 'trace_id' AND response->>'trace_id' IS NOT NULL AND response ? 'status' AND response->>'status' IS NOT NULL", name='chat_response_keys'))
chat_item.append_constraint(CheckConstraint(
    "record->>'status' IN ('entregue','omitido') AND ((record->>'status'='entregue')=(record->>'motivo' IS NULL)) AND (record->>'status'<>'entregue' OR record->>'content' IS NOT NULL)",name='chat_item_status'))
chat_verdict.append_constraint(CheckConstraint(
    "record->>'origem' IN ('E9','E10') AND record->>'tentativa' IN ('1','2') AND record->>'aprovado' IN ('true','false') AND ((record->>'aprovado'='true')=(record->>'motivo' IS NULL))",name='chat_verdict_status'))

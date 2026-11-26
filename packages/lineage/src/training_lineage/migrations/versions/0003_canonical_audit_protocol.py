"""ADR-0015: snapshot aditivo T-011; não importa metadata mutável em runtime."""
from alembic import op
revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

DDL = [
r"""
CREATE TABLE ingestion_reservation (
	reservation_id TEXT NOT NULL, 
	owner_id TEXT NOT NULL, 
	source_sha256 TEXT NOT NULL, 
	document_version_id TEXT NOT NULL, 
	phase TEXT NOT NULL, 
	generation BIGINT NOT NULL, 
	lease_until TIMESTAMP WITH TIME ZONE NOT NULL, 
	attempt_id TEXT NOT NULL, 
	error_code TEXT, 
	PRIMARY KEY (reservation_id), 
	CONSTRAINT reservation_owner_source_unique UNIQUE (owner_id, source_sha256), 
	CONSTRAINT reservation_ids CHECK (reservation_id ~ '[^[:space:]]' AND owner_id ~ '[^[:space:]]' AND document_version_id ~ '[^[:space:]]' AND attempt_id ~ '[^[:space:]]'), 
	CONSTRAINT reservation_hash_generation CHECK (source_sha256 ~ '^[0-9a-f]{64}$' AND generation>=1), 
	CONSTRAINT reservation_phase CHECK (phase IN ('reserved','parsing','persisted','indexing','ready','failed')), 
	CONSTRAINT reservation_error CHECK ((phase='failed')=(error_code IS NOT NULL) AND (error_code IS NULL OR error_code IN ('provider','invalid_output','timeout','configuration','storage','conflict','stale_generation','legacy_insufficient','index_divergent'))), 
	UNIQUE (document_version_id)
)

""",
r"""
CREATE TABLE canonical_text (
	canonical_id TEXT NOT NULL, 
	document_version_id TEXT NOT NULL, 
	canonical_version TEXT NOT NULL, 
	parser_version TEXT NOT NULL, 
	text TEXT NOT NULL, 
	text_sha256 TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (canonical_id), 
	CONSTRAINT canonical_recipe_unique UNIQUE (document_version_id, canonical_version), 
	CONSTRAINT canonical_identifiers CHECK (canonical_id ~ '[^[:space:]]' AND canonical_version ~ '[^[:space:]]' AND parser_version ~ '[^[:space:]]' AND length(text)>0), 
	CONSTRAINT canonical_digest CHECK (text_sha256 = encode(sha256(convert_to(text,'UTF8')),'hex')), 
	FOREIGN KEY(document_version_id) REFERENCES document_version (document_version_id)
)

""",
r"""
CREATE TABLE canonical_span (
	span_id TEXT NOT NULL, 
	canonical_id TEXT NOT NULL, 
	start BIGINT NOT NULL, 
	"end" BIGINT NOT NULL, 
	page BIGINT, 
	section_path TEXT[] NOT NULL, 
	h1_id TEXT, 
	kind TEXT NOT NULL, 
	provenance TEXT NOT NULL, 
	PRIMARY KEY (span_id), 
	CONSTRAINT canonical_span_start_unique UNIQUE (canonical_id, start), 
	CONSTRAINT canonical_span_range CHECK (start >= 0 AND "end" > start), 
	CONSTRAINT canonical_span_enums CHECK (kind IN ('heading','paragraph','table','separator') AND provenance IN ('exact','unknown')), 
	CONSTRAINT canonical_span_page CHECK (page IS NULL OR (page >= 1 AND provenance='exact' AND kind<>'separator')), 
	CONSTRAINT canonical_span_ids CHECK (span_id ~ '[^[:space:]]' AND (h1_id IS NULL OR h1_id ~ '[^[:space:]]') AND array_position(section_path,NULL) IS NULL), 
	FOREIGN KEY(canonical_id) REFERENCES canonical_text (canonical_id)
)

""",
r"""
CREATE TABLE chunk_provenance (
	chunk_id TEXT NOT NULL, 
	canonical_id TEXT, 
	start BIGINT, 
	"end" BIGINT, 
	span_ids TEXT[] NOT NULL, 
	overlap_ranges JSONB NOT NULL, 
	mapping_status TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (chunk_id), 
	CONSTRAINT chunk_provenance_state CHECK ((mapping_status='exact' AND canonical_id IS NOT NULL AND start IS NOT NULL AND "end" IS NOT NULL AND start>=0 AND "end">start AND cardinality(span_ids)>0) OR (mapping_status='insufficient' AND canonical_id IS NULL AND start IS NULL AND "end" IS NULL AND cardinality(span_ids)=0 AND overlap_ranges='[]'::jsonb)), 
	CONSTRAINT chunk_provenance_arrays CHECK (array_position(span_ids,NULL) IS NULL AND jsonb_typeof(overlap_ranges)='array'), 
	FOREIGN KEY(chunk_id) REFERENCES chunk (chunk_id), 
	FOREIGN KEY(canonical_id) REFERENCES canonical_text (canonical_id)
)

""",
r"""
CREATE TABLE embedding_input (
	input_id TEXT NOT NULL, 
	chunk_id TEXT NOT NULL, 
	title TEXT NOT NULL, 
	section_context TEXT[] NOT NULL, 
	text_sha256 TEXT NOT NULL, 
	format_version TEXT NOT NULL, 
	embedding_key TEXT NOT NULL, 
	input_sha256 TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (input_id), 
	CONSTRAINT embedding_chunk_key_unique UNIQUE (chunk_id, embedding_key), 
	CONSTRAINT embedding_input_ids CHECK (input_id ~ '[^[:space:]]' AND format_version ~ '[^[:space:]]' AND embedding_key ~ '[^[:space:]]' AND array_position(section_context,NULL) IS NULL), 
	CONSTRAINT embedding_input_hashes CHECK (text_sha256 ~ '^[0-9a-f]{64}$' AND input_sha256 ~ '^[0-9a-f]{64}$'), 
	FOREIGN KEY(chunk_id) REFERENCES chunk (chunk_id)
)

""",
r"""
CREATE TABLE ingestion_audit_event (
	event_id TEXT NOT NULL, 
	attempt_id TEXT NOT NULL, 
	owner_id TEXT NOT NULL, 
	document_version_id TEXT NOT NULL, 
	chunk_id TEXT NOT NULL, 
	sequence BIGINT NOT NULL, 
	stage TEXT NOT NULL, 
	policy_version TEXT NOT NULL, 
	input_sha256 TEXT NOT NULL, 
	heuristic_version TEXT NOT NULL, 
	heuristic_rule_ids TEXT[] NOT NULL, 
	judge_metadata JSONB, 
	outcome TEXT NOT NULL, 
	reason_code TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	triage_event_id TEXT, 
	judge_event_id TEXT, 
	supersedes_decision_id TEXT, 
	PRIMARY KEY (event_id), 
	CONSTRAINT audit_chunk_sequence_unique UNIQUE (chunk_id, sequence), 
	CONSTRAINT audit_attempt_stage_unique UNIQUE (chunk_id, attempt_id, stage), 
	CONSTRAINT audit_scope_unique UNIQUE (event_id, chunk_id, owner_id, document_version_id), 
	CONSTRAINT audit_triage_event_id_scope FOREIGN KEY(triage_event_id, chunk_id, owner_id, document_version_id) REFERENCES ingestion_audit_event (event_id, chunk_id, owner_id, document_version_id), 
	CONSTRAINT audit_judge_event_id_scope FOREIGN KEY(judge_event_id, chunk_id, owner_id, document_version_id) REFERENCES ingestion_audit_event (event_id, chunk_id, owner_id, document_version_id), 
	CONSTRAINT audit_supersedes_decision_id_scope FOREIGN KEY(supersedes_decision_id, chunk_id, owner_id, document_version_id) REFERENCES ingestion_audit_event (event_id, chunk_id, owner_id, document_version_id), 
	CONSTRAINT audit_sequence_hash CHECK (sequence>=1 AND input_sha256 ~ '^[0-9a-f]{64}$'), 
	CONSTRAINT audit_identifiers CHECK (event_id ~ '[^[:space:]]' AND attempt_id ~ '[^[:space:]]' AND policy_version ~ '[^[:space:]]' AND heuristic_version ~ '[^[:space:]]' AND array_position(heuristic_rule_ids,NULL) IS NULL), 
	CONSTRAINT audit_stage_outcome CHECK (stage IN ('triage','judge','decision') AND outcome IN ('clean','suspect','safe','injection','error','allowed','blocked','unavailable')), 
	CONSTRAINT audit_reason CHECK (reason_code IN ('suspected','rule_block','judge_block','judge_release','no_suspicion','judge_unavailable','legacy_insufficient')), 
	FOREIGN KEY(document_version_id) REFERENCES document_version (document_version_id), 
	FOREIGN KEY(chunk_id) REFERENCES chunk (chunk_id)
)

""",
r"""CREATE INDEX audit_chunk_head ON ingestion_audit_event (chunk_id, sequence)""",
r"""
CREATE TABLE reconciliation_job (
	job_id TEXT NOT NULL, 
	owner_id TEXT NOT NULL, 
	document_version_id TEXT NOT NULL, 
	embedding_key TEXT NOT NULL, 
	desired_revision BIGINT NOT NULL, 
	cursor TEXT, 
	attempt BIGINT NOT NULL, 
	state TEXT NOT NULL, 
	error_code TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	reservation_id TEXT NOT NULL, 
	generation BIGINT NOT NULL, 
	PRIMARY KEY (job_id), 
	CONSTRAINT job_target_unique UNIQUE (owner_id, document_version_id, embedding_key, desired_revision), 
	CONSTRAINT job_revisions CHECK (desired_revision>=1 AND attempt>=1 AND generation>=1), 
	CONSTRAINT job_identifiers CHECK (job_id ~ '[^[:space:]]' AND owner_id ~ '[^[:space:]]' AND embedding_key ~ '[^[:space:]]'), 
	CONSTRAINT job_state CHECK (state IN ('pending','running','retry','complete','blocked')), 
	CONSTRAINT job_error CHECK ((state IN ('retry','blocked'))=(error_code IS NOT NULL) AND (error_code IS NULL OR error_code IN ('provider','invalid_output','timeout','configuration','storage','conflict','stale_generation','legacy_insufficient','index_divergent'))), 
	FOREIGN KEY(document_version_id) REFERENCES document_version (document_version_id), 
	FOREIGN KEY(cursor) REFERENCES chunk (chunk_id), 
	FOREIGN KEY(reservation_id) REFERENCES ingestion_reservation (reservation_id)
)

""",
r"""
CREATE TABLE effective_chunk_decision (
	chunk_id TEXT NOT NULL, 
	owner_id TEXT NOT NULL, 
	document_version_id TEXT NOT NULL, 
	decision_event_id TEXT NOT NULL, 
	revision BIGINT NOT NULL, 
	allowed BOOLEAN NOT NULL, 
	policy_version TEXT NOT NULL, 
	PRIMARY KEY (chunk_id), 
	CONSTRAINT effective_event_scope FOREIGN KEY(decision_event_id, chunk_id, owner_id, document_version_id) REFERENCES ingestion_audit_event (event_id, chunk_id, owner_id, document_version_id), 
	CONSTRAINT effective_revision CHECK (revision>=1), 
	FOREIGN KEY(chunk_id) REFERENCES chunk (chunk_id)
)

""",
r"""
CREATE TABLE index_ack (
	chunk_id TEXT NOT NULL, 
	embedding_key TEXT NOT NULL, 
	owner_id TEXT NOT NULL, 
	document_version_id TEXT NOT NULL, 
	decision_event_id TEXT NOT NULL, 
	decision_revision BIGINT NOT NULL, 
	generation BIGINT NOT NULL, 
	reservation_id TEXT NOT NULL, 
	input_id TEXT, 
	acknowledged_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (chunk_id, embedding_key), 
	CONSTRAINT ack_event_scope FOREIGN KEY(decision_event_id, chunk_id, owner_id, document_version_id) REFERENCES ingestion_audit_event (event_id, chunk_id, owner_id, document_version_id), 
	CONSTRAINT ack_revisions CHECK (decision_revision>=1 AND generation>=1), 
	CONSTRAINT ack_embedding_key CHECK (embedding_key ~ '[^[:space:]]'), 
	FOREIGN KEY(chunk_id) REFERENCES chunk (chunk_id), 
	FOREIGN KEY(reservation_id) REFERENCES ingestion_reservation (reservation_id), 
	FOREIGN KEY(input_id) REFERENCES embedding_input (input_id)
)

"""
]

HISTORY = ('canonical_text','canonical_span','chunk_provenance','embedding_input','ingestion_audit_event')
NEW_TABLES = HISTORY + ('ingestion_reservation','effective_chunk_decision','reconciliation_job','index_ack')
VALIDATIONS = [
r"""CREATE FUNCTION t011_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'append-only history is immutable' USING ERRCODE='23514'; END; $$""",
r"""CREATE FUNCTION t011_canonical_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parser text;
BEGIN
 SELECT parser_version INTO parser FROM document_version WHERE document_version_id=NEW.document_version_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'canonical parent missing' USING ERRCODE='23503'; END IF;
 IF parser<>NEW.parser_version THEN RAISE EXCEPTION 'canonical parser mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END; $$""",
r"""CREATE FUNCTION t011_canonical_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE n bigint; lo bigint; hi bigint; total bigint;
BEGIN
 SELECT count(*), min(start), max("end"), sum("end"-start) INTO n,lo,hi,total
 FROM canonical_span WHERE canonical_id=NEW.canonical_id;
 IF n=0 OR lo<>0 OR hi<>char_length(NEW.text) OR total<>char_length(NEW.text) THEN
  RAISE EXCEPTION 'canonical partition incomplete' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END; $$""",
r"""CREATE FUNCTION t011_span_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE length bigint;
BEGIN
 SELECT char_length(text) INTO length FROM canonical_text WHERE canonical_id=NEW.canonical_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'span parent missing' USING ERRCODE='23503'; END IF;
 IF NEW."end">length OR EXISTS(SELECT 1 FROM canonical_span WHERE canonical_id=NEW.canonical_id
  AND start<NEW."end" AND NEW.start<"end") OR EXISTS(SELECT 1 FROM unnest(NEW.section_path) s WHERE s !~ '[^[:space:]]') THEN
  RAISE EXCEPTION 'span range or section invalid' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END; $$""",
r"""CREATE FUNCTION t011_mapping_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c chunk%ROWTYPE; source canonical_text%ROWTYPE; ids text[]; h1_count bigint;
        overlap jsonb; previous_end bigint; lo bigint; hi bigint;
BEGIN
 SELECT * INTO c FROM chunk WHERE chunk_id=NEW.chunk_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'mapping chunk missing' USING ERRCODE='23503'; END IF;
 IF NEW.mapping_status='insufficient' THEN RETURN NEW; END IF;
 SELECT * INTO source FROM canonical_text WHERE canonical_id=NEW.canonical_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'mapping canonical missing' USING ERRCODE='23503'; END IF;
 IF source.document_version_id<>c.document_version_id OR NEW.start<>c.start_offset OR NEW."end"<>c.end_offset
  OR NEW."end">char_length(source.text) OR c.text<>substr(source.text,NEW.start::int+1,(NEW."end"-NEW.start)::int) THEN
  RAISE EXCEPTION 'mapping content mismatch' USING ERRCODE='23514'; END IF;
 SELECT array_agg(span_id ORDER BY start), count(DISTINCT h1_id) INTO ids,h1_count FROM canonical_span
  WHERE canonical_id=NEW.canonical_id AND start<NEW."end" AND NEW.start<"end";
 IF ids IS DISTINCT FROM NEW.span_ids OR h1_count>1 THEN
  RAISE EXCEPTION 'mapping spans mismatch' USING ERRCODE='23514'; END IF;
 IF jsonb_typeof(NEW.overlap_ranges) IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'overlap array invalid' USING ERRCODE='23514'; END IF;
 previous_end:=NEW.start;
 FOR overlap IN SELECT * FROM jsonb_array_elements(NEW.overlap_ranges) LOOP
  IF jsonb_typeof(overlap->'start') IS DISTINCT FROM 'number' OR jsonb_typeof(overlap->'end') IS DISTINCT FROM 'number'
   OR overlap->>'start' !~ '^[0-9]+$' OR overlap->>'end' !~ '^[0-9]+$' THEN
   RAISE EXCEPTION 'overlap invalid' USING ERRCODE='23514'; END IF;
  lo:=(overlap->>'start')::bigint; hi:=(overlap->>'end')::bigint;
  IF lo<previous_end OR hi<=lo OR hi>NEW."end" THEN RAISE EXCEPTION 'overlap range invalid' USING ERRCODE='23514'; END IF;
  previous_end:=hi;
 END LOOP;
 RETURN NEW;
END; $$""",
r"""CREATE FUNCTION t011_embedding_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE value text;
BEGIN
 SELECT text INTO value FROM chunk WHERE chunk_id=NEW.chunk_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'embedding chunk missing' USING ERRCODE='23503'; END IF;
 IF NEW.text_sha256<>encode(sha256(convert_to(value,'UTF8')),'hex')
  OR right(NEW.embedding_key,char_length(NEW.format_version)+1)<>'@'||NEW.format_version
  OR EXISTS(SELECT 1 FROM unnest(NEW.section_context) s WHERE s !~ '[^[:space:]]') THEN
  RAISE EXCEPTION 'embedding source mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END; $$""",
r"""CREATE FUNCTION t011_audit_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c chunk%ROWTYPE; owning text; head bigint; t ingestion_audit_event%ROWTYPE;
 j ingestion_audit_event%ROWTYPE; latest_triage text; previous_decision text; md jsonb; k text;
BEGIN
 SELECT * INTO c FROM chunk WHERE chunk_id=NEW.chunk_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'audit chunk missing' USING ERRCODE='23503'; END IF;
 SELECT owner_id INTO owning FROM document_version WHERE document_version_id=c.document_version_id;
 IF (c.document_version_id,owning) IS DISTINCT FROM (NEW.document_version_id,NEW.owner_id) THEN
  RAISE EXCEPTION 'audit scope mismatch' USING ERRCODE='23503'; END IF;
 SELECT coalesce(max(sequence),0) INTO head FROM ingestion_audit_event WHERE chunk_id=NEW.chunk_id;
 IF NEW.sequence<>head+1 THEN RAISE EXCEPTION 'audit revision stale' USING ERRCODE='23505'; END IF;
 IF cardinality(NEW.heuristic_rule_ids)<>(SELECT count(DISTINCT x) FROM unnest(NEW.heuristic_rule_ids) x)
  OR EXISTS(SELECT 1 FROM unnest(NEW.heuristic_rule_ids) x WHERE x !~ '[^[:space:]]') THEN
  RAISE EXCEPTION 'audit rules invalid' USING ERRCODE='23514'; END IF;
 IF NEW.stage='triage' THEN
  IF NEW.judge_metadata IS NOT NULL OR NEW.triage_event_id IS NOT NULL OR NEW.judge_event_id IS NOT NULL
   OR NEW.supersedes_decision_id IS NOT NULL OR NOT (
    (NEW.outcome='clean' AND NEW.reason_code='no_suspicion' AND cardinality(NEW.heuristic_rule_ids)=0) OR
    (NEW.outcome='suspect' AND NEW.reason_code='suspected' AND cardinality(NEW.heuristic_rule_ids)>0)) THEN
   RAISE EXCEPTION 'triage invalid' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 SELECT * INTO t FROM ingestion_audit_event WHERE event_id=NEW.triage_event_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'triage reference missing' USING ERRCODE='23503'; END IF;
 SELECT event_id INTO latest_triage FROM ingestion_audit_event WHERE chunk_id=NEW.chunk_id AND stage='triage' ORDER BY sequence DESC LIMIT 1;
 IF t.stage<>'triage' OR t.event_id<>latest_triage OR
  (t.chunk_id,t.owner_id,t.document_version_id,t.attempt_id,t.policy_version,t.input_sha256,t.heuristic_version,t.heuristic_rule_ids)
  IS DISTINCT FROM
  (NEW.chunk_id,NEW.owner_id,NEW.document_version_id,NEW.attempt_id,NEW.policy_version,NEW.input_sha256,NEW.heuristic_version,NEW.heuristic_rule_ids) THEN
  RAISE EXCEPTION 'triage scope or policy stale' USING ERRCODE='23514'; END IF;
 IF NEW.stage='judge' THEN
  md:=NEW.judge_metadata;
  IF t.outcome<>'suspect' OR NEW.judge_event_id IS NOT NULL OR NEW.supersedes_decision_id IS NOT NULL
   OR md IS NULL OR jsonb_typeof(md) IS DISTINCT FROM 'object'
   OR NOT (md ?& ARRAY['provider','model','config_version','config_sha256','prompt_version','prompt_sha256','request_id','result','error_kind'])
   OR md-ARRAY['provider','model','config_version','config_sha256','prompt_version','prompt_sha256','request_id','result','error_kind']<>'{}'::jsonb THEN
   RAISE EXCEPTION 'judge metadata invalid' USING ERRCODE='23514'; END IF;
  FOREACH k IN ARRAY ARRAY['provider','model','config_version','config_sha256','prompt_version','prompt_sha256','result'] LOOP
   IF jsonb_typeof(md->k) IS DISTINCT FROM 'string' OR md->>k !~ '[^[:space:]]' THEN
    RAISE EXCEPTION 'judge identifiers invalid' USING ERRCODE='23514'; END IF;
  END LOOP;
  IF md->>'provider' NOT IN ('google','openai','anthropic','openrouter') OR md->>'config_sha256' !~ '^[0-9a-f]{64}$'
   OR md->>'prompt_sha256' !~ '^[0-9a-f]{64}$' OR md->>'result' IS DISTINCT FROM NEW.outcome
   OR NOT ((NEW.outcome='safe' AND NEW.reason_code='judge_release') OR
           (NEW.outcome='injection' AND NEW.reason_code='judge_block') OR
           (NEW.outcome='error' AND NEW.reason_code='judge_unavailable'))
   OR ((NEW.outcome='error') IS DISTINCT FROM (md->>'error_kind' IS NOT NULL))
   OR (md->>'error_kind' IS NOT NULL AND md->>'error_kind' NOT IN ('provider','invalid_output','timeout','configuration','storage','conflict','stale_generation','legacy_insufficient','index_divergent'))
   OR (md->>'request_id' IS NOT NULL AND md->>'request_id' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$') THEN
   RAISE EXCEPTION 'judge result invalid' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.stage<>'decision' OR NEW.judge_metadata IS NOT NULL THEN RAISE EXCEPTION 'decision invalid' USING ERRCODE='23514'; END IF;
 IF NEW.judge_event_id IS NOT NULL THEN
  SELECT * INTO j FROM ingestion_audit_event WHERE event_id=NEW.judge_event_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'judge reference missing' USING ERRCODE='23503'; END IF;
  IF j.stage<>'judge' OR j.triage_event_id<>t.event_id OR
   (j.chunk_id,j.owner_id,j.document_version_id,j.attempt_id,j.policy_version,j.input_sha256,j.heuristic_version)
   IS DISTINCT FROM (NEW.chunk_id,NEW.owner_id,NEW.document_version_id,NEW.attempt_id,NEW.policy_version,NEW.input_sha256,NEW.heuristic_version) THEN
   RAISE EXCEPTION 'judge scope mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 SELECT event_id INTO previous_decision FROM ingestion_audit_event WHERE chunk_id=NEW.chunk_id AND stage='decision' ORDER BY sequence DESC LIMIT 1;
 IF NEW.supersedes_decision_id IS DISTINCT FROM previous_decision THEN RAISE EXCEPTION 'superseded decision stale' USING ERRCODE='23514'; END IF;
 IF NOT (
  (NEW.reason_code='no_suspicion' AND NEW.outcome='allowed' AND t.outcome='clean' AND NEW.judge_event_id IS NULL) OR
  (NEW.reason_code='judge_release' AND NEW.outcome='allowed' AND NEW.judge_event_id IS NOT NULL AND j.outcome='safe') OR
  (NEW.reason_code='rule_block' AND NEW.outcome='blocked' AND t.outcome='suspect') OR
  (NEW.reason_code='judge_block' AND NEW.outcome='blocked' AND NEW.judge_event_id IS NOT NULL AND j.outcome='injection') OR
  (NEW.reason_code='judge_unavailable' AND NEW.outcome='unavailable' AND NEW.judge_event_id IS NOT NULL AND j.outcome='error') OR
  (NEW.reason_code='legacy_insufficient' AND NEW.outcome='unavailable' AND EXISTS(
      SELECT 1 FROM chunk_provenance WHERE chunk_id=NEW.chunk_id AND mapping_status='insufficient'))) THEN
  RAISE EXCEPTION 'decision reason mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.outcome='allowed' AND (NOT EXISTS(SELECT 1 FROM chunk_provenance WHERE chunk_id=NEW.chunk_id AND mapping_status='exact')
  OR NOT EXISTS(SELECT 1 FROM embedding_input WHERE chunk_id=NEW.chunk_id)) THEN
  RAISE EXCEPTION 'allowed source incomplete' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END; $$""",
r"""CREATE FUNCTION t011_effective_write() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e ingestion_audit_event%ROWTYPE; head bigint;
BEGIN
 SELECT * INTO e FROM ingestion_audit_event WHERE event_id=NEW.decision_event_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'decision reference missing' USING ERRCODE='23503'; END IF;
 SELECT max(sequence) INTO head FROM ingestion_audit_event WHERE chunk_id=NEW.chunk_id;
 IF e.stage<>'decision' OR e.sequence<>head OR
  (e.chunk_id,e.owner_id,e.document_version_id,e.sequence,e.policy_version,e.outcome='allowed') IS DISTINCT FROM
  (NEW.chunk_id,NEW.owner_id,NEW.document_version_id,NEW.revision,NEW.policy_version,NEW.allowed) THEN
  RAISE EXCEPTION 'effective decision stale or invalid' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END; $$""",
r"""CREATE FUNCTION t011_reservation_write() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v document_version%ROWTYPE;
BEGIN
 SELECT * INTO v FROM document_version WHERE document_version_id=NEW.document_version_id;
 IF FOUND AND (v.owner_id,v.sha256) IS DISTINCT FROM (NEW.owner_id,NEW.source_sha256) THEN
  RAISE EXCEPTION 'reservation source mismatch' USING ERRCODE='23503'; END IF;
 IF TG_OP='UPDATE' THEN
  IF (NEW.reservation_id,NEW.owner_id,NEW.source_sha256,NEW.document_version_id) IS DISTINCT FROM
     (OLD.reservation_id,OLD.owner_id,OLD.source_sha256,OLD.document_version_id)
   OR NEW.generation NOT IN (OLD.generation,OLD.generation+1)
   OR (NEW.generation=OLD.generation AND NEW.attempt_id<>OLD.attempt_id)
   OR (NEW.generation>OLD.generation AND OLD.lease_until>clock_timestamp() AND OLD.phase NOT IN ('ready','failed')) THEN
   RAISE EXCEPTION 'reservation fencing invalid' USING ERRCODE='23514'; END IF;
  IF NEW.generation=OLD.generation AND NEW.phase<>OLD.phase AND OLD.lease_until<=clock_timestamp() THEN
   RAISE EXCEPTION 'reservation lease expired' USING ERRCODE='23514'; END IF;
  IF NEW.phase='ready' AND OLD.phase<>'ready' THEN
   IF OLD.phase<>'indexing' OR NEW.generation<>OLD.generation
    OR NOT EXISTS(SELECT 1 FROM chunk WHERE document_version_id=NEW.document_version_id)
    OR NOT EXISTS(SELECT 1 FROM reconciliation_job WHERE document_version_id=NEW.document_version_id)
    OR EXISTS(SELECT 1 FROM reconciliation_job j WHERE j.document_version_id=NEW.document_version_id
      AND j.desired_revision=(SELECT max(j2.desired_revision) FROM reconciliation_job j2
        WHERE j2.document_version_id=j.document_version_id AND j2.embedding_key=j.embedding_key)
      AND (j.state<>'complete' OR j.generation<>NEW.generation))
    OR EXISTS(SELECT 1 FROM chunk c WHERE c.document_version_id=NEW.document_version_id AND NOT EXISTS(
      SELECT 1 FROM effective_chunk_decision d JOIN ingestion_audit_event e ON e.event_id=d.decision_event_id
      JOIN chunk_provenance m ON m.chunk_id=d.chunk_id AND m.mapping_status='exact'
      WHERE d.chunk_id=c.chunk_id AND d.owner_id=NEW.owner_id AND e.outcome IN ('allowed','blocked')
      AND d.revision=(SELECT max(sequence) FROM ingestion_audit_event WHERE chunk_id=c.chunk_id)
      AND NOT EXISTS(SELECT 1 FROM reconciliation_job j WHERE j.document_version_id=NEW.document_version_id
        AND j.desired_revision=(SELECT max(j2.desired_revision) FROM reconciliation_job j2
          WHERE j2.document_version_id=j.document_version_id AND j2.embedding_key=j.embedding_key)
        AND NOT EXISTS(SELECT 1 FROM index_ack a WHERE a.chunk_id=c.chunk_id AND a.owner_id=NEW.owner_id
          AND a.embedding_key=j.embedding_key AND a.generation=NEW.generation AND a.decision_event_id=e.event_id
          AND a.decision_revision=d.revision AND (NOT d.allowed OR EXISTS(SELECT 1 FROM embedding_input i
            WHERE i.input_id=a.input_id AND i.chunk_id=c.chunk_id AND i.embedding_key=j.embedding_key)))))) THEN
    RAISE EXCEPTION 'ready publication incomplete' USING ERRCODE='23514'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END; $$""",
r"""CREATE FUNCTION t011_operational_scope() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r ingestion_reservation%ROWTYPE; owning text; cursor_version text; e ingestion_audit_event%ROWTYPE; head bigint;
BEGIN
 SELECT owner_id INTO owning FROM document_version WHERE document_version_id=NEW.document_version_id;
 IF NOT FOUND OR owning<>NEW.owner_id THEN RAISE EXCEPTION 'operational owner mismatch' USING ERRCODE='23503'; END IF;
 SELECT * INTO r FROM ingestion_reservation WHERE reservation_id=NEW.reservation_id FOR UPDATE;
 IF NOT FOUND OR (r.owner_id,r.document_version_id) IS DISTINCT FROM (NEW.owner_id,NEW.document_version_id) THEN
  RAISE EXCEPTION 'operational reservation mismatch' USING ERRCODE='23503'; END IF;
 IF r.generation<>NEW.generation OR r.lease_until<=clock_timestamp() OR r.phase IN ('ready','failed') THEN
  RAISE EXCEPTION 'operational generation stale' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='reconciliation_job' THEN
  IF TG_OP='UPDATE' AND ((NEW.job_id,NEW.owner_id,NEW.document_version_id,NEW.embedding_key,NEW.desired_revision,NEW.created_at,NEW.reservation_id)
    IS DISTINCT FROM (OLD.job_id,OLD.owner_id,OLD.document_version_id,OLD.embedding_key,OLD.desired_revision,OLD.created_at,OLD.reservation_id)
    OR NEW.attempt NOT IN (OLD.attempt,OLD.attempt+1)
    OR (OLD.cursor IS NOT NULL AND (NEW.cursor IS NULL OR NEW.cursor<OLD.cursor))) THEN
   RAISE EXCEPTION 'checkpoint identity or cursor invalid' USING ERRCODE='23514'; END IF;
  IF EXISTS(SELECT 1 FROM reconciliation_job WHERE owner_id=NEW.owner_id AND document_version_id=NEW.document_version_id
   AND embedding_key=NEW.embedding_key AND desired_revision>NEW.desired_revision) THEN
   RAISE EXCEPTION 'job revision stale' USING ERRCODE='23514'; END IF;
  IF NEW.cursor IS NOT NULL THEN
   SELECT document_version_id INTO cursor_version FROM chunk WHERE chunk_id=NEW.cursor;
   IF NOT FOUND OR cursor_version<>NEW.document_version_id THEN RAISE EXCEPTION 'job cursor outside scope' USING ERRCODE='23503'; END IF;
  END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM reconciliation_job WHERE owner_id=NEW.owner_id
    AND document_version_id=NEW.document_version_id AND embedding_key=NEW.embedding_key) THEN
   RAISE EXCEPTION 'ack target missing' USING ERRCODE='23503'; END IF;
  SELECT * INTO e FROM ingestion_audit_event WHERE event_id=NEW.decision_event_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'ack decision missing' USING ERRCODE='23503'; END IF;
  SELECT max(sequence) INTO head FROM ingestion_audit_event WHERE chunk_id=NEW.chunk_id;
  IF e.stage<>'decision' OR e.outcome NOT IN ('allowed','blocked') OR e.sequence<>head OR
   (e.chunk_id,e.owner_id,e.document_version_id,e.sequence) IS DISTINCT FROM
   (NEW.chunk_id,NEW.owner_id,NEW.document_version_id,NEW.decision_revision) THEN
   RAISE EXCEPTION 'ack decision stale' USING ERRCODE='23514'; END IF;
  IF (e.outcome='allowed' AND NOT EXISTS(SELECT 1 FROM embedding_input WHERE input_id=NEW.input_id
    AND chunk_id=NEW.chunk_id AND embedding_key=NEW.embedding_key)) OR (e.outcome='blocked' AND NEW.input_id IS NOT NULL) THEN
   RAISE EXCEPTION 'ack input mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END; $$""",
]
TRIGGERS = [
 ('canonical_text','BEFORE INSERT','t011_canonical_insert'),
 ('canonical_span','BEFORE INSERT','t011_span_insert'),
 ('chunk_provenance','BEFORE INSERT','t011_mapping_insert'),
 ('embedding_input','BEFORE INSERT','t011_embedding_insert'),
 ('ingestion_audit_event','BEFORE INSERT','t011_audit_insert'),
 ('effective_chunk_decision','BEFORE INSERT OR UPDATE','t011_effective_write'),
 ('ingestion_reservation','BEFORE INSERT OR UPDATE','t011_reservation_write'),
 ('reconciliation_job','BEFORE INSERT OR UPDATE','t011_operational_scope'),
 ('index_ack','BEFORE INSERT OR UPDATE','t011_operational_scope'),
]


def upgrade():
    for statement in DDL + VALIDATIONS:
        op.execute(statement)
    for table in HISTORY:
        op.execute(f'CREATE TRIGGER {table}_history_rows BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION t011_immutable()')
        op.execute(f'CREATE TRIGGER {table}_history_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION t011_immutable()')
    for table, event, function in TRIGGERS:
        op.execute(f'CREATE TRIGGER {table}_validate {event} ON {table} FOR EACH ROW EXECUTE FUNCTION {function}()')
    op.execute('CREATE CONSTRAINT TRIGGER canonical_partition_complete AFTER INSERT ON canonical_text DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION t011_canonical_complete()')


def downgrade():
    # Verificar TODAS as tabelas antes de QUALQUER DROP; preservar até checkpoints.
    for table in NEW_TABLES:
        op.execute(f"DO $$ BEGIN IF EXISTS(SELECT 1 FROM {table}) THEN RAISE EXCEPTION 'história nova impede downgrade destrutivo' USING ERRCODE='23514'; END IF; END; $$")
    for table in ('index_ack','reconciliation_job','effective_chunk_decision','ingestion_audit_event',
                  'ingestion_reservation','embedding_input','chunk_provenance','canonical_span','canonical_text'):
        op.drop_table(table)
    for function in ('t011_operational_scope','t011_reservation_write','t011_effective_write','t011_audit_insert',
                     't011_embedding_insert','t011_mapping_insert','t011_span_insert','t011_canonical_complete',
                     't011_canonical_insert','t011_immutable'):
        op.execute(f'DROP FUNCTION {function}()')

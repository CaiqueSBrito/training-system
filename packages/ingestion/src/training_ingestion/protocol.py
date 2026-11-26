"""Execução durável I1–I6 usando exclusivamente os contratos T010/T011.

O chamador de fila fornece chamadas repetíveis; este módulo não inicia scheduler,
threads de renovação nem retentativas automáticas.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from training_contracts.ingestao import (
    EmbeddingInputRecord, EffectiveChunkDecision, IngestionAuditEvent,
    JudgeMetadata, ReconciliationJob,
)
from training_contracts.linhagem import ChunkRecord, DocumentVersionRecord
from training_lineage import LineageRepository
from training_llm import InjectionJudgeError, document_input

from .core import IngestionError
from .indexing import high_precision_reason, suspicious_reason


LEASE_SECONDS = 900
LEASE_CONFIG_VERSION = 'lease-v1:900s'
POLICY_VERSION = f'ingestion-policy-v3:{LEASE_CONFIG_VERSION}'
HEURISTIC_VERSION = 'heuristica-injecao-v3'


class IngestionBusy(RuntimeError):
    """Uma execução válida ainda mantém a reserva do mesmo dono/hash."""


class IngestionLeaseLost(RuntimeError):
    """Fencing recusou publicação por geração/lease obsoleta."""


class IngestionStageError(RuntimeError):
    """Falha sanitizada; não contém texto, resposta ou exceção do fornecedor."""
    def __init__(self, category, *, status_code=None, retryable=None):
        self.category, self.status_code, self.retryable = category, status_code, retryable
        super().__init__(f'Falha na etapa de ingestão: {category}')


@dataclass(frozen=True)
class _Assessment:
    decision: EffectiveChunkDecision
    rule_reason: str | None


class DurableIngestionProtocol:
    def __init__(self, engine, parser, chunker, processor, max_file_bytes):
        self.engine, self.parser, self.chunker = engine, parser, chunker
        self.processor, self.max_file_bytes = processor, max_file_bytes
        self.model = processor.model_client
        self.index = processor.index
        self.embedding_key = self.index.settings.embedding_key

    def _read(self, operation):
        with self.engine.connect() as connection:
            return operation(LineageRepository(connection))

    def _write(self, operation):
        with self.engine.begin() as connection:
            return operation(LineageRepository(connection))

    def _renew(self, owner_id, reservation_id, generation):
        try:
            return self._write(lambda repo: repo.renew(reservation_id, generation,
                owner_id=owner_id, lease_seconds=LEASE_SECONDS))
        except ValueError:
            raise IngestionLeaseLost('Reserva obsoleta ou expirada') from None

    def ingest(self, path, owner_id, *, document_id=None):
        path = Path(path)
        if path.suffix.lower() not in {'.pdf', '.docx', '.md', '.markdown'}:
            raise IngestionError('formato_nao_suportado')
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise IngestionError('dono_invalido')
        with path.open('rb') as source:
            data = source.read(self.max_file_bytes + 1)
        if len(data) > self.max_file_bytes:
            raise IngestionError('tamanho_excedido')
        if not data:
            raise IngestionError('arquivo_vazio')
        digest = sha256(data).hexdigest()
        attempt_id = str(uuid4())
        status, reservation = self._write(lambda repo: repo.reserve(
            owner_id, digest, attempt_id, lease_seconds=LEASE_SECONDS))
        if status == 'busy':
            raise IngestionBusy('Ingestão já está em andamento')
        if status == 'ready':
            return self._ready_result(owner_id, reservation.document_version_id, True)

        try:
            self._run(owner_id, path, data, digest, document_id, attempt_id, reservation)
            return self._ready_result(owner_id, reservation.document_version_id, False)
        except IngestionLeaseLost:
            raise
        except Exception as exc:
            category, status_code, retryable = self._failure_category(exc)
            try:
                self._record_failure(owner_id, reservation, category)
            except Exception:
                pass  # A lease vencida ou erro de storage não pode ser sobrescrito.
            if isinstance(exc, IngestionStageError):
                raise
            if isinstance(exc, IngestionError):
                raise
            raise IngestionStageError(category, status_code=status_code,
                                      retryable=retryable) from None

    def _run(self, owner_id, path, data, digest, document_id, attempt_id, reservation):
        version_id = reservation.document_version_id
        newly_parsed = reservation.phase == 'reserved'
        if newly_parsed:
            self._write(lambda repo: repo.transition_reservation(
                reservation.reservation_id, reservation.generation, owner_id=owner_id,
                phase='parsing'))
            self._renew(owner_id, reservation.reservation_id, reservation.generation)
            filename = 'source.md' if path.suffix.lower() == '.markdown' else 'source' + path.suffix.lower()
            parsed = self.parser.parse(data, filename, version_id)
            chunks, provenance = self.chunker.split_with_provenance(parsed, version_id)
            self._renew(owner_id, reservation.reservation_id, reservation.generation)
            if not chunks:
                raise IngestionStageError('storage')
            title = parsed.title or path.stem
            now = datetime.now(timezone.utc)
            version = DocumentVersionRecord(document_version_id=version_id,
                document_id=document_id or str(uuid4()), owner_id=owner_id, sha256=digest,
                parser_version=self.parser.version, created_at=now)
            inputs = tuple(EmbeddingInputRecord(input_id=str(uuid4()), chunk_id=item.chunk_id,
                title=title, section_context=tuple(item.section_path),
                text_sha256=sha256(item.text.encode('utf-8')).hexdigest(),
                format_version=self.index.settings.format_version,
                embedding_key=self.embedding_key,
                input_sha256=sha256(document_input(title, item.section_path, item.text).encode('utf-8')).hexdigest(),
                created_at=now) for item in chunks)
            job = ReconciliationJob(job_id=str(uuid4()), owner_id=owner_id,
                document_version_id=version_id, embedding_key=self.embedding_key,
                desired_revision=1, cursor=None, attempt=1, state='pending',
                error_code=None, created_at=now)
            self._write(lambda repo: repo.commit_parsed(reservation.reservation_id,
                reservation.generation, owner_id=owner_id, document=version,
                canonical=parsed.canonical, spans=parsed.spans, chunks=chunks,
                provenance=provenance, embedding_inputs=inputs, job=job,
                format_input=document_input))

        self._renew(owner_id, reservation.reservation_id, reservation.generation)
        stored = self._read(lambda repo: (
            repo.get_canonical_for_version(owner_id, version_id),
            repo.list_chunks(version_id),
            repo.load_embedding_inputs(owner_id,
                [c.chunk_id for c in repo.list_chunks(version_id)], embedding_key=self.embedding_key),
            repo.get_latest_reconciliation_job(owner_id, version_id, self.embedding_key),
        ))
        canonical, chunks, embedding_inputs, latest_job = stored
        chunks = sorted(chunks, key=lambda item: item.chunk_id)
        if canonical is None or not chunks or set(embedding_inputs) != {c.chunk_id for c in chunks}:
            raise IngestionStageError('legacy_insufficient')
        if not newly_parsed:
            desired = (latest_job.desired_revision + 1) if latest_job else 1
            latest_job = ReconciliationJob(job_id=str(uuid4()), owner_id=owner_id,
                document_version_id=version_id, embedding_key=self.embedding_key,
                desired_revision=desired, cursor=None, attempt=1, state='pending',
                error_code=None, created_at=datetime.now(timezone.utc))
            self._write(lambda repo: repo.create_reconciliation_job(
                latest_job, reservation_id=reservation.reservation_id,
                generation=reservation.generation))

        current_job = latest_job
        running = current_job.model_copy(update={'state': 'running', 'error_code': None})
        self._write(lambda repo: repo.checkpoint_job(running,
            expected_attempt=current_job.attempt, expected_cursor=current_job.cursor,
            reservation_id=reservation.reservation_id, generation=reservation.generation))
        self._write(lambda repo: repo.transition_reservation(
            reservation.reservation_id, reservation.generation, owner_id=owner_id,
            phase='indexing'))

        decisions = self._read(lambda repo: repo.get_effective_decisions(
            owner_id, [item.chunk_id for item in chunks]))
        latest_events = self._read(lambda repo: {item.chunk_id: repo.list_assessments(owner_id,item.chunk_id)
            for item in chunks})
        revisions = self._read(lambda repo: repo.get_assessment_revisions(
            owner_id, [item.chunk_id for item in chunks]))
        maps = self._read(lambda repo: repo.load_chunk_provenance(
            owner_id, [item.chunk_id for item in chunks]))
        if set(maps) != {item.chunk_id for item in chunks} or any(
                m.mapping_status != 'exact' for m in maps.values()):
            raise IngestionStageError('legacy_insufficient')

        from .reconciliation import Reconciler
        projection=Reconciler(self.engine,self.processor)
        prepared = []
        for item in chunks:
            self._renew(owner_id, reservation.reservation_id, reservation.generation)
            decision = decisions.get(item.chunk_id)
            tail = latest_events[item.chunk_id][-1] if latest_events[item.chunk_id] else None
            if decision is None or (tail is not None and tail.stage == 'decision'
                                    and tail.outcome == 'unavailable'):
                assessment = self._assess(owner_id, item, embedding_inputs[item.chunk_id],
                    reservation, attempt_id, revisions[item.chunk_id])
                decision = assessment.decision
                decisions[item.chunk_id] = decision
                revisions[item.chunk_id] = decision.revision
            if decision.allowed:
                record = embedding_inputs[item.chunk_id]
                formatted = document_input(record.title, list(record.section_context), item.text)
                record.validate_input(item.text, formatted)
                self._renew(owner_id, reservation.reservation_id, reservation.generation)
                vector = self.model.embed_document(record.title, list(record.section_context), item.text)
                self._renew(owner_id, reservation.reservation_id, reservation.generation)
                effective_chunk = item
            else:
                vector = None
                safe_reason = 'bloqueado_por_decisao_auditavel'
                effective_chunk = item.model_copy(update={
                    'injection_flagged': True, 'injection_reason': safe_reason})
            prepared.append((item,decision,embedding_inputs[item.chunk_id]))
            self._renew(owner_id, reservation.reservation_id, reservation.generation)
            self.index.upsert(effective_chunk, vector, owner_id=owner_id,
                decision_event_id=decision.decision_event_id,decision_revision=decision.revision,
                input_sha256=embedding_inputs[item.chunk_id].input_sha256)
            # Mesma verificação autoritativa da reconciliação; antes de ack.
            projection._verify_batch(owner_id,version_id,[prepared[-1]])
            self._renew(owner_id, reservation.reservation_id, reservation.generation)
            self._write(lambda repo, item=item, decision=decision: repo.record_index_ack(
                owner_id, item.chunk_id, self.embedding_key,
                reservation_id=reservation.reservation_id, generation=reservation.generation,
                decision_revision=decision.revision))
            updated = current_job.model_copy(update={'cursor': item.chunk_id, 'state': 'running'})
            self._write(lambda repo, updated=updated, current_job=current_job: repo.checkpoint_job(
                updated, expected_attempt=current_job.attempt, expected_cursor=current_job.cursor,
                reservation_id=reservation.reservation_id, generation=reservation.generation))
            current_job = updated

        self._renew(owner_id, reservation.reservation_id, reservation.generation)
        for start in range(0,len(prepared),128):
            projection._verify_batch(owner_id,version_id,prepared[start:start+128])
        completed = current_job.model_copy(update={'state': 'complete', 'error_code': None})
        self._write(lambda repo: repo.checkpoint_job(completed,
            expected_attempt=current_job.attempt, expected_cursor=current_job.cursor,
            reservation_id=reservation.reservation_id, generation=reservation.generation))
        self._write(lambda repo: repo.publish_ready(reservation.reservation_id,
            reservation.generation, owner_id=owner_id, embedding_key=self.embedding_key))

    def _assess(self, owner_id, chunk, embedding_record, reservation, attempt_id, revision):
        value = '\n'.join([embedding_record.title, *embedding_record.section_context, chunk.text])
        digest = sha256(value.encode('utf-8')).hexdigest()
        prior = self._read(lambda repo: repo.list_assessments(owner_id, chunk.chunk_id))
        supersedes = next((event.event_id for event in reversed(prior) if event.stage == 'decision'), None)
        triage_id = str(uuid4()); now = datetime.now(timezone.utc)
        suspect = suspicious_reason(value)
        precise = high_precision_reason(value)
        rules = (suspect.split(':', 1)[-1],) if suspect else ()
        triage = IngestionAuditEvent(event_id=triage_id, attempt_id=attempt_id,
            owner_id=owner_id, document_version_id=chunk.document_version_id,
            chunk_id=chunk.chunk_id, sequence=revision + 1, stage='triage',
            policy_version=POLICY_VERSION, input_sha256=digest,
            heuristic_version=HEURISTIC_VERSION, heuristic_rule_ids=rules,
            judge_metadata=None, outcome='suspect' if suspect else 'clean',
            reason_code='suspected' if suspect else 'no_suspicion', created_at=now,
            triage_event_id=None, judge_event_id=None, supersedes_decision_id=None)
        self._write(lambda repo: repo.append_assessment([triage], revision,
            evaluated_input=value, reservation_id=reservation.reservation_id,
            generation=reservation.generation))
        if not suspect:
            return self._append_decision(owner_id, chunk, value, reservation, attempt_id,
                revision + 1, triage_id, None, 'allowed', 'no_suspicion', supersedes)
        if precise:
            return self._append_decision(owner_id, chunk, value, reservation, attempt_id,
                revision + 1, triage_id, None, 'blocked', 'rule_block', supersedes)

        spec = None
        try:
            spec = self.model.injection_judge_spec()
            verdict = self.model.judge_injection(value)
            self._renew(owner_id, reservation.reservation_id, reservation.generation)
            outcome = 'injection' if verdict.injection else 'safe'
            metadata = JudgeMetadata(provider=spec['provider'], model=spec['model'],
                config_version=spec['config_version'], config_sha256=spec['config_sha256'],
                prompt_version=spec['prompt_version'], prompt_sha256=spec['prompt_sha256'],
                request_id=None, result=outcome, error_kind=None)
        except Exception as exc:
            if isinstance(exc, IngestionLeaseLost):
                raise
            self._renew(owner_id, reservation.reservation_id, reservation.generation)
            kind = self._failure_category(exc)[0]
            # Sem metadado verdadeiro não se fabrica evento de juiz. A decisão fica
            # indisponível; evento de triagem sozinho não concede autorização.
            if not isinstance(exc, InjectionJudgeError) and not hasattr(self.model, 'injection_judge_spec'):
                kind = 'configuration'
            if spec is not None:
                metadata = JudgeMetadata(provider=spec['provider'], model=spec['model'],
                    config_version=spec['config_version'], config_sha256=spec['config_sha256'],
                    prompt_version=spec['prompt_version'], prompt_sha256=spec['prompt_sha256'],
                    request_id=None, result='error', error_kind=kind)
                event = self._judge_event(owner_id, chunk, attempt_id, revision + 2, triage_id,
                    digest, metadata, rules)
                decision = self._decision_event(owner_id, chunk, attempt_id, revision + 3, triage_id,
                    event.event_id, digest, 'unavailable', 'judge_unavailable', supersedes, rules)
                self._write(lambda repo: repo.append_assessment([event, decision], revision + 1,
                    evaluated_input=value, reservation_id=reservation.reservation_id,
                    generation=reservation.generation))
            raise IngestionStageError(kind,
                status_code=getattr(exc, 'status_code', None),
                retryable=getattr(exc, 'retryable', None)) from None

        event = self._judge_event(owner_id, chunk, attempt_id, revision + 2, triage_id,
            digest, metadata, rules)
        if outcome == 'safe':
            result = self._decision_event(owner_id, chunk, attempt_id, revision + 3, triage_id,
                event.event_id, digest, 'allowed', 'judge_release', supersedes, rules)
            effective = True
        else:
            result = self._decision_event(owner_id, chunk, attempt_id, revision + 3, triage_id,
                event.event_id, digest, 'blocked', 'judge_block', supersedes, rules)
            effective = False
        decision = self._write(lambda repo: repo.append_assessment([event, result],
            revision + 1, evaluated_input=value, reservation_id=reservation.reservation_id,
            generation=reservation.generation))
        return _Assessment(decision, None if effective else 'judge_block')

    def _append_decision(self, owner_id, chunk, value, reservation, attempt_id, revision,
                         triage_id, judge_id, outcome, reason, supersedes):
        event = self._decision_event(owner_id, chunk, attempt_id, revision + 1, triage_id,
            judge_id, sha256(value.encode('utf-8')).hexdigest(), outcome, reason, supersedes,
            (suspicious_reason(value).split(':',1)[-1],) if suspicious_reason(value) else ())
        decision = self._write(lambda repo: repo.append_assessment([event], revision,
            evaluated_input=value, reservation_id=reservation.reservation_id,
            generation=reservation.generation))
        return _Assessment(decision, reason if outcome == 'blocked' else None)

    @staticmethod
    def _judge_event(owner_id, chunk, attempt_id, sequence, triage_id, input_hash, metadata, rule_ids):
        result = metadata.result
        reason = {'safe': 'judge_release', 'injection': 'judge_block',
                  'error': 'judge_unavailable'}[result]
        return IngestionAuditEvent(event_id=str(uuid4()), attempt_id=attempt_id,
            owner_id=owner_id, document_version_id=chunk.document_version_id,
            chunk_id=chunk.chunk_id, sequence=sequence, stage='judge',
            policy_version=POLICY_VERSION, input_sha256=input_hash,
            heuristic_version=HEURISTIC_VERSION, heuristic_rule_ids=rule_ids,
            judge_metadata=metadata, outcome=result, reason_code=reason,
            created_at=datetime.now(timezone.utc), triage_event_id=triage_id,
            judge_event_id=None, supersedes_decision_id=None)

    @staticmethod
    def _decision_event(owner_id, chunk, attempt_id, sequence, triage_id, judge_id,
                        input_hash, outcome, reason, supersedes, rule_ids):
        return IngestionAuditEvent(event_id=str(uuid4()), attempt_id=attempt_id,
            owner_id=owner_id, document_version_id=chunk.document_version_id,
            chunk_id=chunk.chunk_id, sequence=sequence, stage='decision',
            policy_version=POLICY_VERSION, input_sha256=input_hash,
            heuristic_version=HEURISTIC_VERSION, heuristic_rule_ids=rule_ids,
            judge_metadata=None, outcome=outcome, reason_code=reason,
            created_at=datetime.now(timezone.utc), triage_event_id=triage_id,
            judge_event_id=judge_id, supersedes_decision_id=supersedes)

    def reclassify(self, owner_id, version_id):
        attempt_id = str(uuid4())
        self._read(lambda repo: repo._owned_version(owner_id, version_id))
        chunks = self._read(lambda repo: repo.list_chunks(version_id))
        inputs = self._read(lambda repo: repo.load_embedding_inputs(owner_id,
            [c.chunk_id for c in chunks], embedding_key=self.embedding_key))
        if not chunks:
            return {}
        if set(inputs) != {c.chunk_id for c in chunks}:
            provenance = self._read(lambda repo: repo.load_chunk_provenance(
                owner_id, [c.chunk_id for c in chunks]))
            for item in chunks:
                if item.chunk_id not in inputs:
                    if item.chunk_id not in provenance:
                        self._write(lambda repo, item=item: repo.mark_legacy_insufficient(
                            owner_id, item.chunk_id, created_at=datetime.now(timezone.utc)))
                    revision = self._read(lambda repo, item=item: repo.get_assessment_revisions(
                        owner_id, [item.chunk_id])[item.chunk_id])
                    self._append_legacy_unavailable(owner_id, item, attempt_id, revision)
        current = self._read(lambda repo: repo.get_effective_decisions(owner_id,
            [c.chunk_id for c in chunks]))
        result = {}
        for item in chunks:
            if item.chunk_id not in inputs:
                result[item.chunk_id] = self._read(lambda repo, item=item: repo.get_effective_decisions(
                    owner_id, [item.chunk_id]).get(item.chunk_id))
                continue
            revision = self._read(lambda repo, item=item: repo.get_assessment_revisions(
                owner_id, [item.chunk_id])[item.chunk_id])
            # Reclassification is append-only and not a reservation publication.
            result[item.chunk_id] = self._assess_unfenced(owner_id, item,
                inputs[item.chunk_id], attempt_id, revision, current.get(item.chunk_id))
        return result

    def _assess_unfenced(self, owner_id, chunk, embedding_record, attempt_id, revision, previous):
        value='\n'.join([embedding_record.title,*embedding_record.section_context,chunk.text])
        digest=sha256(value.encode()).hexdigest();triage_id=str(uuid4())
        suspect=suspicious_reason(value);precise=high_precision_reason(value)
        rules=(suspect.split(':',1)[-1],) if suspect else ()
        triage=IngestionAuditEvent(event_id=triage_id,attempt_id=attempt_id,owner_id=owner_id,
            document_version_id=chunk.document_version_id,chunk_id=chunk.chunk_id,sequence=revision+1,
            stage='triage',policy_version=POLICY_VERSION,input_sha256=digest,
            heuristic_version=HEURISTIC_VERSION,heuristic_rule_ids=rules,judge_metadata=None,
            outcome='suspect' if suspect else 'clean',reason_code='suspected' if suspect else 'no_suspicion',
            created_at=datetime.now(timezone.utc),triage_event_id=None,judge_event_id=None,
            supersedes_decision_id=None)
        self._write(lambda repo:repo.append_assessment([triage],revision,evaluated_input=value))
        prior=self._read(lambda repo:repo.list_assessments(owner_id,chunk.chunk_id))
        previous_id=next((event.event_id for event in reversed(prior) if event.stage=='decision'),None)
        if not suspect:
            event=self._decision_event(owner_id,chunk,attempt_id,revision+2,triage_id,None,digest,
                'allowed','no_suspicion',previous_id,rules)
            return self._write(lambda repo:repo.append_assessment([event],revision+1,evaluated_input=value))
        if precise:
            event=self._decision_event(owner_id,chunk,attempt_id,revision+2,triage_id,None,digest,
                'blocked','rule_block',previous_id,rules)
            return self._write(lambda repo:repo.append_assessment([event],revision+1,evaluated_input=value))
        spec=self.model.injection_judge_spec()
        try:
            verdict=self.model.judge_injection(value)
        except Exception as exc:
            kind=self._failure_category(exc)[0]
            metadata=JudgeMetadata(provider=spec['provider'],model=spec['model'],
                config_version=spec['config_version'],config_sha256=spec['config_sha256'],
                prompt_version=spec['prompt_version'],prompt_sha256=spec['prompt_sha256'],
                request_id=None,result='error',error_kind=kind)
            judge=self._judge_event(owner_id,chunk,attempt_id,revision+2,triage_id,digest,metadata,rules)
            unavailable=self._decision_event(owner_id,chunk,attempt_id,revision+3,triage_id,
                judge.event_id,digest,'unavailable','judge_unavailable',previous_id,rules)
            self._write(lambda repo:repo.append_assessment([judge,unavailable],revision+1,
                evaluated_input=value))
            raise IngestionStageError(kind,
                status_code=getattr(exc,'status_code',None),
                retryable=getattr(exc,'retryable',None)) from None
        outcome='injection' if verdict.injection else 'safe'
        metadata=JudgeMetadata(provider=spec['provider'],model=spec['model'],
            config_version=spec['config_version'],config_sha256=spec['config_sha256'],
            prompt_version=spec['prompt_version'],prompt_sha256=spec['prompt_sha256'],
            request_id=None,result=outcome,error_kind=None)
        judge=self._judge_event(owner_id,chunk,attempt_id,revision+2,triage_id,digest,metadata,rules)
        reason='judge_block' if verdict.injection else 'judge_release'
        decision=self._decision_event(owner_id,chunk,attempt_id,revision+3,triage_id,judge.event_id,digest,
            'blocked' if verdict.injection else 'allowed',reason,previous_id,rules)
        return self._write(lambda repo:repo.append_assessment([judge,decision],revision+1,evaluated_input=value))

    def _append_legacy_unavailable(self, owner_id, chunk, attempt_id, revision):
        """Revoke old effective access with an honest text-only insufficient-context decision."""
        value = chunk.text
        digest = sha256(value.encode('utf-8')).hexdigest()
        suspect = suspicious_reason(value)
        rule_ids = (suspect.split(':', 1)[-1],) if suspect else ()
        triage_id = str(uuid4())
        triage = IngestionAuditEvent(event_id=triage_id, attempt_id=attempt_id,
            owner_id=owner_id, document_version_id=chunk.document_version_id,
            chunk_id=chunk.chunk_id, sequence=revision+1, stage='triage',
            policy_version=POLICY_VERSION, input_sha256=digest,
            heuristic_version=HEURISTIC_VERSION, heuristic_rule_ids=rule_ids,
            judge_metadata=None, outcome='suspect' if suspect else 'clean',
            reason_code='suspected' if suspect else 'no_suspicion',
            created_at=datetime.now(timezone.utc), triage_event_id=None,
            judge_event_id=None, supersedes_decision_id=None)
        prior = self._read(lambda repo: repo.list_assessments(owner_id, chunk.chunk_id))
        supersedes = next((event.event_id for event in reversed(prior) if event.stage == 'decision'), None)
        decision = self._decision_event(owner_id,chunk,attempt_id,revision+2,triage_id,None,
            digest,'unavailable','legacy_insufficient',supersedes,rule_ids)
        self._write(lambda repo:repo.append_assessment([triage,decision],revision,
            evaluated_input=value))

    def _ready_result(self, owner_id, version_id, reused):
        def operation(repo):
            chunks=repo.list_chunks(version_id)
            revisions=repo.get_assessment_revisions(owner_id,[c.chunk_id for c in chunks])
            authorized=repo.authorize_candidates(owner_id,[c.chunk_id for c in chunks],revisions)
            version=repo._owned_version(owner_id,version_id)
            return version,[authorized[c.chunk_id] for c in chunks if c.chunk_id in authorized]
        version,chunks=self._read(operation)
        from .core import IngestionResult
        return IngestionResult(version,chunks,reused)

    @staticmethod
    def _failure_category(exc):
        if isinstance(exc, IngestionStageError):
            return exc.category, exc.status_code, exc.retryable
        if isinstance(exc, InjectionJudgeError):
            category = exc.kind if exc.kind in {'provider','invalid_output','timeout','configuration'} else 'configuration'
            return category, exc.status_code, exc.retryable
        if isinstance(exc, TimeoutError):
            return 'timeout', None, True
        if isinstance(exc, PermissionError):
            return 'conflict', None, False
        if isinstance(exc, (OSError,)):
            return 'storage', None, None
        return 'storage', None, None

    def _record_failure(self, owner_id, reservation, category):
        def operation(repo):
            job=repo.get_latest_reconciliation_job(owner_id,reservation.document_version_id,
                                                   self.embedding_key)
            if job is not None and job.state in {'pending','running'}:
                retry=job.model_copy(update={'state':'retry','error_code':category})
                repo.checkpoint_job(retry,expected_attempt=job.attempt,expected_cursor=job.cursor,
                    reservation_id=reservation.reservation_id,generation=reservation.generation)
            repo.transition_reservation(reservation.reservation_id,reservation.generation,
                owner_id=owner_id,phase='failed',error_code=category)
        self._write(operation)


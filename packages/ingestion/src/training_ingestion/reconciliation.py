"""Fenced, resumable projection repair from authoritative lineage into Qdrant."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import uuid4

from qdrant_client import models
from training_contracts.ingestao import OrphanInventoryEntry, ReconciliationJob
from training_lineage import LineageRepository
from training_llm import document_input


class ReconciliationError(RuntimeError):
    """Sanitized projection failure."""


class ReconciliationLeaseLost(ReconciliationError):
    """The worker lost its reservation generation."""


class Reconciler:
    BATCH_SIZE = 128
    INVENTORY_MAX_AGE_SECONDS = 300

    def __init__(self, engine, processor, *, lease_seconds=900):
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError('Lease de reconciliação inválida')
        self.engine, self.processor, self.index = engine, processor, processor.index
        self.model = processor.model_client
        self.embedding_key = self.index.settings.embedding_key
        self.lease_seconds = lease_seconds

    def _read(self, fn):
        with self.engine.connect() as c:
            return fn(LineageRepository(c))

    def _write(self, fn):
        with self.engine.begin() as c:
            return fn(LineageRepository(c))

    @staticmethod
    def _lease_lost(exc):
        return isinstance(exc, (ValueError, PermissionError))

    def _renew(self, owner, reservation):
        try:
            self._write(lambda r: r.renew(reservation.reservation_id, reservation.generation,
                owner_id=owner, lease_seconds=self.lease_seconds))
        except (ValueError, PermissionError):
            raise ReconciliationLeaseLost('Reserva de reconciliação obsoleta') from None

    def reconcile_version(self, owner_id, version_id):
        attempt_id = str(uuid4())
        status, reservation, job = self._write(lambda r: r.begin_reconciliation(
            owner_id, version_id, self.embedding_key, attempt_id, lease_seconds=self.lease_seconds))
        if status == 'busy':
            raise ReconciliationError('Versão ocupada por outra geração')
        try:
            job_running = job.model_copy(update={'state':'running','error_code':None})
            self._write(lambda r: r.checkpoint_job(job_running, expected_attempt=job.attempt,
                expected_cursor=None, reservation_id=reservation.reservation_id,
                generation=reservation.generation))
            self._write(lambda r: r.transition_reservation(reservation.reservation_id,
                reservation.generation, owner_id=owner_id, phase='indexing'))

            # Reclassification appends a new assessment; it never changes the historic chunk.
            chunks=[]; read_cursor=None
            while True:
                page=self._read(lambda r,cursor=read_cursor: r.list_reconciliation_chunks(
                    owner_id,version_id,cursor=cursor,limit=self.BATCH_SIZE))
                if not page:
                    break
                chunks.extend(page); read_cursor=page[-1].chunk_id
            decisions = self._read(lambda r: r.get_effective_decisions(owner_id,[c.chunk_id for c in chunks]))
            missing_decision = [c.chunk_id for c in chunks if c.chunk_id not in decisions]
            if missing_decision:
                self.processor.model_client  # Fail before any Qdrant mutation if judge is unavailable.
                self._write(lambda r: r.renew(reservation.reservation_id,reservation.generation,
                    owner_id=owner_id,lease_seconds=self.lease_seconds))
                # Protocol's public reclassification path is the canonical assessment writer.
                from .protocol import DurableIngestionProtocol
                protocol = getattr(self.processor, 'protocol', None)
                if protocol is None:
                    protocol = DurableIngestionProtocol(self.engine, None, None, self.processor, 1)
                protocol.reclassify(owner_id, version_id)
                decisions = self._read(lambda r: r.get_effective_decisions(owner_id,[c.chunk_id for c in chunks]))

            maps = self._read(lambda r: r.load_chunk_provenance(owner_id,[c.chunk_id for c in chunks]))
            inputs = self._read(lambda r: r.load_embedding_inputs(owner_id,[c.chunk_id for c in chunks],
                embedding_key=self.embedding_key))
            revisions = self._read(lambda r: r.get_assessment_revisions(owner_id,[c.chunk_id for c in chunks]))
            if not chunks:
                raise ReconciliationError('Versão sem chunks reconciliáveis')

            cursor = None
            for start in range(0,len(chunks),self.BATCH_SIZE):
                batch = chunks[start:start+self.BATCH_SIZE]
                prepared=[]; blocked_ids=[]
                for chunk in batch:
                    decision=decisions.get(chunk.chunk_id)
                    if decision is None:
                        raise ReconciliationError('Decisão efetiva indisponível')
                    mapping=maps.get(chunk.chunk_id)
                    entry=inputs.get(chunk.chunk_id)
                    if mapping is None or mapping.mapping_status!='exact':
                        self._write(lambda r, c=chunk: r.mark_legacy_insufficient(owner_id,c.chunk_id,
                            created_at=datetime.now(timezone.utc)))
                        self._write(lambda r, c=chunk: self._append_legacy_unavailable(r,owner_id,c,attempt_id))
                        self._fail(owner_id,reservation,job,'legacy_insufficient')
                        return {'state':'blocked','error_code':'legacy_insufficient',
                                'generation':reservation.generation,'job_id':job.job_id}
                    if decision.allowed and entry is None:
                        self._fail(owner_id,reservation,job,'legacy_insufficient')
                        return {'state':'blocked','error_code':'legacy_insufficient',
                                'generation':reservation.generation,'job_id':job.job_id}
                    self._renew(owner_id,reservation)
                    if decision.allowed:
                        formatted=document_input(entry.title,list(entry.section_context),chunk.text)
                        entry.validate_input(chunk.text,formatted)
                        vector=self.model.embed_document(entry.title,list(entry.section_context),chunk.text)
                        self._renew(owner_id,reservation)
                        input_hash=entry.input_sha256
                        self.index.upsert(chunk,vector,owner_id=owner_id,
                            decision_event_id=decision.decision_event_id,decision_revision=decision.revision,
                            input_sha256=input_hash)
                    else:
                        blocked_ids.append(chunk.chunk_id)
                    prepared.append((chunk,decision,entry))
                self.index.delete_owned_points(owner_id=owner_id,version_id=version_id,chunk_ids=blocked_ids)
                self._renew(owner_id,reservation)
                self._verify_batch(owner_id,version_id,prepared)
                for chunk,decision,entry in prepared:
                    self._renew(owner_id,reservation)
                    self._write(lambda r,c=chunk,d=decision: r.record_index_ack(owner_id,c.chunk_id,
                        self.embedding_key,reservation_id=reservation.reservation_id,
                        generation=reservation.generation,decision_revision=d.revision))
                    cursor=chunk.chunk_id
                updated=job_running.model_copy(update={'cursor':cursor,'state':'running'})
                self._write(lambda r,u=updated,old=job_running: r.checkpoint_job(u,
                    expected_attempt=old.attempt,expected_cursor=old.cursor,
                    reservation_id=reservation.reservation_id,generation=reservation.generation))
                job_running=updated
            completed=job_running.model_copy(update={'state':'complete','error_code':None})
            self._write(lambda r:r.checkpoint_job(completed,expected_attempt=job_running.attempt,
                expected_cursor=job_running.cursor,reservation_id=reservation.reservation_id,
                generation=reservation.generation))
            ready=self._write(lambda r:r.publish_ready(reservation.reservation_id,reservation.generation,
                owner_id=owner_id,embedding_key=self.embedding_key))
            return {'state':'complete','generation':ready.generation,'job_id':job.job_id,
                    'chunks':len(chunks),'desired_revision':job.desired_revision}
        except ReconciliationLeaseLost:
            raise
        except Exception as exc:
            category = getattr(exc,'category',None) or ('legacy_insufficient' if str(exc)=='legacy_insufficient' else 'storage')
            try:
                self._fail(owner_id,reservation,job,category)
            except Exception:
                pass
            if isinstance(exc,ReconciliationError):
                raise
            if self._lease_lost(exc):
                raise ReconciliationLeaseLost('Reserva de reconciliação obsoleta') from None
            raise ReconciliationError(category) from None

    def _fail(self, owner, reservation, job, category):
        def op(repo):
            current=repo.get_reconciliation_job(owner,job.job_id)
            if current and current.state in {'pending','running'}:
                failed=current.model_copy(update={'state':'blocked' if category=='legacy_insufficient' else 'retry',
                    'error_code':category})
                repo.checkpoint_job(failed,expected_attempt=current.attempt,expected_cursor=current.cursor,
                    reservation_id=reservation.reservation_id,generation=reservation.generation)
            repo.transition_reservation(reservation.reservation_id,reservation.generation,
                owner_id=owner,phase='failed',error_code=category)
        self._write(op)

    def _verify_batch(self, owner, version, prepared):
        ids=[self.index.point_id(c.chunk_id) for c,_,_ in prepared]
        points=self.index.client.retrieve(self.index.collection_name,ids,with_payload=True,with_vectors=True)
        found={str(p.id):p for p in points}
        if len(found)!=len(points) or not set(found).issubset(ids):
            raise ReconciliationError('Resposta de índice divergente')
        for chunk,decision,entry in prepared:
            point=found.get(self.index.point_id(chunk.chunk_id))
            if not decision.allowed:
                if point is not None:
                    raise ReconciliationError('Chunk bloqueado continua no índice')
                continue
            if point is None:
                raise ReconciliationError('Ponto reconciliado ausente')
            expected={**chunk.model_dump(),'owner_id':owner,'embedding_key':self.embedding_key,
                'decision_event_id':decision.decision_event_id,'decision_revision':decision.revision,
                'input_sha256':entry.input_sha256}
            if point.payload!=expected:
                raise ReconciliationError('Payload divergente')
            vectors=point.vector or {}
            dense=vectors.get('dense') if isinstance(vectors,dict) else None
            sparse=vectors.get('bm25') if isinstance(vectors,dict) else None
            if not isinstance(dense,list) or len(dense)!=3072 or not any(dense) or not isinstance(sparse,models.SparseVector):
                raise ReconciliationError('Vetores divergentes')

    @staticmethod
    def _append_legacy_unavailable(repo, owner, chunk, attempt):
        from training_contracts.ingestao import IngestionAuditEvent
        revision=repo.get_assessment_revisions(owner,[chunk.chunk_id])[chunk.chunk_id]
        value=chunk.text;digest=sha256(value.encode()).hexdigest();now=datetime.now(timezone.utc)
        prior=repo.list_assessments(owner,chunk.chunk_id)
        supersedes=next((e.event_id for e in reversed(prior) if e.stage=='decision'),None)
        triage_id=str(uuid4())
        triage=IngestionAuditEvent(event_id=triage_id,attempt_id=attempt,owner_id=owner,
            document_version_id=chunk.document_version_id,chunk_id=chunk.chunk_id,sequence=revision+1,
            stage='triage',policy_version='ingestion-policy-v3',input_sha256=digest,
            heuristic_version='heuristica-injecao-v3',heuristic_rule_ids=(),judge_metadata=None,
            outcome='clean',reason_code='no_suspicion',created_at=now,triage_event_id=None,
            judge_event_id=None,supersedes_decision_id=None)
        decision=IngestionAuditEvent(event_id=str(uuid4()),attempt_id=attempt,owner_id=owner,
            document_version_id=chunk.document_version_id,chunk_id=chunk.chunk_id,sequence=revision+2,
            stage='decision',policy_version='ingestion-policy-v3',input_sha256=digest,
            heuristic_version='heuristica-injecao-v3',heuristic_rule_ids=(),judge_metadata=None,
            outcome='unavailable',reason_code='legacy_insufficient',created_at=now,
            triage_event_id=triage_id,judge_event_id=None,supersedes_decision_id=supersedes)
        repo.append_assessment([triage,decision],revision,evaluated_input=value)

    @staticmethod
    def payload_digest(payload):
        return sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def fixture_manifest_digest(manifest):
        body={k:v for k,v in manifest.items() if k!='digest'}
        return sha256(json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

    def inventory_orphans(self, *, run_id, source_db_identity, fixture_manifest=None):
        entries=[];offset=None;now=datetime.now(timezone.utc)
        proven=(fixture_manifest is not None and fixture_manifest.get('run_id')==run_id
            and fixture_manifest.get('collection')==self.index.collection_name
            and fixture_manifest.get('source_db_identity')==source_db_identity
            and fixture_manifest.get('digest')==self.fixture_manifest_digest(fixture_manifest))
        points_map=fixture_manifest.get('points',{}) if proven else {}
        while True:
            points,offset=self.index.client.scroll(self.index.collection_name,offset=offset,limit=128,
                with_payload=True,with_vectors=False)
            for point in points:
                payload=point.payload or {};digest=self.payload_digest(payload);point_id=str(point.id)
                classification='fixture_proven' if points_map.get(point_id)==digest else 'unknown'
                entries.append(OrphanInventoryEntry(inventory_id=str(uuid4()),point_id=point_id,
                    collection=self.index.collection_name,owner_id=payload.get('owner_id'),
                    version_id=payload.get('document_version_id'),observed_at=now,payload_digest=digest,
                    source_db_identity=source_db_identity,fixture_manifest_ref=fixture_manifest.get('manifest_ref') if classification=='fixture_proven' else None,
                    classification=classification))
            if offset is None: break
        digest=sha256('|'.join(sorted(f'{e.point_id}:{e.payload_digest}' for e in entries)).encode()).hexdigest()
        return {'run_id':run_id,'collection':self.index.collection_name,'source_db_identity':source_db_identity,
            'observed_at':now,'entries':tuple(entries),'digest':digest}

    def execute_orphan_deletion(self, inventory, *, fixture_manifest, quiescence_check=None, authorization=None):
        if not callable(quiescence_check) or not callable(authorization):
            raise ReconciliationError('Quiescência e autorização explícitas obrigatórias')
        now=datetime.now(timezone.utc)
        age=(now-inventory['observed_at']).total_seconds()
        expected=fixture_manifest.get('digest')==self.fixture_manifest_digest(fixture_manifest)
        scope={'run_id':inventory['run_id'],'collection':inventory['collection'],
            'source_db_identity':inventory['source_db_identity'],'digest':inventory['digest']}
        if (age<0 or age>self.INVENTORY_MAX_AGE_SECONDS or not expected
                or inventory['collection']!=self.index.collection_name
                or fixture_manifest.get('run_id')!=inventory['run_id']
                or fixture_manifest.get('collection')!=inventory['collection']
                or fixture_manifest.get('source_db_identity')!=inventory['source_db_identity']
                or not quiescence_check(scope) or not authorization(scope)):
            raise ReconciliationError('Inventário expirado ou sem autorização')
        current=self.inventory_orphans(run_id=inventory['run_id'],source_db_identity=inventory['source_db_identity'],fixture_manifest=fixture_manifest)
        if current['digest']!=inventory['digest']:
            raise ReconciliationError('Inventário mudou desde a observação')
        deletable=[e for e in inventory['entries'] if e.classification=='fixture_proven']
        if len(deletable)!=len(inventory['entries']):
            raise ReconciliationError('Inventário inclui pontos sem prova de fixture')
        for entry in deletable:
            point=self.index.client.retrieve(self.index.collection_name,[entry.point_id],with_payload=True,with_vectors=False)
            if not point or self.payload_digest(point[0].payload or {})!=entry.payload_digest:
                raise ReconciliationError('Ponto de fixture mudou')
            self.index.client.delete(self.index.collection_name,[entry.point_id],wait=True)
        return len(deletable)

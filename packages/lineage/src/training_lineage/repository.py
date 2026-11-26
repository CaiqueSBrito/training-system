"""Repositório transacional; os valores de saída sempre passam pelos contratos."""

from datetime import timezone, timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import Connection, select, func
from sqlalchemy.dialects.postgresql import insert
from pydantic import ValidationError
from training_contracts.linhagem import ChunkRecord, DocumentVersionRecord
from training_contracts.ingestao import (
    CanonicalTextRecord, CanonicalSpan, ChunkProvenanceRecord, EmbeddingInputRecord,
    IngestionAuditEvent, EffectiveChunkDecision, IngestionReservation, ReconciliationJob,
    validate_canonical_spans, validate_chunk_provenance, resolve_citation_spans,
)

from .tables import (chunk, document_version, canonical_text, canonical_span,
    chunk_provenance, embedding_input, ingestion_audit_event, effective_chunk_decision,
    ingestion_reservation, reconciliation_job, index_ack)


def _validated(model, value):
    try:
        return model.model_validate(value.model_dump() if isinstance(value, model) else value)
    except ValidationError:
        raise ValueError('Registro de linhagem inválido') from None


def _from_row(model, row):
    return _validated(model, {name: row[name] for name in model.model_fields})


class LineageRepository:
    """Usa a transação da Connection fornecida; nunca faz commit implicitamente."""

    def __init__(self, connection: Connection):
        self.connection = connection

    def insert_document_version(self, record: DocumentVersionRecord) -> None:
        validated = DocumentVersionRecord.model_validate(record.model_dump())
        values = validated.model_dump()
        if validated.created_at.tzinfo is None:
            values["created_at"] = validated.created_at.replace(tzinfo=timezone.utc)
        self.connection.execute(document_version.insert().values(**values))

    def insert_chunk(self, record: ChunkRecord) -> None:
        validated = ChunkRecord.model_validate(record.model_dump())
        self.connection.execute(chunk.insert().values(**validated.model_dump()))

    def get_document_version(self, document_version_id: str) -> DocumentVersionRecord | None:
        row = self.connection.execute(select(document_version).where(
            document_version.c.document_version_id == document_version_id,
        )).mappings().one_or_none()
        return DocumentVersionRecord.model_validate(dict(row)) if row is not None else None

    def find_document_version(self, owner_id: str, sha256: str) -> DocumentVersionRecord | None:
        row = self.connection.execute(select(document_version).where(
            document_version.c.owner_id == owner_id, document_version.c.sha256 == sha256,
        )).mappings().one_or_none()
        return DocumentVersionRecord.model_validate(dict(row)) if row is not None else None

    def get_chunk(self, chunk_id: str) -> ChunkRecord | None:
        row = self.connection.execute(select(chunk).where(chunk.c.chunk_id == chunk_id)).mappings().one_or_none()
        return ChunkRecord.model_validate(dict(row)) if row is not None else None

    def list_chunks(self, document_version_id: str) -> list[ChunkRecord]:
        """Página (ausentes por último), offset inicial/final e ID como desempate."""
        rows = self.connection.execute(select(chunk).where(
            chunk.c.document_version_id == document_version_id,
        ).order_by(chunk.c.page.asc().nulls_last(), chunk.c.start_offset,
                   chunk.c.end_offset, chunk.c.chunk_id)).mappings()
        return [ChunkRecord.model_validate(dict(row)) for row in rows]

    def _owned_version(self, owner_id, version_id, *, lock=False):
        query=select(document_version).where(document_version.c.owner_id==owner_id,
                                             document_version.c.document_version_id==version_id)
        if lock:
            query=query.with_for_update()
        row=self.connection.execute(query).mappings().one_or_none()
        if row is None:
            raise PermissionError('Versão inacessível')
        return _from_row(DocumentVersionRecord,row)

    def _owned_chunk(self, owner_id, chunk_id, *, lock=False, shared=False):
        query=select(chunk).select_from(chunk.join(document_version,
            chunk.c.document_version_id==document_version.c.document_version_id)).where(chunk.c.chunk_id==chunk_id,
                                                       document_version.c.owner_id==owner_id)
        if lock or shared:
            query=query.with_for_update(read=shared,of=chunk)
        row=self.connection.execute(query).mappings().one_or_none()
        if row is None:
            raise PermissionError('Chunk inacessível')
        return _from_row(ChunkRecord,row)

    def _put_history(self, table, record, key):
        row=self.connection.execute(select(table).where(table.c[key]==getattr(record,key))).mappings().one_or_none()
        if row is not None:
            if _from_row(type(record),row)!=record:
                raise ValueError('Identidade histórica divergente')
            return
        self.connection.execute(table.insert().values(**record.model_dump()))

    def persist_canonical(self, owner_id, record, spans, chunks, provenance, embedding_inputs, *, format_input):
        """Unidade curta/atômica; formatter real fornecido pelo consumidor, sem I/O remoto."""
        record=_validated(CanonicalTextRecord,record)
        version=self._owned_version(owner_id,record.document_version_id)
        if record.parser_version!=version.parser_version:
            raise ValueError('Parser canônico divergente da versão')
        spans=tuple(_validated(CanonicalSpan,s) for s in spans)
        chunks=tuple(_validated(ChunkRecord,c) for c in chunks)
        maps=tuple(_validated(ChunkProvenanceRecord,m) for m in provenance)
        inputs=tuple(_validated(EmbeddingInputRecord,i) for i in embedding_inputs)
        validate_canonical_spans(record,spans)
        chunk_map={c.chunk_id:c for c in chunks}
        if not chunks or len(chunk_map)!=len(chunks) or len({m.chunk_id for m in maps})!=len(maps) or set(chunk_map)!={m.chunk_id for m in maps}:
            raise ValueError('Conjunto de chunks e mapas divergente')
        for mapping in maps:
            validate_chunk_provenance(record,spans,chunk_map[mapping.chunk_id],mapping)
        if {i.chunk_id for i in inputs}!=set(chunk_map) or len({i.input_id for i in inputs})!=len(inputs):
            raise ValueError('Entradas de embedding incompletas')
        for item in inputs:
            value=chunk_map[item.chunk_id].text
            item.validate_input(value,format_input(item.title,list(item.section_context),value))
        with self.connection.begin_nested():
            self._put_history(canonical_text,record,'canonical_id')
            for span in spans:
                self._put_history(canonical_span,span,'span_id')
            for item in chunks:
                self._put_history(chunk,item,'chunk_id')
            for item in maps:
                self._put_history(chunk_provenance,item,'chunk_id')
            for item in inputs:
                self._put_history(embedding_input,item,'input_id')

    def get_canonical(self, owner_id, canonical_id):
        row=self.connection.execute(select(canonical_text).select_from(canonical_text.join(document_version,
            canonical_text.c.document_version_id==document_version.c.document_version_id)).where(
            canonical_text.c.canonical_id==canonical_id,document_version.c.owner_id==owner_id)).mappings().one_or_none()
        return _from_row(CanonicalTextRecord,row) if row else None

    def get_canonical_for_version(self, owner_id, version_id):
        self._owned_version(owner_id,version_id)
        rows=self.connection.execute(select(canonical_text).where(
            canonical_text.c.document_version_id==version_id)).mappings().all()
        if len(rows)>1:
            raise ValueError('Seleção de receita canônica ambígua')
        return _from_row(CanonicalTextRecord,rows[0]) if rows else None

    def has_persisted_artifacts(self, owner_id, version_id):
        if self.get_document_version(version_id) is None:
            return False
        canonical=self.get_canonical_for_version(owner_id,version_id)
        if canonical is None:
            return False
        ids=[c.chunk_id for c in self.list_chunks(version_id)]
        maps=self.load_chunk_provenance(owner_id,ids)
        inputs=self.load_embedding_inputs(owner_id,ids)
        return bool(ids) and set(maps)==set(ids) and set(inputs)==set(ids) and all(
            m.mapping_status=='exact' and m.canonical_id==canonical.canonical_id for m in maps.values())

    def list_canonical_spans(self, owner_id, canonical_id):
        if self.get_canonical(owner_id,canonical_id) is None:
            raise PermissionError('Fonte canônica inacessível')
        return tuple(_from_row(CanonicalSpan,r) for r in self.connection.execute(
            select(canonical_span).where(canonical_span.c.canonical_id==canonical_id)
            .order_by(canonical_span.c.start)).mappings())

    def load_chunk_provenance(self, owner_id, chunk_ids):
        chunk_ids=tuple(dict.fromkeys(chunk_ids))
        if len(chunk_ids)>128:
            return {key:value for start in range(0,len(chunk_ids),128)
                    for key,value in self.load_chunk_provenance(owner_id,chunk_ids[start:start+128]).items()}
        if not chunk_ids:
            return {}
        rows=self.connection.execute(select(chunk_provenance).select_from(chunk_provenance.join(chunk,
            chunk_provenance.c.chunk_id==chunk.c.chunk_id).join(document_version,
            chunk.c.document_version_id==document_version.c.document_version_id)).where(
            document_version.c.owner_id==owner_id,chunk.c.chunk_id.in_(chunk_ids))).mappings()
        return {r['chunk_id']:_from_row(ChunkProvenanceRecord,r) for r in rows}

    def resolve_citation(self, owner_id, selection):
        from training_contracts.ingestao import CitationSpanSelection
        selection=_validated(CitationSpanSelection,selection)
        item=self._owned_chunk(owner_id,selection.chunk_id)
        mapping=self.load_chunk_provenance(owner_id,[item.chunk_id]).get(item.chunk_id)
        if mapping is None or mapping.mapping_status!='exact':
            raise ValueError('Fonte canônica insuficiente')
        canonical=self.get_canonical(owner_id,mapping.canonical_id)
        return resolve_citation_spans(selection,canonical,
            self.list_canonical_spans(owner_id,canonical.canonical_id),item,mapping)

    def mark_legacy_insufficient(self, owner_id, chunk_id, *, created_at):
        self._owned_chunk(owner_id,chunk_id)
        existing=self.load_chunk_provenance(owner_id,[chunk_id]).get(chunk_id)
        if existing is not None:
            if existing.mapping_status!='insufficient':
                raise ValueError('Proveniência histórica já é exata')
            return existing
        record=ChunkProvenanceRecord(chunk_id=chunk_id,canonical_id=None,start=None,end=None,
            span_ids=(),overlap_ranges=(),mapping_status='insufficient',created_at=created_at)
        self._put_history(chunk_provenance,record,'chunk_id')
        return record

    def load_embedding_inputs(self, owner_id, chunk_ids, *, embedding_key=None):
        chunk_ids=tuple(dict.fromkeys(chunk_ids))
        if len(chunk_ids)>128:
            return {key:value for start in range(0,len(chunk_ids),128)
                    for key,value in self.load_embedding_inputs(owner_id,chunk_ids[start:start+128],embedding_key=embedding_key).items()}
        if not chunk_ids:
            return {}
        query=select(embedding_input).select_from(embedding_input.join(chunk,
            embedding_input.c.chunk_id==chunk.c.chunk_id).join(document_version,
            chunk.c.document_version_id==document_version.c.document_version_id)).where(
            document_version.c.owner_id==owner_id,chunk.c.chunk_id.in_(chunk_ids))
        if embedding_key is not None:
            query=query.where(embedding_input.c.embedding_key==embedding_key)
        result={}
        for row in self.connection.execute(query).mappings():
            if row['chunk_id'] in result:
                raise ValueError('Chave de embedding ambígua')
            result[row['chunk_id']]=_from_row(EmbeddingInputRecord,row)
        return result

    def reproduce_embedding_input(self, owner_id, chunk_id, embedding_key, *, format_input):
        item=self._owned_chunk(owner_id,chunk_id)
        record=self.load_embedding_inputs(owner_id,[chunk_id],embedding_key=embedding_key).get(chunk_id)
        if record is None:
            raise ValueError('Entrada histórica de embedding insuficiente')
        formatted=format_input(record.title,list(record.section_context),item.text)
        record.validate_input(item.text,formatted)
        return formatted

    def get_assessment_revisions(self, owner_id, chunk_ids):
        chunk_ids=tuple(dict.fromkeys(chunk_ids))
        if len(chunk_ids)>128:
            return {key:value for start in range(0,len(chunk_ids),128)
                    for key,value in self.get_assessment_revisions(owner_id,chunk_ids[start:start+128]).items()}
        if not chunk_ids:
            return {}
        rows=self.connection.execute(select(chunk.c.chunk_id,func.coalesce(func.max(ingestion_audit_event.c.sequence),0)).select_from(chunk)
            .join(document_version).outerjoin(ingestion_audit_event,ingestion_audit_event.c.chunk_id==chunk.c.chunk_id)
            .where(document_version.c.owner_id==owner_id,chunk.c.chunk_id.in_(chunk_ids))
            .group_by(chunk.c.chunk_id))
        return {r[0]:r[1] for r in rows}

    def append_assessment(self, events, expected_revision, *, evaluated_input,
                          reservation_id=None, generation=None):
        """CAS = última sequence (inclui cadeia parcial), não apenas última decisão."""
        events=tuple(_validated(IngestionAuditEvent,e) for e in events)
        if not events or type(expected_revision) is not int or expected_revision<0:
            raise ValueError('Avaliação vazia ou revisão inválida')
        first=events[0]
        input_hash=sha256(evaluated_input.encode('utf-8')).hexdigest()
        if any(e.chunk_id!=first.chunk_id or e.owner_id!=first.owner_id or e.document_version_id!=first.document_version_id
               or e.input_sha256!=input_hash for e in events):
            raise ValueError('Cadeia ou hash da avaliação divergente')
        if any(e.sequence!=expected_revision+i+1 for i,e in enumerate(events)):
            raise ValueError('Revisão obsoleta ou sequência divergente')
        if (reservation_id is None)!=(generation is None):
            raise ValueError('Fencing incompleto')
        with self.connection.begin_nested():
            if reservation_id is not None:
                reservation=self._active_reservation(first.owner_id,reservation_id,generation)
                if reservation.document_version_id!=first.document_version_id:
                    raise ValueError('Versão da reserva divergente')
            item=self._owned_chunk(first.owner_id,first.chunk_id,lock=True)
            if item.document_version_id!=first.document_version_id:
                raise ValueError('Versão do chunk divergente')
            if self.get_assessment_revisions(first.owner_id,[first.chunk_id])[first.chunk_id]!=expected_revision:
                raise ValueError('Revisão obsoleta')
            for event in events:
                self.connection.execute(ingestion_audit_event.insert().values(**event.model_dump()))
            last=events[-1]
            if last.stage=='decision':
                effective=EffectiveChunkDecision(chunk_id=last.chunk_id,owner_id=last.owner_id,
                    decision_event_id=last.event_id,revision=last.sequence,
                    allowed=last.outcome=='allowed',policy_version=last.policy_version)
                values={**effective.model_dump(),'document_version_id':last.document_version_id}
                statement=insert(effective_chunk_decision).values(**values)
                self.connection.execute(statement.on_conflict_do_update(index_elements=['chunk_id'],set_=values))
                return effective
        return None

    def list_assessments(self, owner_id, chunk_id):
        self._owned_chunk(owner_id,chunk_id)
        return tuple(_from_row(IngestionAuditEvent,r) for r in self.connection.execute(
            select(ingestion_audit_event).where(ingestion_audit_event.c.chunk_id==chunk_id,
                ingestion_audit_event.c.owner_id==owner_id).order_by(ingestion_audit_event.c.sequence)).mappings())

    def get_effective_decisions(self, owner_id, chunk_ids):
        chunk_ids=tuple(dict.fromkeys(chunk_ids))
        if len(chunk_ids)>128:
            return {key:value for start in range(0,len(chunk_ids),128)
                    for key,value in self.get_effective_decisions(owner_id,chunk_ids[start:start+128]).items()}
        if not chunk_ids:
            return {}
        heads=(select(ingestion_audit_event.c.chunk_id.label('id'),
            func.max(ingestion_audit_event.c.sequence).label('revision')).where(
                ingestion_audit_event.c.owner_id==owner_id,ingestion_audit_event.c.chunk_id.in_(chunk_ids))
            .group_by(ingestion_audit_event.c.chunk_id).subquery())
        source=effective_chunk_decision.join(heads,
            (heads.c.id==effective_chunk_decision.c.chunk_id)&(heads.c.revision==effective_chunk_decision.c.revision))
        source=source.join(chunk,chunk.c.chunk_id==effective_chunk_decision.c.chunk_id).join(document_version,
            chunk.c.document_version_id==document_version.c.document_version_id)
        rows=self.connection.execute(select(effective_chunk_decision).select_from(source)
            .where(effective_chunk_decision.c.owner_id==owner_id,document_version.c.owner_id==owner_id,
                   effective_chunk_decision.c.chunk_id.in_(chunk_ids))).mappings()
        return {r['chunk_id']:_from_row(EffectiveChunkDecision,r) for r in rows}

    def _now(self):
        return self.connection.scalar(select(func.clock_timestamp()))

    def get_reservation(self, owner_id, reservation_id):
        row=self.connection.execute(select(ingestion_reservation).where(
            ingestion_reservation.c.reservation_id==reservation_id,ingestion_reservation.c.owner_id==owner_id)).mappings().one_or_none()
        return _from_row(IngestionReservation,row) if row else None

    def get_reservation_for_version(self, owner_id, version_id):
        row=self.connection.execute(select(ingestion_reservation).where(
            ingestion_reservation.c.document_version_id==version_id,
            ingestion_reservation.c.owner_id==owner_id)).mappings().one_or_none()
        return _from_row(IngestionReservation,row) if row else None

    def _active_reservation(self, owner_id, reservation_id, generation):
        row=self.connection.execute(select(ingestion_reservation).where(
            ingestion_reservation.c.reservation_id==reservation_id,ingestion_reservation.c.owner_id==owner_id)
            .with_for_update()).mappings().one_or_none()
        if row is None:
            raise PermissionError('Reserva inacessível')
        record=_from_row(IngestionReservation,row)
        if record.generation!=generation or record.lease_until<=self._now() or record.phase in ('failed','ready'):
            raise ValueError('Reserva obsoleta ou expirada')
        return record

    def reserve(self, owner_id, source_sha256, attempt_id, *, lease_seconds=300):
        if type(lease_seconds) is not int or lease_seconds<=0:
            raise ValueError('Lease inválida')
        with self.connection.begin_nested():
            previous=self.find_document_version(owner_id,source_sha256)
            candidate=IngestionReservation(reservation_id=str(uuid4()),owner_id=owner_id,
                source_sha256=source_sha256,document_version_id=previous.document_version_id if previous else str(uuid4()),
                phase='reserved',generation=1,lease_until=self._now()+timedelta(seconds=lease_seconds),
                attempt_id=attempt_id,error_code=None)
            created=self.connection.execute(insert(ingestion_reservation).values(**candidate.model_dump())
                .on_conflict_do_nothing(index_elements=['owner_id','source_sha256'])
                .returning(ingestion_reservation.c.reservation_id)).scalar_one_or_none()
            row=self.connection.execute(select(ingestion_reservation).where(
                ingestion_reservation.c.owner_id==owner_id,ingestion_reservation.c.source_sha256==source_sha256)
                .with_for_update()).mappings().one()
            record=_from_row(IngestionReservation,row)
            if created:
                return 'acquired',record
            if record.phase=='ready' and self._version_complete(owner_id,record.document_version_id,record.generation):
                return 'ready',record
            if record.phase not in ('ready','failed') and record.lease_until>self._now():
                return ('acquired' if record.attempt_id==attempt_id else 'busy'),record
            phase='persisted' if self.has_persisted_artifacts(owner_id,record.document_version_id) else 'reserved'
            values=dict(phase=phase,generation=record.generation+1,
                lease_until=self._now()+timedelta(seconds=lease_seconds),attempt_id=attempt_id,error_code=None)
            self.connection.execute(ingestion_reservation.update().where(
                ingestion_reservation.c.reservation_id==record.reservation_id,
                ingestion_reservation.c.generation==record.generation).values(**values))
            return 'acquired',self.get_reservation(owner_id,record.reservation_id)

    def renew(self, reservation_id, generation, *, owner_id, lease_seconds=300):
        if type(lease_seconds) is not int or lease_seconds<=0:
            raise ValueError('Lease inválida')
        self._active_reservation(owner_id,reservation_id,generation)
        changed=self.connection.execute(ingestion_reservation.update().where(
            ingestion_reservation.c.reservation_id==reservation_id,ingestion_reservation.c.generation==generation,
            ingestion_reservation.c.lease_until>func.clock_timestamp())
            .values(lease_until=self._now()+timedelta(seconds=lease_seconds)))
        if changed.rowcount!=1:
            raise ValueError('Reserva obsoleta ou expirada')
        return self.get_reservation(owner_id,reservation_id)

    def transition_reservation(self, reservation_id, generation, *, owner_id, phase, error_code=None):
        current=self._active_reservation(owner_id,reservation_id,generation)
        transitions={'reserved':{'parsing','failed'},'parsing':{'persisted','failed'},
                     'persisted':{'indexing','failed'},'indexing':{'failed'}}
        if phase not in transitions.get(current.phase,set()):
            raise ValueError('Transição de reserva inválida')
        updated=_validated(IngestionReservation,current.model_copy(update={'phase':phase,'error_code':error_code}))
        self.connection.execute(ingestion_reservation.update().where(
            ingestion_reservation.c.reservation_id==reservation_id,ingestion_reservation.c.generation==generation)
            .values(phase=updated.phase,error_code=updated.error_code))
        return updated

    def commit_parsed(self, reservation_id, generation, *, owner_id, document, canonical,
                      spans, chunks, provenance, embedding_inputs, job, format_input):
        document=_validated(DocumentVersionRecord,document)
        with self.connection.begin_nested():
            reservation=self._active_reservation(owner_id,reservation_id,generation)
            if (document.document_version_id,document.owner_id,document.sha256)!=(
                    reservation.document_version_id,owner_id,reservation.source_sha256):
                raise ValueError('Documento não corresponde à reserva')
            if reservation.phase not in ('parsing','persisted'):
                raise ValueError('Fase de persistência inválida')
            self._put_history(document_version,document,'document_version_id')
            self.persist_canonical(owner_id,canonical,spans,chunks,provenance,embedding_inputs,format_input=format_input)
            self.create_reconciliation_job(job,reservation_id=reservation_id,generation=generation)
            self.connection.execute(ingestion_reservation.update().where(
                ingestion_reservation.c.reservation_id==reservation_id,ingestion_reservation.c.generation==generation)
                .values(phase='persisted',error_code=None))

    def create_reconciliation_job(self, record, *, reservation_id, generation):
        record=_validated(ReconciliationJob,record)
        reservation=self._active_reservation(record.owner_id,reservation_id,generation)
        if reservation.document_version_id!=record.document_version_id:
            raise ValueError('Job fora da reserva')
        self._owned_version(record.owner_id,record.document_version_id,lock=True)
        existing=self.get_reconciliation_job(record.owner_id,record.job_id)
        if existing:
            if existing!=record:
                raise ValueError('Identidade de job divergente')
            return existing
        maximum=self.connection.scalar(select(func.max(reconciliation_job.c.desired_revision)).where(
            reconciliation_job.c.document_version_id==record.document_version_id,
            reconciliation_job.c.embedding_key==record.embedding_key))
        if maximum is not None and record.desired_revision<=maximum:
            raise ValueError('Revisão de job obsoleta')
        self.connection.execute(reconciliation_job.insert().values(**record.model_dump(),
                                                                  reservation_id=reservation_id,generation=generation))
        return record

    def get_reconciliation_job(self, owner_id, job_id):
        row=self.connection.execute(select(reconciliation_job).where(
            reconciliation_job.c.job_id==job_id,reconciliation_job.c.owner_id==owner_id)).mappings().one_or_none()
        return _from_row(ReconciliationJob,row) if row else None

    def get_latest_reconciliation_job(self, owner_id, version_id, embedding_key):
        """Return the highest desired revision for one owner-scoped projection."""
        self._owned_version(owner_id,version_id)
        row=self.connection.execute(select(reconciliation_job).where(
            reconciliation_job.c.owner_id==owner_id,
            reconciliation_job.c.document_version_id==version_id,
            reconciliation_job.c.embedding_key==embedding_key)
            .order_by(reconciliation_job.c.desired_revision.desc()).limit(1)).mappings().one_or_none()
        return _from_row(ReconciliationJob,row) if row else None

    def begin_reconciliation(self, owner_id, version_id, embedding_key, attempt_id, *, lease_seconds=300):
        """Fence a new projection run and create its durable monotonic job in one short transaction."""
        if type(lease_seconds) is not int or not 1<=lease_seconds<=3600:
            raise ValueError('Lease de reconciliação inválida')
        self._owned_version(owner_id,version_id,lock=True)
        with self.connection.begin_nested():
            row=self.connection.execute(select(ingestion_reservation).where(
                ingestion_reservation.c.owner_id==owner_id,
                ingestion_reservation.c.document_version_id==version_id).with_for_update()).mappings().one_or_none()
            now=self._now()
            if row is None:
                version=self._owned_version(owner_id,version_id)
                reservation=IngestionReservation(reservation_id=str(uuid4()),owner_id=owner_id,
                    source_sha256=version.sha256,document_version_id=version_id,phase='persisted',
                    generation=1,lease_until=now+timedelta(seconds=lease_seconds),
                    attempt_id=attempt_id,error_code=None)
                self.connection.execute(ingestion_reservation.insert().values(**reservation.model_dump()))
            else:
                reservation=_from_row(IngestionReservation,row)
                if reservation.phase not in ('ready','failed') and reservation.lease_until>now:
                    return 'busy',reservation,None
                values={'generation':reservation.generation+1,'phase':'persisted',
                    'lease_until':now+timedelta(seconds=lease_seconds),
                    'attempt_id':attempt_id,'error_code':None}
                changed=self.connection.execute(ingestion_reservation.update().where(
                    ingestion_reservation.c.reservation_id==reservation.reservation_id,
                    ingestion_reservation.c.generation==reservation.generation).values(**values))
                if changed.rowcount!=1:
                    return 'busy',reservation,None
                reservation=self.get_reservation(owner_id,reservation.reservation_id)
            latest=self.get_latest_reconciliation_job(owner_id,version_id,embedding_key)
            job=ReconciliationJob(job_id=str(uuid4()),owner_id=owner_id,
                document_version_id=version_id,embedding_key=embedding_key,
                desired_revision=(latest.desired_revision+1) if latest else 1,
                cursor=None,attempt=1,state='pending',error_code=None,created_at=now)
            self.create_reconciliation_job(job,reservation_id=reservation.reservation_id,
                                           generation=reservation.generation)
            return 'acquired',reservation,job

    def find_owned_chunk(self, owner_id, version_id, chunk_id):
        """Return a chunk only when all three identifiers belong to this owner."""
        row=self.connection.execute(select(chunk).select_from(chunk.join(document_version,
            chunk.c.document_version_id==document_version.c.document_version_id)).where(
            document_version.c.owner_id==owner_id,
            document_version.c.document_version_id==version_id,
            chunk.c.chunk_id==chunk_id)).mappings().one_or_none()
        return _from_row(ChunkRecord,row) if row else None

    def checkpoint_job(self, record, *, expected_attempt, expected_cursor, reservation_id, generation):
        record=_validated(ReconciliationJob,record)
        reservation=self._active_reservation(record.owner_id,reservation_id,generation)
        if reservation.document_version_id!=record.document_version_id:
            raise ValueError('Job fora da reserva')
        current=self.connection.execute(select(reconciliation_job).where(
            reconciliation_job.c.job_id==record.job_id,reconciliation_job.c.owner_id==record.owner_id)
            .with_for_update()).mappings().one_or_none()
        if current is None or current['attempt']!=expected_attempt or current['cursor']!=expected_cursor:
            raise ValueError('Checkpoint obsoleto')
        previous=_from_row(ReconciliationJob,current)
        if any(getattr(previous,k)!=getattr(record,k) for k in ('document_version_id','embedding_key','desired_revision','created_at')):
            raise ValueError('Identidade do job divergente')
        if record.attempt not in (expected_attempt,expected_attempt+1):
            raise ValueError('Tentativa de job inválida')
        if previous.cursor is not None and (record.cursor is None or record.cursor<previous.cursor):
            raise ValueError('Cursor regressivo')
        maximum=self.connection.scalar(select(func.max(reconciliation_job.c.desired_revision)).where(
            reconciliation_job.c.document_version_id==record.document_version_id,
            reconciliation_job.c.embedding_key==record.embedding_key))
        if maximum!=record.desired_revision:
            raise ValueError('Revisão de job obsoleta')
        if record.cursor is not None:
            cursor=self._owned_chunk(record.owner_id,record.cursor)
            if cursor.document_version_id!=record.document_version_id or (previous.cursor is not None and record.cursor<previous.cursor):
                raise ValueError('Cursor fora do escopo ou regressivo')
        if record.state=='complete' and not self._projection_complete(record.owner_id,record.document_version_id,
                                                                      generation,record.embedding_key):
            raise ValueError('Projeção incompleta ou revisão obsoleta')
        self.connection.execute(reconciliation_job.update().where(reconciliation_job.c.job_id==record.job_id,
            reconciliation_job.c.attempt==expected_attempt,reconciliation_job.c.cursor.is_(None) if expected_cursor is None
            else reconciliation_job.c.cursor==expected_cursor).values(**record.model_dump(),reservation_id=reservation_id,generation=generation))
        return record

    def list_reconciliation_chunks(self, owner_id, version_id, *, cursor=None, limit=128):
        self._owned_version(owner_id,version_id)
        if type(limit) is not int or not 0<limit<=1000:
            raise ValueError('Lote inválido')
        query=select(chunk).where(chunk.c.document_version_id==version_id)
        if cursor is not None:
            last=self._owned_chunk(owner_id,cursor)
            if last.document_version_id!=version_id:
                raise ValueError('Cursor fora da versão')
            query=query.where(chunk.c.chunk_id>cursor)
        return [_from_row(ChunkRecord,r) for r in self.connection.execute(query.order_by(chunk.c.chunk_id).limit(limit)).mappings()]

    def record_index_ack(self, owner_id, chunk_id, embedding_key, *, reservation_id, generation, decision_revision):
        reservation=self._active_reservation(owner_id,reservation_id,generation)
        item=self._owned_chunk(owner_id,chunk_id,lock=True)
        decision=self.get_effective_decisions(owner_id,[chunk_id]).get(chunk_id)
        if item.document_version_id!=reservation.document_version_id or decision is None or decision.revision!=decision_revision:
            raise ValueError('Confirmação obsoleta ou fora da reserva')
        event=self.connection.execute(select(ingestion_audit_event).where(
            ingestion_audit_event.c.event_id==decision.decision_event_id)).mappings().one()
        if event['outcome'] not in ('allowed','blocked'):
            raise ValueError('Decisão indisponível')
        entry=self.load_embedding_inputs(owner_id,[chunk_id],embedding_key=embedding_key).get(chunk_id)
        if decision.allowed and entry is None:
            raise ValueError('Entrada de embedding insuficiente')
        values=dict(chunk_id=chunk_id,owner_id=owner_id,document_version_id=item.document_version_id,
            embedding_key=embedding_key,decision_event_id=decision.decision_event_id,decision_revision=decision.revision,
            generation=generation,reservation_id=reservation_id,input_id=entry.input_id if decision.allowed else None,
            acknowledged_at=self._now())
        statement=insert(index_ack).values(**values)
        self.connection.execute(statement.on_conflict_do_update(index_elements=['chunk_id','embedding_key'],set_=values))

    def _projection_complete(self, owner_id, version_id, generation, embedding_key):
        self._owned_version(owner_id,version_id)
        chunks=list(self.connection.execute(select(chunk.c.chunk_id).where(chunk.c.document_version_id==version_id)
                                            .with_for_update(read=True)).scalars())
        if not chunks:
            return False
        decisions=self.get_effective_decisions(owner_id,chunks)
        maps=self.load_chunk_provenance(owner_id,chunks)
        entries=self.load_embedding_inputs(owner_id,chunks,embedding_key=embedding_key)
        receipts={r['chunk_id']:r for r in self.connection.execute(select(index_ack).where(
            index_ack.c.owner_id==owner_id,index_ack.c.document_version_id==version_id,
            index_ack.c.embedding_key==embedding_key,index_ack.c.generation==generation)).mappings()}
        outcomes=dict(self.connection.execute(select(ingestion_audit_event.c.event_id,ingestion_audit_event.c.outcome)
            .where(ingestion_audit_event.c.event_id.in_([d.decision_event_id for d in decisions.values()]))).all())
        for chunk_id in chunks:
            decision=decisions.get(chunk_id); receipt=receipts.get(chunk_id)
            if decision is None or receipt is None or maps.get(chunk_id) is None or maps[chunk_id].mapping_status!='exact':
                return False
            if outcomes.get(decision.decision_event_id) not in ('allowed','blocked'):
                return False
            if receipt['decision_revision']!=decision.revision or receipt['decision_event_id']!=decision.decision_event_id:
                return False
            if decision.allowed and (chunk_id not in entries or receipt['input_id']!=entries[chunk_id].input_id):
                return False
        return True

    def _version_complete(self, owner_id, version_id, generation, embedding_key=None):
        query=select(reconciliation_job).where(reconciliation_job.c.owner_id==owner_id,
            reconciliation_job.c.document_version_id==version_id)
        if embedding_key is not None:
            query=query.where(reconciliation_job.c.embedding_key==embedding_key)
        jobs=self.connection.execute(query.order_by(reconciliation_job.c.desired_revision.desc())).mappings().all()
        if not jobs:
            return False
        latest={}
        for job in jobs:
            latest.setdefault(job['embedding_key'],job)
        return all(j['state']=='complete' and j['generation']==generation and
            self._projection_complete(owner_id,version_id,generation,key) for key,j in latest.items())

    def publish_ready(self, reservation_id, generation, *, owner_id, embedding_key):
        current=self._active_reservation(owner_id,reservation_id,generation)
        has_key=self.connection.scalar(select(reconciliation_job.c.job_id).where(
            reconciliation_job.c.owner_id==owner_id,reconciliation_job.c.document_version_id==current.document_version_id,
            reconciliation_job.c.embedding_key==embedding_key).limit(1))
        if current.phase!='indexing' or not has_key or not self._version_complete(owner_id,current.document_version_id,generation):
            raise ValueError('Publicação incompleta ou obsoleta')
        changed=self.connection.execute(ingestion_reservation.update().where(
            ingestion_reservation.c.reservation_id==reservation_id,ingestion_reservation.c.generation==generation,
            ingestion_reservation.c.lease_until>func.clock_timestamp())
            .values(phase='ready',error_code=None))
        if changed.rowcount!=1:
            raise ValueError('Reserva obsoleta ou expirada')
        return self.get_reservation(owner_id,reservation_id)

    def authorize_candidates(self, owner_id, candidate_ids, observed_revision=None):
        """Barreira V2: reserva FOR SHARE antes de chunks FOR SHARE, até commit.

        Escritores fenced (reserve/publish/index_ack/assessment) já adquirem a
        reserva antes dos chunks. Não inverter essa ordem no chamador. Todas as
        reservas são travadas antes de qualquer chunk, inclusive em lotes >128.
        observed_revision=None revalida a revisão atual sob os mesmos locks
        (E12); o dict explícito continua exigindo a revisão vista na recuperação.
        """
        candidate_ids=tuple(sorted(set(candidate_ids)))
        if not candidate_ids:
            return {}
        # Descobrir versões não requer lock: chunks e versões são imutáveis.
        versions=set()
        for start in range(0,len(candidate_ids),128):
            versions.update(self.connection.execute(select(chunk.c.document_version_id).select_from(
                chunk.join(document_version,chunk.c.document_version_id==document_version.c.document_version_id))
                .where(document_version.c.owner_id==owner_id,chunk.c.chunk_id.in_(candidate_ids[start:start+128])))
                .scalars())
        reservations={}
        version_ids=sorted(versions)
        for start in range(0,len(version_ids),128):
            for row in self.connection.execute(select(ingestion_reservation).where(
                    ingestion_reservation.c.owner_id==owner_id,
                    ingestion_reservation.c.document_version_id.in_(version_ids[start:start+128]))
                    .order_by(ingestion_reservation.c.document_version_id)
                    .with_for_update(read=True)).mappings():
                reservations[row['document_version_id']]=row
        rows=[]
        for start in range(0,len(candidate_ids),128):
            rows.extend(self.connection.execute(select(chunk).select_from(chunk.join(document_version,
                chunk.c.document_version_id==document_version.c.document_version_id)).where(
                document_version.c.owner_id==owner_id,chunk.c.chunk_id.in_(candidate_ids[start:start+128]))
                .order_by(chunk.c.chunk_id).with_for_update(read=True,of=chunk)).mappings().all())
        decisions=self.get_effective_decisions(owner_id,[r['chunk_id'] for r in rows])
        ready_versions={}
        result={}
        for row in rows:
            item=_from_row(ChunkRecord,row); decision=decisions.get(item.chunk_id)
            if decision is None or not decision.allowed or (
                    observed_revision is not None and observed_revision.get(item.chunk_id)!=decision.revision):
                continue
            if item.document_version_id not in ready_versions:
                reservation=reservations.get(item.document_version_id)
                ready_versions[item.document_version_id]=bool(reservation and reservation['phase']=='ready' and
                    self._version_complete(owner_id,item.document_version_id,reservation['generation']))
            if ready_versions[item.document_version_id]:
                result[item.chunk_id]=item
        return result

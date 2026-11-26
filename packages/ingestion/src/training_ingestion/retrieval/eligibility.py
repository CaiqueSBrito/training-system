"""Minimal safe candidate read: Qdrant proposes, Postgres authorizes."""
from qdrant_client import models
from sqlalchemy import select
from training_lineage.tables import index_ack
from training_llm import document_input
from training_lineage import LineageRepository
from ..indexing import bm25_document


class EligibilityUnavailable(RuntimeError):
    """Authoritative lineage could not safely authorize candidates."""


class SafeRetriever:
    def __init__(self, engine, processor):
        self.engine=engine
        self.processor=processor
        self.index=processor.index
        self.model=processor.model_client

    def search(self, owner_id, query, *, limit=10):
        if not isinstance(owner_id,str) or not owner_id.strip() or not isinstance(query,str) or not query.strip():
            return []
        if type(limit) is not int or not 1<=limit<=1000:
            raise ValueError('Limite de busca inválido')
        vector=self.model.embed_query(query)
        if not isinstance(vector,list) or len(vector)!=3072:
            raise ValueError('Embedding de consulta inválido')
        result=self.index.client.query_points(self.index.collection_name,
            query=vector,using='dense',limit=limit,with_payload=True,
            query_filter=models.Filter(must=[
                models.FieldCondition(key='owner_id',match=models.MatchValue(value=owner_id)),
                models.FieldCondition(key='embedding_key',match=models.MatchValue(value=self.index.settings.embedding_key)),
                models.FieldCondition(key='injection_flagged',match=models.MatchValue(value=False)),
            ]))
        return authorize_points(self.engine, self.index, owner_id, result.points)


def authorize_points(engine, index, owner_id, points):
    """Shared authoritative payload barrier; never returns Qdrant text directly."""
    candidates={}; ordered=[]
    for point in points:
        payload=point.payload or {}
        chunk_id=payload.get('chunk_id'); revision=payload.get('decision_revision')
        if (not isinstance(chunk_id,str) or type(revision) is not int
                or not payload.get('decision_event_id')
                or not isinstance(payload.get('input_sha256'),str)):
            continue
        if chunk_id in candidates:
            continue
        candidates[chunk_id]=revision;ordered.append((chunk_id,payload))
    if not candidates:
        return []
    try:
        with engine.connect() as connection:
            repo=LineageRepository(connection)
            authorized=repo.authorize_candidates(owner_id,candidates.keys(),candidates)
            decisions=repo.get_effective_decisions(owner_id,candidates.keys())
            inputs=repo.load_embedding_inputs(owner_id,candidates.keys(),
                embedding_key=index.settings.embedding_key)
            receipts={r['chunk_id']:r for r in connection.execute(select(index_ack).where(
                index_ack.c.owner_id==owner_id, index_ack.c.chunk_id.in_(candidates),
                index_ack.c.embedding_key==index.settings.embedding_key)).mappings()}
    except Exception:
        raise EligibilityUnavailable('Linhagem indisponível para autorizar candidatos') from None
    output=[]
    for chunk_id,payload in ordered:
        record=authorized.get(chunk_id)
        if record is None:
            continue
        if (payload.get('document_version_id')!=record.document_version_id
                or payload.get('text')!=record.text
                or payload.get('section_path')!=record.section_path
                or payload.get('owner_id')!=owner_id):
            continue
        decision=decisions.get(chunk_id);entry=inputs.get(chunk_id)
        if (decision is None or not decision.allowed or entry is None
                or payload.get('decision_event_id')!=decision.decision_event_id
                or payload.get('decision_revision')!=decision.revision
                or payload.get('input_sha256')!=entry.input_sha256):
            continue
        receipt=receipts.get(chunk_id)
        if (receipt is None or receipt['decision_event_id']!=decision.decision_event_id
                or receipt['decision_revision']!=decision.revision or receipt['input_id']!=entry.input_id
                or payload.get('embedding_key')!=index.settings.embedding_key):
            continue
        try:
            entry.validate_input(record.text, document_input(entry.title,list(entry.section_context),record.text))
        except ValueError:
            continue
        output.append(record)
    return output

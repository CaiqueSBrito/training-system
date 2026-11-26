"""Ingestão local; use ingest_document para executar I1–I6."""
from .core import (
    ChunkConfig, Chunker, DoclingParser, IngestionError,
    IngestionResult, Ingestor, ParsedDocument, count_tokens,
)

from .indexing import bm25_document

__all__ = ["ChunkConfig", "Chunker", "DoclingParser", "IngestionError",
           "IngestionResult", "Ingestor", "ParsedDocument", "count_tokens", "ingest_document", "bm25_document"]


def ingest_document(path, owner_id, *, engine, document_id=None, env_file='.env', config=None):
    """Entrada de produção, exige modelos reais e valida coleção antes de ingerir."""
    from .indexing import IndexingPipeline
    pipeline = IndexingPipeline.from_env(env_file)
    try:
        return Ingestor(engine, config=config, processor=pipeline).ingest(
            path, owner_id, document_id=document_id)
    finally:
        pipeline.index.client.close()
        pipeline.model_client.client.close()

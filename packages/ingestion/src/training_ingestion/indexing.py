"""I4–I6: verificação fechada em falha, embedding individual e Qdrant remoto."""
import math
import re
import unicodedata
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models
from training_contracts.linhagem import ChunkRecord
from training_llm import GeminiModels, ModelSettings


_TARGET = r'(?:instru\w*|prompt\w*|segred\w*|secret\w*|regr\w*|rule\w*|orienta\w*|diretriz\w*|guideline\w*|system message)'
_PATTERNS = (
    rf"\b(?:ignore|ignorar|desconsidere|desconsiderar|disregard|forget|esqueca|revele|revelar|mostre|exiba|reveal|show|print|output|repita|repeat|diga|liste|override|bypass|nao\s+siga|do\s+not\s+follow|don't\s+follow)\b.{{0,100}}\b{_TARGET}",
    r'\b(?:voce e|you are|aja como|act as)\b.{0,80}\b(?:assistente|assistant|modelo|model|usuario|user)\b',
    r'\b(?:a partir de agora|from now on|novas?\s+instruc\w*|nova\s+tarefa|new task|ao gerar|when generating)\b',
    r'\b(?:repita|repeat)\b.{0,50}\b(?:texto\s+(?:acima|anterior)|text\s+above)\b',
    r'\b(?:obedeca|obey)\b.{0,60}\b(?:usuario|user)\b',
)
# Estes sinais ambíguos encaminham ao juiz, sem determinar exclusão.
_ROLE = r'(?:^\s*(?:system|assistant|user|human|model|root)\s*:|###\s*(?:system|instruction)\b|\[c\d+\]|<system(?:_error)?>|\[/?inst\]|\[system\])'
_CHECKER = r'\b(?:verificador|juiz|classifier|checker)\b'
_MODEL_DELIMITER = r'(?:<\|im_(?:start|end)\|>|<(?:start|end)_of_turn>|\[/?inst\]|<<sys>>|<\|(?:system|assistant)\|>)'
_FILLERS = {'\u3164', '\u115f', '\u1160', '\u2800'}


def _normalize(value):
    # Normalização de triagem; o juiz e o consumidor recebem o original intacto.
    normalized = ''.join(c for c in unicodedata.normalize('NFKD', value.casefold())
                         if unicodedata.category(c) not in {'Mn', 'Cf'} and c not in _FILLERS)
    normalized = normalized.translate(str.maketrans({'ı': 'i', 'օ': 'o'}))
    # Soletração espaçada de palavras ("i g n o r e"), sem juntar palavras normais.
    return re.sub(r'(?<!\w)(?:[^\W\d_]\s+){2,}[^\W\d_](?!\w)',
                  lambda match: re.sub(r'\s+', '', match.group()), normalized)


def high_precision_reason(value: str) -> str | None:
    # Somente controles e delimitadores inequívocos. Cf genérico pode ser legítimo.
    if any(0xE0000 <= ord(c) <= 0xE007F for c in value):
        return 'heuristica-injecao-v3:tag-unicode'
    if any(0x202A <= ord(c) <= 0x202E or 0x2066 <= ord(c) <= 0x2069 for c in value):
        return 'heuristica-injecao-v3:controle-bidi'
    if re.search(_MODEL_DELIMITER, value, re.IGNORECASE):
        return 'heuristica-injecao-v3:delimitador-modelo'
    return None


def suspicious_reason(value: str) -> str | None:
    precise = high_precision_reason(value)
    if precise:
        return precise
    if any(unicodedata.category(c) == 'Cf' for c in value):
        return 'heuristica-injecao-v3:controle-formatacao'
    if any(c in _FILLERS for c in value):
        return 'heuristica-injecao-v3:preenchedor-invisivel'
    for word in re.findall(r'[^\W\d_]+', value):
        scripts = {name for c in word for name in ('LATIN', 'CYRILLIC', 'GREEK', 'ARMENIAN')
                   if name in unicodedata.name(c, '')}
        if 'LATIN' in scripts and scripts & {'CYRILLIC', 'GREEK', 'ARMENIAN'}:
            return 'heuristica-injecao-v3:escrita-mista'
    normalized = _normalize(value)
    if re.search(_ROLE, normalized, re.MULTILINE) or re.search(_CHECKER, normalized):
        return 'heuristica-injecao-v3:papel-ou-verificador-ambiguo'
    for number, pattern in enumerate(_PATTERNS, 1):
        if re.search(pattern, normalized, re.DOTALL):
            return f'heuristica-injecao-v3:regra-{number}'
    return None


class InjectionChecker:
    def __init__(self, judge):
        self.judge = judge

    def check(self, chunk: ChunkRecord, title: str = '') -> ChunkRecord:
        value = '\n'.join([title, *chunk.section_path, chunk.text])
        suspect = suspicious_reason(value)
        if not suspect:
            return chunk
        verdict = self.judge.judge_injection(value)
        precise = high_precision_reason(value)
        # Registrar o motivo efetivo: não atribuir ao juiz bloqueio que ele liberou.
        reason = precise
        if reason is None and verdict.injection:
            reason = f'juiz; motivo não confiável: {verdict.reason}'
        values = chunk.model_dump()
        values.update(injection_flagged=reason is not None, injection_reason=reason)
        return ChunkRecord.model_validate(values)


def bm25_document(text: str) -> models.Document:
    """Contrato compartilhado por indexação e consultas da recuperação."""
    return models.Document(text=text, model='qdrant/bm25', options={'language': 'portuguese'})


class QdrantIndex:
    def __init__(self, client, settings, *, collection_name=None):
        settings.validate_embedding()
        self.client, self.settings = client, settings
        self.collection_name = collection_name or settings.embedding_key
        if self.collection_name != settings.embedding_key:
            raise ValueError('Nome da coleção diverge da chave de embedding')

    @property
    def metadata(self):
        return {'embedding_key': self.settings.embedding_key,
                'bm25': {'model': 'qdrant/bm25', 'language': 'portuguese'}}

    def ensure_collection(self):
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                self.collection_name,
                vectors_config={'dense': models.VectorParams(size=3072, distance=models.Distance.COSINE)},
                sparse_vectors_config={'bm25': models.SparseVectorParams(modifier=models.Modifier.IDF)},
                metadata=self.metadata,
            )
        info = self.client.get_collection(self.collection_name)
        dense = info.config.params.vectors
        sparse = info.config.params.sparse_vectors
        if (not isinstance(dense, dict) or 'dense' not in dense or dense['dense'].size != 3072
                or dense['dense'].distance != models.Distance.COSINE
                or not sparse or 'bm25' not in sparse or sparse['bm25'].modifier != models.Modifier.IDF
                or info.config.metadata != self.metadata):
            raise ValueError('configuração da coleção diverge da chave de embedding/BM25')
        expected_indexes = {
            'document_version_id': models.PayloadSchemaType.KEYWORD,
            'owner_id': models.PayloadSchemaType.KEYWORD,
            'chunk_id': models.PayloadSchemaType.KEYWORD,
            'injection_flagged': models.PayloadSchemaType.BOOL,
            'decision_revision': models.PayloadSchemaType.INTEGER,
            'input_sha256': models.PayloadSchemaType.KEYWORD,
        }
        for field, schema in expected_indexes.items():
            existing = info.payload_schema.get(field)
            if existing is not None and existing.data_type != schema:
                raise ValueError('Índice de payload divergente')
            if existing is None:
                self.client.create_payload_index(self.collection_name, field,
                                                field_schema=schema, wait=True)
        actual = self.client.get_collection(self.collection_name).payload_schema
        if any(field not in actual or actual[field].data_type != schema
               for field, schema in expected_indexes.items()):
            raise ValueError('Índice de payload ausente ou divergente')

    @staticmethod
    def point_id(chunk_id):
        # Qdrant só aceita uint64 ou UUID; o SHA-256 original permanece no payload.
        return str(uuid5(NAMESPACE_URL, 'training-system:chunk:' + chunk_id))

    def upsert(self, chunk, vector, *, owner_id, decision_event_id=None,
               decision_revision=None, input_sha256=None):
        if chunk.injection_flagged:
            self.delete_owned_point(self.point_id(chunk.chunk_id), owner_id=owner_id,
                                    version_id=chunk.document_version_id, chunk_id=chunk.chunk_id)
            return
        if (vector is None or len(vector) != 3072 or not all(math.isfinite(v) for v in vector)
                or not any(vector)):
            raise ValueError('Embedding inválido para indexação')
        payload={**chunk.model_dump(), 'owner_id': owner_id, 'embedding_key': self.settings.embedding_key}
        if decision_event_id is not None:
            if type(decision_revision) is not int or decision_revision < 1 or not input_sha256:
                raise ValueError('Metadados de decisão inválidos')
            payload.update(decision_event_id=decision_event_id, decision_revision=decision_revision,
                           input_sha256=input_sha256)
        self.client.upsert(self.collection_name, points=[models.PointStruct(
            id=self.point_id(chunk.chunk_id),
            vector={'dense': vector, 'bm25': bm25_document(chunk.text)},
            payload=payload,
        )], wait=True)

    def delete_owned_point(self, point_id, *, owner_id, version_id, chunk_id):
        """Delete one point only after its complete ownership identity is verified."""
        rows=self.client.retrieve(self.collection_name,[point_id],with_payload=True,with_vectors=False)
        if not rows:
            return False
        payload=rows[0].payload or {}
        if (str(rows[0].id)!=str(point_id) or payload.get('owner_id')!=owner_id
                or payload.get('document_version_id')!=version_id or payload.get('chunk_id')!=chunk_id
                or payload.get('embedding_key')!=self.settings.embedding_key):
            raise ValueError('Ponto do índice fora do escopo autorizado')
        selector=models.FilterSelector(filter=models.Filter(must=[
            models.FieldCondition(key='owner_id',match=models.MatchValue(value=owner_id)),
            models.FieldCondition(key='document_version_id',match=models.MatchValue(value=version_id)),
            models.FieldCondition(key='chunk_id',match=models.MatchValue(value=chunk_id)),
            models.FieldCondition(key='embedding_key',match=models.MatchValue(value=self.settings.embedding_key)),
        ]))
        self.client.delete(self.collection_name,selector,wait=True)
        return True

    def delete_owned_points(self, *, owner_id, version_id, chunk_ids):
        chunk_ids=tuple(dict.fromkeys(chunk_ids))
        if not chunk_ids:
            return 0
        ids=[self.point_id(chunk_id) for chunk_id in chunk_ids]
        rows=self.client.retrieve(self.collection_name,ids,with_payload=True,with_vectors=False)
        expected={self.point_id(chunk_id):chunk_id for chunk_id in chunk_ids}
        for point in rows:
            point_id=str(point.id);payload=point.payload or {}
            if (point_id not in expected or payload.get('owner_id')!=owner_id
                    or payload.get('document_version_id')!=version_id
                    or payload.get('chunk_id')!=expected[point_id]
                    or payload.get('embedding_key')!=self.settings.embedding_key):
                raise ValueError('Ponto do índice fora do escopo autorizado')
        selector=models.FilterSelector(filter=models.Filter(must=[
            models.FieldCondition(key='owner_id',match=models.MatchValue(value=owner_id)),
            models.FieldCondition(key='document_version_id',match=models.MatchValue(value=version_id)),
            models.FieldCondition(key='embedding_key',match=models.MatchValue(value=self.settings.embedding_key)),
            models.FieldCondition(key='chunk_id',match=models.MatchAny(any=list(chunk_ids))),
        ]))
        self.client.delete(self.collection_name,selector,wait=True)
        return len(rows)

    def delete_version(self, version_id):
        self.client.delete(self.collection_name, models.FilterSelector(filter=models.Filter(must=[
            models.FieldCondition(key='document_version_id', match=models.MatchValue(value=version_id))
        ])), wait=True)


class IndexingPipeline:
    """Produção: conectar como processor do Ingestor para finalizar I1–I6."""
    def __init__(self, model_client, index):
        self.model_client, self.index = model_client, index
        self.checker = InjectionChecker(model_client)
        self.index.ensure_collection()

    @classmethod
    def from_env(cls, env_file='.env'):
        settings = ModelSettings.from_env(env_file)
        model_client = GeminiModels(settings)
        client = QdrantClient(url=settings.qdrant_url, cloud_inference=True)
        return cls(model_client, QdrantIndex(client, settings))

    def process(self, chunks, version, title):
        checked = [self.checker.check(c, title) for c in chunks]
        for c in checked:
            if not c.injection_flagged:
                vector = self.model_client.embed_document(title, c.section_path, c.text)
                self.index.upsert(c, vector, owner_id=version.owner_id)
        return checked

    def verify_existing(self, chunks, owner_id):
        # Limite fixo por requisição; a ordem de retorno do Qdrant não é garantida.
        if len({c.chunk_id for c in chunks}) != len(chunks):
            raise ValueError('Versão com chunks duplicados')
        for start in range(0, len(chunks), 128):
            batch = chunks[start:start + 128]
            ids = [self.index.point_id(c.chunk_id) for c in batch]
            points = self.index.client.retrieve(self.index.collection_name, ids,
                                               with_payload=True, with_vectors=True)
            found = {str(p.id): p for p in points}
            if len(found) != len(points) or not set(found).issubset(ids):
                raise ValueError('Resposta do índice divergente')
            for chunk, point_id in zip(batch, ids):
                point = found.get(point_id)
                if chunk.injection_flagged:
                    if point is not None:
                        raise ValueError('Versão incompleta: chunk marcado presente no índice')
                    continue
                expected = {**chunk.model_dump(), 'owner_id': owner_id,
                            'embedding_key': self.index.settings.embedding_key}
                if point is None or point.payload != expected:
                    raise ValueError('Versão incompleta ou índice divergente; reingestão exige reconciliação')
                vectors = point.vector
                dense = vectors.get('dense') if isinstance(vectors, dict) else None
                sparse = vectors.get('bm25') if isinstance(vectors, dict) else None
                if (not isinstance(dense, list) or len(dense) != 3072
                        or not all(math.isfinite(v) for v in dense) or not any(dense)
                        or not isinstance(sparse, models.SparseVector)
                        or len(sparse.indices) != len(sparse.values)
                        or not all(math.isfinite(v) for v in sparse.values)):
                    raise ValueError('Vetores do índice ausentes ou divergentes')

    def rollback(self, version_id):
        self.index.delete_version(version_id)

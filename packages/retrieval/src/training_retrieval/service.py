"""E5–E7: owner-scoped proposals, authoritative authorization, ranked context."""
from dataclasses import dataclass
from contextlib import contextmanager
from time import perf_counter
from training_observability import manual_span
from hashlib import sha256
import json
import math
from uuid import uuid4

from qdrant_client import models
from sqlalchemy import select
from training_contracts.agentes import SearchPlan, Classification
from training_contracts.comum import RetrievalMode
from training_contracts.geracao import NumberedContext, ContextChunk
from training_contracts.linhagem import ChunkRecord, RetrievalRunRecord, RetrievalCandidate
from training_ingestion.indexing import bm25_document
from training_ingestion.retrieval.eligibility import authorize_points, EligibilityUnavailable
from training_lineage.tables import document_version, chunk, canonical_text, canonical_span
from .coverage import canonical_sections


class ScopeError(ValueError):
    """Missing, inaccessible or ambiguous version/section; never guesses a scope."""


class ContinuationError(ValueError):
    """Cursor does not belong to the exact owner, scope and mode."""


class RetrievalUnavailable(RuntimeError):
    """Sanitized retrieval or model failure, never an alternative answer."""


@dataclass(frozen=True)
class RetrievalSettings:
    candidate_limit: int = 50
    top_n: int = 5

    def __post_init__(self):
        if type(self.candidate_limit) is not int or not 30 <= self.candidate_limit <= 50:
            raise ValueError('candidate_limit must be 30..50')
        if type(self.top_n) is not int or not 1 <= self.top_n <= self.candidate_limit:
            raise ValueError('top_n must be 1..candidate_limit')


@dataclass(frozen=True)
class RetrievalScope:
    document_version_ids: tuple[str, ...]
    section_prefix: tuple[str, ...] = ()
    continuation: dict | None = None

    def __post_init__(self):
        if (not isinstance(self.document_version_ids, tuple) or not self.document_version_ids
                or any(not isinstance(v,str) or not v.strip() for v in self.document_version_ids)
                or len(set(self.document_version_ids)) != len(self.document_version_ids)
                or not isinstance(self.section_prefix, tuple)
                or any(not isinstance(s,str) or not s.strip() for s in self.section_prefix)):
            raise ValueError('Explicit unique versions and exact section components required')


@dataclass(frozen=True)
class Allocation:
    document_version_id: str
    section_path: tuple[str, ...]
    count: int
    chunk_ids: tuple[str, ...]
    canonical_size: int | None = None
    canonical_id: str | None = None
    h1_id: str | None = None


@dataclass(frozen=True)
class RetrievalResult:
    context: NumberedContext | None
    chunks: list[ChunkRecord]
    run: RetrievalRunRecord
    allocations: list[Allocation]
    continuation: dict


def largest_remainder(weights, count):
    if (type(count) is not int or count < 0 or not weights
            or any(type(w) is not int or w < 0 for w in weights) or sum(weights) == 0):
        raise ValueError('Nonnegative integer weights, positive total and integer count required')
    total = sum(weights)
    result = [w * count // total for w in weights]
    order = sorted(range(len(weights)), key=lambda i: (-(weights[i]*count % total), i))
    for i in order[:count - sum(result)]: result[i] += 1
    return result


class RetrievalService:
    def __init__(self, engine, index, model_client, reranker, *, chat_turn_id, settings=None, tracer_provider=None):
        if not isinstance(chat_turn_id,str) or not chat_turn_id.strip():
            raise ValueError('chat_turn_id required')
        self.engine, self.index, self.model, self.reranker = engine, index, model_client, reranker
        self.chat_turn_id = chat_turn_id
        self.settings = settings or RetrievalSettings()
        self.tracer_provider=tracer_provider

    @contextmanager
    def _stage(self,name,mode):
        error=None
        with manual_span(name,tracer_provider=self.tracer_provider,metadata={'mode':mode.value}) as span:
            start=perf_counter()
            try:
                yield span
            except Exception as exc:
                # Terminar span sem gravar exceção potencialmente sensível do SDK/SQL.
                span.set_attribute('training.error.kind',type(exc).__name__)
                error=exc
            finally:
                span.set_attribute('training.duration_seconds',perf_counter()-start)
        if error is not None: raise error from None

    def _resolve(self, owner_id, scope, *, coverage=False):
        # Read only metadata here. Text can leave PG only through authorize_points.
        try:
            with self.engine.connect() as c:
                versions = set(c.execute(select(document_version.c.document_version_id).where(
                    document_version.c.owner_id == owner_id,
                    document_version.c.document_version_id.in_(scope.document_version_ids))).scalars())
                if versions != set(scope.document_version_ids):
                    raise ScopeError('Escopo ausente ou inacessível')
                rows = c.execute(select(chunk.c.chunk_id, chunk.c.document_version_id, chunk.c.section_path,
                    chunk.c.start_offset, chunk.c.end_offset, chunk.c.page).where(
                    chunk.c.document_version_id.in_(versions))).mappings().all()
                headings = c.execute(select(canonical_text.c.document_version_id,canonical_span.c.section_path)
                    .select_from(canonical_span.join(canonical_text,canonical_span.c.canonical_id==canonical_text.c.canonical_id))
                    .where(canonical_text.c.document_version_id.in_(versions),canonical_span.c.kind=='heading')).all()
                scope_spans = (c.execute(select(canonical_text.c.document_version_id,canonical_span.c.section_path,
                    canonical_span.c.start,canonical_span.c.end).select_from(canonical_span.join(canonical_text,
                    canonical_span.c.canonical_id==canonical_text.c.canonical_id)).where(
                    canonical_text.c.document_version_id.in_(versions),canonical_span.c.kind!='separator')).mappings().all()
                    if coverage and scope.section_prefix else [])
        except ScopeError:
            raise
        except Exception:
            raise EligibilityUnavailable('Linhagem indisponível para resolver escopo') from None
        prefix = list(scope.section_prefix)
        all_rows = rows
        if coverage and prefix:
            matches=[span for span in scope_spans if list(span['section_path'][:len(prefix)])==prefix]
            rows=[r for r in rows if any(span['document_version_id']==r['document_version_id']
                and span['start']<r['end_offset'] and r['start_offset']<span['end'] for span in matches)]
        else:
            rows = [r for r in rows if r['section_path'][:len(prefix)] == prefix]
        if prefix:
            matching = {r['document_version_id'] for r in rows}
            if len(matching) != 1:
                raise ScopeError('Seção ausente ou ambígua entre versões')
            occurrences=[v for v,path in headings if list(path)==prefix]
            if len(occurrences)>1: raise ScopeError('Seção repetida ambígua')
            all_rows=sorted((r for r in all_rows if r['document_version_id'] in matching),
                            key=lambda r:r['start_offset'])
            matching_ids = {r['chunk_id'] for r in rows}
            runs = 0; inside = False
            for r in all_rows:
                current = r['chunk_id'] in matching_ids
                if current and not inside: runs += 1
                inside = current
            if runs > 1: raise ScopeError('Seção repetida ambígua')
        order = {v:i for i,v in enumerate(scope.document_version_ids)}
        return sorted(rows, key=lambda r:(order[r['document_version_id']],
            r['page'] is None, r['page'] or 0, r['start_offset'], r['end_offset'], r['chunk_id']))

    def _filter(self, owner_id, scope, rows):
        return models.Filter(must=[
            models.FieldCondition(key='owner_id',match=models.MatchValue(value=owner_id)),
            models.FieldCondition(key='document_version_id',match=models.MatchAny(any=list(scope.document_version_ids))),
            models.FieldCondition(key='embedding_key',match=models.MatchValue(value=self.index.settings.embedding_key)),
            models.FieldCondition(key='injection_flagged',match=models.MatchValue(value=False)),
            # Exact prefix resolved against PG arrays, not Qdrant array membership.
            models.FieldCondition(key='chunk_id',match=models.MatchAny(any=[r['chunk_id'] for r in rows])),
        ])

    def retrieve(self, owner_id, plan, classification, scope, count):
        if not isinstance(owner_id,str) or not owner_id.strip(): raise ValueError('owner_id required')
        if type(count) is not int or not 1 <= count <= 20: raise ValueError('count must be 1..20')
        if not isinstance(scope,RetrievalScope): raise TypeError('RetrievalScope required')
        plan = SearchPlan.model_validate(plan)
        classification = Classification.model_validate(classification)
        if not classification.intencao.e_geracao: raise ValueError('Generation intent required')
        mode = classification.modo
        binding = sha256(json.dumps([owner_id,scope.document_version_ids,scope.section_prefix,mode.value],
                                    ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        cursor = scope.continuation
        if cursor is not None and (not isinstance(cursor,dict) or cursor.get('version')!=1
                or cursor.get('binding')!=binding or not isinstance(cursor.get('used_chunk_ids'),list)
                or any(not isinstance(v,str) for v in cursor['used_chunk_ids'])):
            raise ContinuationError('Continuação fora do dono/escopo/modo')
        used = set(cursor['used_chunk_ids']) if cursor else set()
        with self._stage('E5_retrieval',mode):
            rows = self._resolve(owner_id,scope,coverage=mode is RetrievalMode.COBERTURA)
            scope_ids = {r['chunk_id'] for r in rows}
            if not used <= scope_ids: raise ContinuationError('Continuação obsoleta')
            full_rows = rows
            rows = [r for r in rows if r['chunk_id'] not in used]
            points = {}; rrf = {}
            search_rows = full_rows if mode is RetrievalMode.COBERTURA else rows
            if search_rows:
                filter_ = self._filter(owner_id,scope,search_rows)
                try:
                    if mode is RetrievalMode.CONSULTA:
                        for query in plan.consultas:
                            vector = self.model.embed_query(query)
                            if (not isinstance(vector,list) or len(vector)!=3072
                                    or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in vector)):
                                raise ValueError('Invalid embedding')
                            result = self.index.client.query_points(self.index.collection_name,
                                prefetch=[models.Prefetch(query=vector,using='dense',filter=filter_,limit=self.settings.candidate_limit),
                                          models.Prefetch(query=bm25_document(query),using='bm25',filter=filter_,limit=self.settings.candidate_limit)],
                                query=models.FusionQuery(fusion=models.Fusion.RRF),query_filter=filter_,
                                limit=self.settings.candidate_limit,with_payload=True)
                            for point in result.points:
                                cid=(point.payload or {}).get('chunk_id')
                                if cid in scope_ids and cid not in used:
                                    points[cid]=point
                                    rrf[cid]=max(rrf.get(cid,0.),float(point.score))
                        points = dict(sorted(points.items(),key=lambda p:(-rrf[p[0]],p[0]))[:self.settings.candidate_limit])
                    else:
                        offset=None
                        while True:
                            page,offset=self.index.client.scroll(self.index.collection_name,scroll_filter=filter_,
                                limit=128,offset=offset,with_payload=True,with_vectors=False)
                            for point in page:
                                cid=(point.payload or {}).get('chunk_id')
                                if cid in scope_ids: points[cid]=point
                            if offset is None: break
                except Exception:
                    raise RetrievalUnavailable('Falha na recuperação ou embedding') from None
            authorized = authorize_points(self.engine,self.index,owner_id,points.values())
            by_id = {c.chunk_id:c for c in authorized}
            candidates = list(authorized) if mode is RetrievalMode.CONSULTA else [by_id[r['chunk_id']] for r in rows if r['chunk_id'] in by_id]
        # Coverage is canonical traversal, not thematic search: every authorized
        # subsection remains eligible, irrespective of query relevance or top_n.
        scores=[None]*len(candidates)
        cutoff=None
        eligible=candidates
        if mode is RetrievalMode.CONSULTA:
            with self._stage('E6_rerank',mode):
                if candidates:
                    try:
                        scores = self.reranker.score(plan.consultas,candidates)
                        if len(scores)!=len(candidates) or not all(math.isfinite(s) for s in scores):
                            raise ValueError('Invalid reranker scores')
                    except Exception:
                        raise RetrievalUnavailable('Falha no cross-encoder local') from None
                cutoff = self.reranker.cutoff
                if not isinstance(cutoff,(int,float)) or not math.isfinite(cutoff):
                    raise ValueError('Calibrated cutoff required')
                eligible = [c for c,s in zip(candidates,scores) if s > cutoff]
        with self._stage('E7_context',mode) as context_span:
            if mode is RetrievalMode.COBERTURA:
                context_span.set_attribute('training.E6.status','Não se aplica')
                context_span.set_attribute('training.E6.reason','Cobertura canônica não executa reranking temático')
            if mode is RetrievalMode.CONSULTA:
                values = dict(zip((c.chunk_id for c in candidates),scores))
                selected = sorted(eligible,key=lambda c:(-values[c.chunk_id],c.chunk_id))[:self.settings.top_n]
            else:
                selected=[]  # coverage window is constructed from the full revalidated scope below
            # Revalidate after CPU inference: revoked decisions cannot escape to E8.
            refresh_points = (points.values() if mode is RetrievalMode.COBERTURA
                              else [points[c.chunk_id] for c in selected])
            refreshed = authorize_points(self.engine,self.index,owner_id,refresh_points)
            refreshed_ids={c.chunk_id for c in refreshed}
            selected=[c for c in selected if c.chunk_id in refreshed_ids]
            allocations=[]
            if mode is RetrievalMode.COBERTURA:
                sections=canonical_sections(self.engine,owner_id,scope,refreshed)
                remaining_ids={c.chunk_id for c in candidates}&refreshed_ids
                active=[section for section in sections if remaining_ids.intersection(section.chunk_ids)]
                shares=largest_remainder([section.canonical_size for section in active],count) if active else []
                budget={section:share for section,share in zip(active,shares)}
                chosen=set()
                # Allocate against all active canonical subsections before picking any
                # window. The item budget bounds context, never the thematic top-N.
                for section,share in zip(active,shares):
                    ids=[cid for cid in section.chunk_ids if cid in remaining_ids]
                    chosen.update(ids[:share])
                selected=[by_id[r['chunk_id']] for r in rows if r['chunk_id'] in chosen]
                for section in sections:
                    allocations.append(Allocation(section.document_version_id,section.section_path,budget.get(section,0),
                        tuple(c.chunk_id for c in selected if c.chunk_id in section.chunk_ids),
                        section.canonical_size,section.canonical_id,section.h1_id))
            elif selected:
                groups={}
                for c in selected: groups.setdefault((c.document_version_id,tuple(c.section_path)),[]).append(c)
                weights=[sum(len(c.text) for c in group) for group in groups.values()]
                shares=largest_remainder(weights,count)
                allocations=[Allocation(key[0],key[1],share,tuple(c.chunk_id for c in group))
                             for (key,group),share in zip(groups.items(),shares)]
            selected_ids={c.chunk_id for c in selected}
            continuation={'version':1,'binding':binding,'used_chunk_ids':sorted(used|selected_ids),
                          'remaining':len((set(c.chunk_id for c in eligible)&refreshed_ids)-selected_ids)}
            context=NumberedContext(chunks=[ContextChunk(ref=f'C{i}',chunk_id=c.chunk_id,page=c.page,
                        section_path=c.section_path,text=c.text) for i,c in enumerate(selected,1)]) if selected else None
            run=RetrievalRunRecord(retrieval_run_id='retrieval-'+uuid4().hex,chat_turn_id=self.chat_turn_id,modo=mode,
                cutoff=cutoff,candidates=[RetrievalCandidate(chunk_id=c.chunk_id,rrf_score=rrf.get(c.chunk_id),
                    rerank_score=float(s) if s is not None else None,selected=c.chunk_id in selected_ids) for c,s in zip(candidates,scores)])
            return RetrievalResult(context,selected,run,allocations,continuation)

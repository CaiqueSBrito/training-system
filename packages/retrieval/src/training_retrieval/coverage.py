"""Canonical subsection sizes from already authorized chunks, without source reads."""
from dataclasses import dataclass
from sqlalchemy import select
from training_lineage import LineageRepository
from training_lineage.tables import canonical_span, canonical_text, document_version
from training_ingestion.retrieval.eligibility import EligibilityUnavailable


@dataclass(frozen=True)
class CoverageSection:
    document_version_id: str
    canonical_id: str
    h1_id: str | None
    section_path: tuple[str, ...]
    intervals: tuple[tuple[int, int], ...]
    chunk_ids: tuple[str, ...]

    @property
    def canonical_size(self):
        return sum(end-start for start,end in self.intervals)


def _union(intervals):
    output=[]
    for start,end in sorted(intervals):
        if output and start<=output[-1][1]:
            output[-1]=(output[-1][0],max(output[-1][1],end))
        else:
            output.append((start,end))
    return tuple(output)


def canonical_sections(engine, owner_id, scope, authorized):
    """Measure only intersections of exact canonical spans with authorized chunks.

    Full H1 occurrence + H2/H3 path identify a subsection. Each codepoint counts
    once even when several chunks or repeated windows contain it. Separators are
    layout glue; heading/paragraph/table spans are real subsection content.
    No canonical text or unauthorised chunk is read by this metadata operation.
    """
    if not authorized: return ()
    try:
        with engine.connect() as connection:
            maps=LineageRepository(connection).load_chunk_provenance(owner_id,[c.chunk_id for c in authorized])
            ids={sid for mapping in maps.values() for sid in mapping.span_ids}
            spans={row['span_id']:row for row in connection.execute(select(
                canonical_span.c.span_id,canonical_span.c.canonical_id,canonical_span.c.start,
                canonical_span.c.end,canonical_span.c.h1_id,canonical_span.c.section_path,
                canonical_span.c.kind,canonical_text.c.document_version_id).select_from(
                    canonical_span.join(canonical_text,canonical_span.c.canonical_id==canonical_text.c.canonical_id)
                    .join(document_version,canonical_text.c.document_version_id==document_version.c.document_version_id))
                .where(document_version.c.owner_id==owner_id,canonical_span.c.span_id.in_(ids),
                       document_version.c.document_version_id.in_(scope.document_version_ids))).mappings()}
        groups={}
        prefix=scope.section_prefix
        version_order={version:i for i,version in enumerate(scope.document_version_ids)}
        for chunk in sorted(authorized,key=lambda c:(version_order[c.document_version_id],c.start_offset,c.end_offset,c.chunk_id)):
            mapping=maps.get(chunk.chunk_id)
            if (mapping is None or mapping.mapping_status!='exact'
                    or (mapping.start,mapping.end)!=(chunk.start_offset,chunk.end_offset)):
                raise ValueError('Exact canonical mapping required')
            for sid in mapping.span_ids:
                span=spans.get(sid)
                if (span is None or span['canonical_id']!=mapping.canonical_id
                        or span['document_version_id']!=chunk.document_version_id):
                    raise ValueError('Canonical span identity mismatch')
                path=tuple(span['section_path'])
                if span['kind']=='separator' or path[:len(prefix)]!=prefix: continue
                start=max(mapping.start,span['start']);end=min(mapping.end,span['end'])
                if start>=end: continue
                key=(chunk.document_version_id,mapping.canonical_id,span['h1_id'],path)
                intervals,chunk_ids=groups.setdefault(key,([],{}))
                intervals.append((start,end));chunk_ids[chunk.chunk_id]=None
        order={version:i for i,version in enumerate(scope.document_version_ids)}
        sections=[CoverageSection(*key,_union(intervals),tuple(chunk_ids))
                  for key,(intervals,chunk_ids) in groups.items()]
        return tuple(sorted(sections,key=lambda s:(order[s.document_version_id],s.intervals[0][0],s.section_path)))
    except Exception:
        raise EligibilityUnavailable('Linhagem indisponível para medir subseções canônicas') from None

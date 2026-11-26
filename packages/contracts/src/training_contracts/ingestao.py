"""Contratos aditivos de ingestão (ADR-0015); sem I/O ou scheduler.

Coordenadas usam codepoints e intervalos semiabertos sobre UTF-8 canônico.
Validações com vários registros recebem explicitamente a fonte autoritativa;
FKs, autorização, unicidade/CAS e persistência continuam responsabilidade do banco.
"""

from datetime import datetime, timedelta
from hashlib import sha256
from typing import Annotated, Literal, Self, Sequence

from pydantic import AwareDatetime, ConfigDict, Field, field_validator, model_validator

from training_contracts.comum import Contrato, Sha256
from training_contracts.linhagem import ChunkRecord

Id = Annotated[str, Field(min_length=1, pattern=r"\S")]
Positive = Annotated[int, Field(ge=1, strict=True)]
Offset = Annotated[int, Field(ge=0, strict=True)]
SafeId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")]
ErrorCode = Literal[
    "provider", "invalid_output", "timeout", "configuration", "storage",
    "conflict", "stale_generation", "legacy_insufficient", "index_divergent",
]
ReasonCode = Literal[
    "suspected", "rule_block", "judge_block", "judge_release", "no_suspicion",
    "judge_unavailable", "legacy_insufficient",
]


class IngestionContract(Contrato):
    """Imutabilidade inclusive das coleções; UTC obrigatório em datas presentes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("*", mode="after")
    @classmethod
    def _utc(cls, value):
        if isinstance(value, datetime) and value.utcoffset() != timedelta(0):
            raise ValueError("timestamp deve estar em UTC")
        return value


class TextRange(IngestionContract):
    start: Offset
    end: Offset

    @model_validator(mode="after")
    def _interval(self) -> Self:
        if self.end <= self.start:
            raise ValueError("intervalo semiaberto deve ser não vazio")
        return self


class CanonicalTextRecord(IngestionContract):
    canonical_id: Id
    document_version_id: Id
    canonical_version: Id
    parser_version: Id
    text: Annotated[str, Field(min_length=1)]
    text_sha256: Sha256
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _digest(self) -> Self:
        if sha256(self.text.encode("utf-8")).hexdigest() != self.text_sha256:
            raise ValueError("hash do texto canônico divergente")
        return self


class CanonicalSpan(TextRange):
    span_id: Id
    canonical_id: Id
    page: Positive | None
    section_path: tuple[Id, ...]
    h1_id: Id | None
    kind: Literal["heading", "paragraph", "table", "separator"]
    provenance: Literal["exact", "unknown"]

    @model_validator(mode="after")
    def _page(self) -> Self:
        if self.page is not None and (self.provenance == "unknown" or self.kind == "separator"):
            raise ValueError("página não pode ser atribuída a separador ou origem desconhecida")
        return self


class ChunkProvenanceRecord(IngestionContract):
    chunk_id: Id
    canonical_id: Id | None
    start: Offset | None
    end: Offset | None
    span_ids: tuple[Id, ...]
    overlap_ranges: tuple[TextRange, ...]
    mapping_status: Literal["exact", "insufficient"]
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _mapping(self) -> Self:
        if self.mapping_status == "insufficient":
            if any(v is not None for v in (self.canonical_id, self.start, self.end)) or self.span_ids or self.overlap_ranges:
                raise ValueError("fonte insuficiente não admite coordenadas fabricadas")
            return self
        if self.canonical_id is None or self.start is None or self.end is None or self.end <= self.start or not self.span_ids:
            raise ValueError("mapeamento exato exige origem, intervalo e spans")
        if len(set(self.span_ids)) != len(self.span_ids):
            raise ValueError("span_ids deve ser único e ordenado")
        previous_end = self.start
        for interval in self.overlap_ranges:
            if not self.start <= interval.start < interval.end <= self.end or interval.start < previous_end:
                raise ValueError("sobreposições devem ser internas, ordenadas e disjuntas")
            previous_end = interval.end
        return self


class CitationSpanSelection(TextRange):
    chunk_id: Id
    canonical_id: Id
    evidence: Annotated[str, Field(min_length=1)]


class ResolvedSpan(TextRange):
    span_id: Id
    canonical_id: Id
    page: Positive | None
    section_path: tuple[Id, ...]
    provenance: Literal["exact", "unknown"]


class EmbeddingInputRecord(IngestionContract):
    input_id: Id
    chunk_id: Id
    title: str
    section_context: tuple[Id, ...]
    text_sha256: Sha256
    format_version: Id
    embedding_key: Id
    input_sha256: Sha256
    created_at: AwareDatetime

    def validate_input(self, text: str, formatted_input: str) -> None:
        """Confere bytes produzidos pelo formatter existente; não cria outro formatter."""
        if sha256(text.encode("utf-8")).hexdigest() != self.text_sha256:
            raise ValueError("texto do embedding divergente")
        if sha256(formatted_input.encode("utf-8")).hexdigest() != self.input_sha256:
            raise ValueError("entrada formatada do embedding divergente")


class IngestionReservation(IngestionContract):
    """Identidade/fencing de consistência; Procrastinate mantém execução e retries."""

    reservation_id: Id
    owner_id: Id
    source_sha256: Sha256
    document_version_id: Id
    phase: Literal["reserved", "parsing", "persisted", "indexing", "ready", "failed"]
    generation: Positive
    lease_until: AwareDatetime
    attempt_id: Id
    error_code: ErrorCode | None

    @model_validator(mode="after")
    def _state(self) -> Self:
        if (self.phase == "failed") != (self.error_code is not None):
            raise ValueError("error_code existe se e somente se a reserva falhou")
        return self


class JudgeMetadata(IngestionContract):
    provider: Literal["google", "openai", "anthropic", "openrouter"]
    model: Id
    config_version: Id
    config_sha256: Sha256
    prompt_version: Id
    prompt_sha256: Sha256
    request_id: SafeId | None
    result: Literal["injection", "safe", "error"]
    error_kind: ErrorCode | None

    @model_validator(mode="after")
    def _result(self) -> Self:
        if (self.result == "error") != (self.error_kind is not None):
            raise ValueError("error_kind existe se e somente se houve erro do juiz")
        return self


class IngestionAuditEvent(IngestionContract):
    event_id: Id
    attempt_id: Id
    owner_id: Id
    document_version_id: Id
    chunk_id: Id
    sequence: Positive
    stage: Literal["triage", "judge", "decision"]
    policy_version: Id
    input_sha256: Sha256
    heuristic_version: Id
    heuristic_rule_ids: tuple[Id, ...]
    judge_metadata: JudgeMetadata | None
    outcome: Literal["clean", "suspect", "safe", "injection", "error", "allowed", "blocked", "unavailable"]
    reason_code: ReasonCode
    created_at: AwareDatetime
    triage_event_id: Id | None = None
    judge_event_id: Id | None = None
    supersedes_decision_id: Id | None

    @model_validator(mode="after")
    def _event(self) -> Self:
        refs = (self.triage_event_id, self.judge_event_id, self.supersedes_decision_id)
        if self.event_id in refs:
            raise ValueError("evento não pode referenciar a si mesmo")
        if len(set(self.heuristic_rule_ids)) != len(self.heuristic_rule_ids):
            raise ValueError("regras da heurística não podem repetir")
        if self.stage != "decision" and self.supersedes_decision_id is not None:
            raise ValueError("somente decisão substitui decisão anterior")
        if self.stage == "triage":
            if self.triage_event_id is not None or self.judge_event_id is not None or self.judge_metadata is not None:
                raise ValueError("triagem não pode inventar chamada ao juiz")
            if self.outcome == "clean":
                if self.reason_code != "no_suspicion" or self.heuristic_rule_ids:
                    raise ValueError("triagem limpa exige ausência de suspeita e regras")
            elif self.outcome != "suspect" or self.reason_code != "suspected" or not self.heuristic_rule_ids:
                raise ValueError("triagem suspeita exige regras e motivo de suspeita")
        elif self.stage == "judge":
            if self.triage_event_id is None or self.judge_event_id is not None or self.judge_metadata is None:
                raise ValueError("juiz exige referência de triagem e metadados reais")
            reason = {"safe": "judge_release", "injection": "judge_block", "error": "judge_unavailable"}.get(self.outcome)
            if reason is None or reason != self.reason_code or self.outcome != self.judge_metadata.result:
                raise ValueError("resultado, motivo e metadados do juiz divergentes")
        else:
            if self.triage_event_id is None or self.judge_metadata is not None:
                raise ValueError("decisão exige triagem e referencia juiz sem duplicar metadados")
            expected = {
                "no_suspicion": "allowed", "judge_release": "allowed",
                "rule_block": "blocked", "judge_block": "blocked",
                "judge_unavailable": "unavailable", "legacy_insufficient": "unavailable",
            }.get(self.reason_code)
            if expected is None or self.outcome != expected:
                raise ValueError("decisão e motivo efetivo divergentes")
            if self.reason_code.startswith("judge_") and self.judge_event_id is None:
                raise ValueError("decisão do juiz exige referência ao seu evento")
            if self.reason_code == "no_suspicion" and self.judge_event_id is not None:
                raise ValueError("decisão sem suspeita não pode referenciar juiz")
        return self


class EffectiveChunkDecision(IngestionContract):
    chunk_id: Id
    owner_id: Id
    decision_event_id: Id
    revision: Positive
    allowed: Annotated[bool, Field(strict=True)]
    policy_version: Id


class ReconciliationJob(IngestionContract):
    """Checkpoint de negócio correlacionado a job Procrastinate, sem fila própria."""

    job_id: Id
    owner_id: Id
    document_version_id: Id
    embedding_key: Id
    desired_revision: Positive
    cursor: Id | None
    attempt: Positive
    state: Literal["pending", "running", "retry", "complete", "blocked"]
    error_code: ErrorCode | None
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _state(self) -> Self:
        if (self.state in ("retry", "blocked")) != (self.error_code is not None):
            raise ValueError("checkpoint retry/blocked exige código sanitizado de erro")
        return self


class OrphanInventoryEntry(IngestionContract):
    inventory_id: Id
    point_id: Id
    collection: Id
    owner_id: Id | None
    version_id: Id | None
    observed_at: AwareDatetime
    payload_digest: Sha256
    source_db_identity: SafeId
    fixture_manifest_ref: Id | None
    classification: Literal["unknown", "active", "fixture_proven"]

    @model_validator(mode="after")
    def _fixture(self) -> Self:
        if self.classification == "fixture_proven" and (
            self.fixture_manifest_ref is None or self.owner_id is None or self.version_id is None
        ):
            raise ValueError("fixture comprovada exige manifesto e identidades")
        return self


def validate_canonical_spans(canonical: CanonicalTextRecord, spans: Sequence[CanonicalSpan]) -> None:
    """Exige partição completa, ordenada, sem inventar fonte nem páginas."""
    cursor = 0
    seen = set()
    for span in spans:
        if span.canonical_id != canonical.canonical_id or span.span_id in seen or span.start != cursor:
            raise ValueError("spans não particionam a fonte canônica")
        seen.add(span.span_id)
        cursor = span.end
    if cursor != len(canonical.text):
        raise ValueError("spans não cobrem exatamente o texto canônico")


def validate_chunk_provenance(
    canonical: CanonicalTextRecord, spans: Sequence[CanonicalSpan],
    chunk: ChunkRecord, mapping: ChunkProvenanceRecord,
) -> None:
    """Validação pura; repositório aplica autorização e integridade transacional."""
    validate_canonical_spans(canonical, spans)
    if mapping.mapping_status != "exact" or mapping.canonical_id != canonical.canonical_id:
        raise ValueError("mapeamento canônico exato indisponível")
    if chunk.chunk_id != mapping.chunk_id or chunk.document_version_id != canonical.document_version_id:
        raise ValueError("identidade de chunk/versão divergente")
    if (mapping.start, mapping.end) != (chunk.start_offset, chunk.end_offset):
        raise ValueError("offsets do chunk divergentes")
    if mapping.end > len(canonical.text) or chunk.text != canonical.text[mapping.start:mapping.end]:
        raise ValueError("conteúdo do chunk não corresponde ao canônico")
    selected = [s for s in spans if s.start < mapping.end and mapping.start < s.end]
    if tuple(s.span_id for s in selected) != mapping.span_ids:
        raise ValueError("spans do chunk divergentes")
    if len({s.h1_id for s in selected if s.h1_id is not None}) > 1:
        raise ValueError("chunk atravessa seções H1 distintas")


def resolve_citation_spans(
    selection: CitationSpanSelection, canonical: CanonicalTextRecord,
    spans: Sequence[CanonicalSpan], chunk: ChunkRecord, mapping: ChunkProvenanceRecord,
) -> tuple[ResolvedSpan, ...]:
    """Resolve interseções verificadas; não faz consulta nem concede acesso ao dono."""
    validate_chunk_provenance(canonical, spans, chunk, mapping)
    if selection.chunk_id != chunk.chunk_id or selection.canonical_id != canonical.canonical_id:
        raise ValueError("identidade da citação divergente")
    if not mapping.start <= selection.start < selection.end <= mapping.end:
        raise ValueError("citação fora do chunk")
    if selection.evidence != canonical.text[selection.start:selection.end]:
        raise ValueError("evidência não corresponde ao intervalo canônico")
    return tuple(ResolvedSpan(
        span_id=s.span_id, canonical_id=s.canonical_id,
        start=max(s.start, selection.start), end=min(s.end, selection.end),
        page=s.page, section_path=s.section_path, provenance=s.provenance,
    ) for s in spans if s.start < selection.end and selection.start < s.end)

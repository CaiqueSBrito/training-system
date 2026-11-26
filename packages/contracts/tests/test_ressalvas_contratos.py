"""Casos novos propostos: ING-CAN-001 e ING-PRO-001; G3 pendente.

Não executados nesta preparação. O módulo de produto ingestao ainda será
implementado em T-010 após gates; import direto impede um verde por skip/fallback.
Validações relacionais de conteúdo/offsets são casos de repositório em casos.md.
"""

from datetime import datetime, timezone
from hashlib import sha256

import pytest
from pydantic import ValidationError

from training_contracts.ingestao import CanonicalSpan, CanonicalTextRecord


def canonical_payload(text):
    return {
        "canonical_id": "canonical-1",
        "document_version_id": "version-1",
        "canonical_version": "docling-order-separators-v1",
        "parser_version": "docling-installed-version",
        "text": text,
        "text_sha256": sha256(text.encode("utf-8")).hexdigest(),
        "created_at": datetime(2026, 11, 24, tzinfo=timezone.utc),
    }


@pytest.mark.parametrize("text", ["abc", "頁面二\n\n頁面三", "e\u0301😀", "https://exemplo/" + "a" * 4000])
def test_ING_CAN_001_preserva_texto_e_hash_utf8(text):
    record = CanonicalTextRecord.model_validate(canonical_payload(text))
    assert record.text == text
    assert record.text_sha256 == sha256(text.encode("utf-8")).hexdigest()
    assert CanonicalTextRecord.model_validate_json(record.model_dump_json()) == record


def test_ING_CAN_001_rejeita_hash_de_texto_diferente_mesmo_tamanho():
    payload = canonical_payload("abc")
    payload["text"] = "abd"
    with pytest.raises(ValidationError):
        CanonicalTextRecord.model_validate(payload)


@pytest.mark.parametrize("digest", ["g" * 64, "a" * 63, "A" * 64])
def test_ING_CAN_001_rejeita_sha_invalido(digest):
    payload = canonical_payload("abc")
    payload["text_sha256"] = digest
    with pytest.raises(ValidationError):
        CanonicalTextRecord.model_validate(payload)


def test_ING_CAN_001_rejeita_timestamp_sem_timezone():
    payload = canonical_payload("abc")
    payload["created_at"] = datetime(2026, 11, 24)
    with pytest.raises(ValidationError):
        CanonicalTextRecord.model_validate(payload)


def test_ING_CAN_001_registro_estrito_e_imutavel():
    payload = canonical_payload("abc")
    with pytest.raises(ValidationError):
        CanonicalTextRecord.model_validate({**payload, "campo_extra": True})
    record = CanonicalTextRecord.model_validate(payload)
    with pytest.raises(ValidationError):
        record.text = "abd"


def span_payload():
    return {
        "span_id": "span-1",
        "canonical_id": "canonical-1",
        "start": 0,
        "end": 3,
        "page": None,
        "section_path": ["Seção"],
        "h1_id": "h1-occurrence-1",
        "kind": "paragraph",
        "provenance": "unknown",
    }


def test_ING_PRO_001_pagina_ausente_permanece_nula():
    span = CanonicalSpan.model_validate(span_payload())
    assert span.page is None
    assert span.provenance == "unknown"


@pytest.mark.parametrize("start,end", [(-1, 2), (2, 2), (3, 2)])
def test_ING_PRO_001_rejeita_intervalos_invalidos(start, end):
    with pytest.raises(ValidationError):
        CanonicalSpan.model_validate({**span_payload(), "start": start, "end": end})


def test_ING_PRO_001_nao_inventa_pagina_para_proveniencia_desconhecida():
    with pytest.raises(ValidationError):
        CanonicalSpan.model_validate({**span_payload(), "page": 2})


def test_ING_PRO_001_pagina_exata_precisa_ser_positiva():
    payload = {**span_payload(), "provenance": "exact", "page": 2}
    assert CanonicalSpan.model_validate(payload).page == 2
    with pytest.raises(ValidationError):
        CanonicalSpan.model_validate({**payload, "page": 0})

# ING-CAN-001 / ING-PRO-001: validação cruzada recebe os registros reais,
# sem fingir que um modelo isolado tem acesso ao banco.
from training_contracts.ingestao import (
    ChunkProvenanceRecord, CitationSpanSelection, EffectiveChunkDecision,
    EmbeddingInputRecord, IngestionAuditEvent, IngestionReservation,
    JudgeMetadata, OrphanInventoryEntry, ReconciliationJob, TextRange,
    resolve_citation_spans, validate_canonical_spans, validate_chunk_provenance,
)
from training_contracts.linhagem import ChunkRecord


NOW = datetime(2026, 11, 24, tzinfo=timezone.utc)
DIGEST = sha256(b"abc").hexdigest()


def source_fixture():
    canonical = CanonicalTextRecord.model_validate(canonical_payload("abc\n\ndef"))
    spans = tuple(CanonicalSpan.model_validate({
        **span_payload(), "span_id": sid, "start": start, "end": end,
        "page": page, "kind": kind,
        "provenance": "exact" if page else "unknown",
        "section_path": section,
    }) for sid, start, end, page, kind, section in (
        ("p2", 0, 3, 2, "paragraph", ["H1", "A"]),
        ("sep", 3, 5, None, "separator", ["H1"]),
        ("p3", 5, 8, 3, "paragraph", ["H1", "B"]),
    ))
    chunk = ChunkRecord(chunk_id="chunk-1", document_version_id="version-1",
        chunker_version="structure-new", start_offset=0, end_offset=8,
        text="abc\n\ndef")
    mapping = ChunkProvenanceRecord(chunk_id="chunk-1", canonical_id="canonical-1",
        start=0, end=8, span_ids=["p2", "sep", "p3"], overlap_ranges=[],
        mapping_status="exact", created_at=NOW)
    return canonical, spans, chunk, mapping


def test_ING_PRO_001_resolve_so_intersecoes_reais_entre_paginas():
    canonical, spans, chunk, mapping = source_fixture()
    validate_canonical_spans(canonical, spans)
    validate_chunk_provenance(canonical, spans, chunk, mapping)
    selection = CitationSpanSelection(chunk_id="chunk-1", canonical_id="canonical-1",
        start=5, end=8, evidence="def")
    result = resolve_citation_spans(selection, canonical, spans, chunk, mapping)
    assert [(s.start, s.end, s.page, s.section_path) for s in result] == [(5, 8, 3, ("H1", "B"))]
    crossed = selection.model_dump() | {"start": 1, "end": 7, "evidence": "bc\n\nde"}
    result = resolve_citation_spans(CitationSpanSelection(**crossed), canonical, spans, chunk, mapping)
    assert [(s.start, s.end, s.page) for s in result] == [(1, 3, 2), (3, 5, None), (5, 7, 3)]


@pytest.mark.parametrize("change", [
    {"text": "abd\n\ndef"}, {"document_version_id": "other"},
    {"start_offset": 1}, {"chunk_id": "other"},
])
def test_ING_CAN_001_proveniencia_rejeita_chunk_divergente(change):
    canonical, spans, chunk, mapping = source_fixture()
    changed = ChunkRecord.model_validate(chunk.model_dump() | change)
    with pytest.raises(ValueError):
        validate_chunk_provenance(canonical, spans, changed, mapping)


@pytest.mark.parametrize("mutation", ["gap", "overlap", "wrong_source", "other_h1", "missing_span"])
def test_ING_PRO_001_rejeita_mapa_sem_particao_ou_h1_fiel(mutation):
    canonical, spans, chunk, mapping = source_fixture()
    changed = list(spans)
    if mutation == "missing_span":
        mapping = ChunkProvenanceRecord(**(mapping.model_dump() | {"span_ids": ["p2"]}))
    else:
        data = changed[-1].model_dump()
        data.update({"gap": {"start": 6}, "overlap": {"start": 4},
            "wrong_source": {"canonical_id": "other"},
            "other_h1": {"h1_id": "second-h1"}}[mutation])
        changed[-1] = CanonicalSpan(**data)
    with pytest.raises(ValueError):
        validate_chunk_provenance(canonical, changed, chunk, mapping)


def test_ING_PRO_001_selecao_repetida_usa_intervalo_e_rejeita_evidencia_falsa():
    canonical, spans, chunk, mapping = source_fixture()
    selection = CitationSpanSelection(chunk_id="chunk-1", canonical_id="canonical-1",
        start=5, end=8, evidence="abc")
    with pytest.raises(ValueError):
        resolve_citation_spans(selection, canonical, spans, chunk, mapping)


def test_ING_CAN_001_historico_insuficiente_nao_inventa_mapa():
    payload = dict(chunk_id="legacy", canonical_id=None, start=None, end=None,
        span_ids=[], overlap_ranges=[], mapping_status="insufficient", created_at=NOW)
    assert ChunkProvenanceRecord(**payload).canonical_id is None
    with pytest.raises(ValidationError):
        ChunkProvenanceRecord(**(payload | {"start": 0, "end": 3}))
    with pytest.raises(ValidationError):
        ChunkProvenanceRecord(**(payload | {"mapping_status": "exact"}))


@pytest.mark.parametrize("start,end", [(True, 3), (0, 0), (3, 2), (-1, 2)])
def test_ING_CAN_001_ranges_estritos(start, end):
    with pytest.raises(ValidationError):
        TextRange(start=start, end=end)


def metadata_payload():
    return dict(provider="google", model="approved-model", config_version="cfg-1",
        config_sha256=DIGEST, prompt_version="prompt-1", prompt_sha256=DIGEST,
        request_id="request-1", result="safe", error_kind=None)


def event_payload():
    return dict(event_id="event-3", attempt_id="attempt-1", owner_id="owner-1",
        document_version_id="version-1", chunk_id="chunk-1", sequence=3,
        stage="decision", policy_version="policy-1", input_sha256=DIGEST,
        heuristic_version="heuristic-1", heuristic_rule_ids=["ambiguous-role"],
        judge_metadata=None, outcome="allowed", reason_code="judge_release",
        triage_event_id="event-1", judge_event_id="event-2",
        supersedes_decision_id=None, created_at=NOW)


def test_ING_AUD_001_liberacao_retem_referencias_e_julgamento_real():
    decision = IngestionAuditEvent(**event_payload())
    judge = IngestionAuditEvent(**(event_payload() | {
        "event_id": "event-2", "sequence": 2, "stage": "judge", "outcome": "safe",
        "judge_event_id": None, "judge_metadata": metadata_payload()}))
    assert decision.judge_event_id == judge.event_id
    assert judge.judge_metadata.result == "safe"
    with pytest.raises(ValidationError):
        decision.outcome = "blocked"
    assert decision.heuristic_rule_ids == ("ambiguous-role",)


@pytest.mark.parametrize("change", [
    {"outcome": "blocked"}, {"judge_event_id": None},
    {"triage_event_id": None}, {"sequence": 0}, {"sequence": True},
    {"reason_code": "resposta privada do juiz"},
    {"supersedes_decision_id": "event-3"},
    {"judge_metadata": metadata_payload()},
])
def test_ING_AUD_001_rejeita_evento_incoerente(change):
    with pytest.raises(ValidationError):
        IngestionAuditEvent(**(event_payload() | change))


def test_ING_AUD_001_erro_do_juiz_nao_e_resultado_seguro():
    payload = metadata_payload() | {"result": "error", "error_kind": "provider"}
    assert JudgeMetadata(**payload).result == "error"
    with pytest.raises(ValidationError):
        JudgeMetadata(**(payload | {"result": "safe"}))
    with pytest.raises(ValidationError):
        JudgeMetadata(**(metadata_payload() | {"request_id": "https://api?key=secret"}))
    with pytest.raises(ValidationError):
        JudgeMetadata(**(metadata_payload() | {"reason": "raw output"}))


def test_ING_AUD_001_trilha_sem_suspeita_nao_finge_chamada():
    payload = event_payload() | {"stage": "triage", "outcome": "clean",
        "reason_code": "no_suspicion", "triage_event_id": None,
        "judge_event_id": None, "heuristic_rule_ids": []}
    event = IngestionAuditEvent(**payload)
    assert event.judge_metadata is None
    with pytest.raises(ValidationError):
        IngestionAuditEvent(**(payload | {"outcome": "suspect"}))


def reservation_payload():
    return dict(reservation_id="reservation-1", owner_id="owner-1", source_sha256=DIGEST,
        document_version_id="version-1", phase="reserved", generation=1,
        lease_until=NOW, attempt_id="attempt-1", error_code=None)


@pytest.mark.parametrize("phase", ["reserved", "parsing", "persisted", "indexing", "ready", "failed"])
def test_ING_TXN_001_reserva_tem_fencing_e_fases_de_dominio(phase):
    payload = reservation_payload() | {"phase": phase,
        "error_code": "provider" if phase == "failed" else None}
    assert IngestionReservation(**payload).generation == 1


@pytest.mark.parametrize("change", [{"generation": 0}, {"generation": True},
    {"phase": "failed"}, {"error_code": "provider"}, {"owner_id": " "}])
def test_ING_TXN_001_rejeita_reserva_sem_identidade_ou_estado_coerente(change):
    with pytest.raises(ValidationError):
        IngestionReservation(**(reservation_payload() | change))


def test_ING_REP_001_input_persistido_confere_texto_e_bytes_enviados():
    value = "title: T — S | text: abc"
    record = EmbeddingInputRecord(input_id="input-1", chunk_id="chunk-1", title="T",
        section_context=["S"], text_sha256=DIGEST, format_version="fmt-v1",
        embedding_key="gemini-embedding-2@3072@fmt-v1",
        input_sha256=sha256(value.encode()).hexdigest(), created_at=NOW)
    record.validate_input("abc", value)
    with pytest.raises(ValueError):
        record.validate_input("abd", value)
    with pytest.raises(ValueError):
        record.validate_input("abc", "title: wrong | text: abc")
    assert record.section_context == ("S",)


def test_ING_REP_001_decisao_efetiva_exige_referencia_nao_flag_historico():
    payload = dict(chunk_id="chunk-1", owner_id="owner-1", decision_event_id="event-3",
        revision=3, allowed=True, policy_version="policy-1")
    assert EffectiveChunkDecision(**payload).allowed is True
    for change in ({"allowed": "true"}, {"revision": 0}, {"decision_event_id": ""}):
        with pytest.raises(ValidationError):
            EffectiveChunkDecision(**(payload | change))


def test_ING_REP_001_checkpoint_nao_e_scheduler_e_erros_sao_sanitizados():
    payload = dict(job_id="procrastinate-123", owner_id="owner-1", document_version_id="version-1",
        embedding_key="gemini-embedding-2@3072@fmt-v1", desired_revision=3, cursor=None,
        attempt=1, state="pending", error_code=None, created_at=NOW)
    assert ReconciliationJob(**payload).job_id == "procrastinate-123"
    for change in ({"state": "retry"}, {"state": "complete", "error_code": "provider"},
        {"state": "blocked", "error_code": "private payload"}, {"attempt": 0}):
        with pytest.raises(ValidationError):
            ReconciliationJob(**(payload | change))


def test_ING_REP_001_inventario_nao_prova_fixture_por_prefixo_ou_antijoin():
    payload = dict(inventory_id="inventory-1", point_id="point-1", collection="collection-1",
        owner_id="test-owner", version_id="version-1", observed_at=NOW,
        payload_digest=DIGEST, source_db_identity="local-app",
        fixture_manifest_ref=None, classification="unknown")
    assert OrphanInventoryEntry(**payload).classification == "unknown"
    with pytest.raises(ValidationError):
        OrphanInventoryEntry(**(payload | {"classification": "fixture_proven"}))
    proved = OrphanInventoryEntry(**(payload | {"classification": "fixture_proven",
        "fixture_manifest_ref": "manifests/run-1.json"}))
    assert proved.fixture_manifest_ref == "manifests/run-1.json"


def test_ING_CAN_001_todos_contratos_novos_exportados_e_schema_registrado():
    import training_contracts
    from training_contracts.schema import MODELOS
    models = (CanonicalTextRecord, CanonicalSpan, ChunkProvenanceRecord,
        CitationSpanSelection, EmbeddingInputRecord, IngestionReservation,
        IngestionAuditEvent, EffectiveChunkDecision, ReconciliationJob, OrphanInventoryEntry)
    for model in models:
        assert getattr(training_contracts, model.__name__) is model
        assert model in MODELOS
        assert model.model_json_schema()["additionalProperties"] is False

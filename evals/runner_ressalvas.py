"""Runner RAGAS 0.4.x real para o escopo de ingestão, reconciliação e consulta segura (T-015).

Regras estritas:
- Proibido mock, stub, score 0.0 padrão ou resposta simulada em código de aplicação.
- Usa `ragas.llms.llm_factory(..., adapter="litellm")` com `instructor.from_litellm` sobre
  `EVAL_JUDGE_MODEL` (via OpenRouter, conforme ADR-0006).
- Avalia apenas as métricas aplicáveis ao escopo realmente disponível (`ContextPrecisionWithReference`
  e `ContextRecall`); `Faithfulness` é marcada explicitamente como `Não se aplica` porque o estágio
  de geração (`E8`) não pertence a este escopo, e `global_pipeline_success` permanece `False`.
- Cobertura determinística de proveniência (`deterministic_provenance_hit_rate == 1.00`, validando
  substrings, `expected_section_paths` nos spans/chunks e `mapping_status == 'exact'`) é gate
  obrigatório para `Passou`.
- Scores das métricas são validados estritamente como `float`/`int` finitos no domínio `[0.0, 1.0]`
  (sem `bool`, `NaN`, `inf` ou clamp silencioso).
- Calibração executável (`run_judge_calibration` / `--calibrate`) avalia controles positivos
  (contextos recuperados reais) e controles negativos (contexto deslocado de outro documento)
  com o juiz real, emitindo `CalibrationManifest` que vincula obrigatoriamente a baseline.
- Execução real roda em sandbox isolada (container Qdrant descartável `qdrant/qdrant:v1.20.2`
  + banco PostgreSQL descartável), sem tocar na coleção compartilhada e sem `except Exception: pass`
  no teardown.
- Relatórios e manifestos persistem apenas IDs, hashes SHA-256, metadados de versão e scores,
  nunca chaves de API nem texto bruto de documentos.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version as pkg_version
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Iterator, Literal, Sequence
from urllib.request import urlopen
from uuid import uuid4

import instructor
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from ragas.llms import llm_factory
from ragas.llms.litellm_llm import LiteLLMStructuredLLM
from ragas.metrics.collections import ContextPrecisionWithReference, ContextRecall
from ragas.metrics.collections.context_precision.util import ContextPrecisionPrompt
from ragas.metrics.collections.context_recall.util import ContextRecallPrompt

from training_llm import ModelSettings, _guard_sdk_logging, read_settings


EVALS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EVALS_ROOT.parent
DEFAULT_DATASET_PATH = EVALS_ROOT / "datasets" / "ressalvas" / "dataset.json"
SUPPORTED_RETRIEVAL_METRICS = frozenset({"context_precision_with_reference", "context_recall"})


class EvalError(RuntimeError):
    """Base de erros do runner de avaliação."""


class EvalConfigError(EvalError):
    """Configuração de dataset, métrica ou modelo do juiz inválida."""


class EvalBlockedError(EvalError):
    """Pré-requisito obrigatório ausente (credencial, aprovação de limiar, calibração ou compatibilidade de baseline)."""


class EvalProviderError(EvalError):
    """Erro sanitizado na chamada ao provedor do juiz RAGAS (sem chave, prompt ou conteúdo de documento)."""

    def __init__(
        self,
        category: Literal["provider", "invalid_output", "timeout", "unsafe_logging"],
        *,
        status_code: int | None = None,
        retryable: bool | None = None,
    ) -> None:
        self.category = category
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(f"Falha sanitizada no juiz RAGAS: {category}")


def _sanitize_provider_exception(exc: Exception) -> EvalProviderError:
    status = getattr(exc, "status_code", None)
    status_code = status if type(status) is int and 100 <= status <= 599 else None
    if isinstance(exc, TimeoutError):
        return EvalProviderError("timeout", status_code=status_code, retryable=True)
    retryable = (
        status_code in {408, 425, 429, 500, 502, 503, 504}
        if status_code is not None
        else None
    )
    return EvalProviderError("provider", status_code=status_code, retryable=retryable)


def _ingest_document_with_resumable_retries(
    ingestor: Any,
    doc_path: Path,
    owner_id: str,
    *,
    document_id: str,
    max_attempts: int = 3,
    backoff_seconds: float = 2.0,
) -> Any:
    """Executa `ingestor.ingest(...)` aproveitando a retomada durável de `DurableIngestionProtocol`
    quando o provedor externo (ex.: Gemini API HTTP 503/429) retorna falha transitória (`retryable=True`).
    Como o protocolo grava a fase `failed` na reserva, re-invocar `ingestor.ingest` retoma a partir
    dos chunks já persistidos e dos chunks ainda sem decisão/ack, sem re-parsear o documento.
    """
    from training_ingestion.protocol import IngestionStageError

    last_exc: IngestionStageError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return ingestor.ingest(doc_path, owner_id, document_id=document_id)
        except IngestionStageError as exc:
            last_exc = exc
            if not exc.retryable or attempt >= max_attempts:
                raise EvalProviderError(
                    "provider" if exc.category == "provider" else "timeout",
                    status_code=exc.status_code,
                    retryable=exc.retryable,
                ) from None
            if backoff_seconds > 0:
                time.sleep(backoff_seconds * attempt)
    assert last_exc is not None
    raise EvalProviderError(
        "provider", status_code=last_exc.status_code, retryable=last_exc.retryable
    )


class ChunkConfigSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_tokens: int = Field(ge=2)
    max_tokens: int = Field(ge=2)
    overlap_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_bounds(self) -> ChunkConfigSpec:
        if self.target_tokens > self.max_tokens:
            raise ValueError("target_tokens deve ser <= max_tokens")
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError("overlap_tokens deve ser < max_tokens")
        return self


class NotApplicableMetricSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["Não se aplica"]
    reason: str = Field(min_length=10)


class DocumentEntrySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_id: str = Field(min_length=1)


class EvaluationCaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1)
    doc_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    user_input: str = Field(min_length=10)
    reference: str = Field(min_length=10)
    expected_section_paths: tuple[tuple[str, ...], ...] = Field(min_length=1)
    expected_evidence_substrings: tuple[str, ...] = Field(min_length=1)
    tags: tuple[str, ...] = Field(default_factory=tuple)


class SecurityRegressionReferenceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: Literal["SEC-ATQ-001"]
    case_group: Literal["C09"]
    attacks_ids: tuple[str, ...] = Field(min_length=9, max_length=9)
    legitimate_controls_count: int = Field(ge=1)
    canonical_source: str = Field(min_length=1)
    evidence_ref: str = Field(min_length=1)
    note: str = Field(min_length=10)


class DatasetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    scope: Literal["ingestao_consulta_segura_sem_geracao"]
    status: str = Field(min_length=1)
    description: str = Field(min_length=10)
    chunk_config: ChunkConfigSpec
    retrieval_top_k: int = Field(ge=1, le=50)
    applicable_metrics: tuple[str, ...] = Field(min_length=1)
    not_applicable_metrics: dict[str, NotApplicableMetricSpec]
    documents: tuple[DocumentEntrySpec, ...] = Field(min_length=1)
    cases: tuple[EvaluationCaseSpec, ...] = Field(min_length=1)
    security_regression_reference: SecurityRegressionReferenceSpec

    @field_validator("applicable_metrics")
    @classmethod
    def _validate_metrics(cls, metrics: tuple[str, ...]) -> tuple[str, ...]:
        if "faithfulness" in metrics:
            raise ValueError(
                "faithfulness não pode ser habilitada no escopo sem geração (E8 inexistente)"
            )
        unsupported = set(metrics) - SUPPORTED_RETRIEVAL_METRICS
        if unsupported:
            raise ValueError(f"Métricas não suportadas neste escopo: {sorted(unsupported)}")
        if len(set(metrics)) != len(metrics):
            raise ValueError("applicable_metrics contém duplicatas")
        return metrics

    @model_validator(mode="after")
    def _validate_cross_refs(self) -> DatasetSpec:
        if "faithfulness" not in self.not_applicable_metrics:
            raise ValueError("faithfulness deve constar explicitamente em not_applicable_metrics")
        doc_ids = [d.doc_id for d in self.documents]
        if len(set(doc_ids)) != len(doc_ids):
            raise ValueError("doc_id duplicado em documents")
        doc_owners = {d.doc_id: d.owner_id for d in self.documents}
        case_ids = [c.case_id for c in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("case_id duplicado em cases")
        for case in self.cases:
            if case.doc_id not in doc_owners:
                raise ValueError(f"Caso {case.case_id} referencia doc_id inexistente: {case.doc_id}")
            if case.owner_id != doc_owners[case.doc_id]:
                raise ValueError(f"Caso {case.case_id} tem owner_id divergente do documento {case.doc_id}")
        return self


def load_dataset_spec(dataset_path: str | Path = DEFAULT_DATASET_PATH) -> tuple[DatasetSpec, str]:
    """Carrega e valida o dataset versionado, verificando os SHA-256 de todos os documentos."""
    path = Path(dataset_path).resolve()
    if not path.is_file():
        raise EvalConfigError(f"Dataset não encontrado: {path}")
    raw_bytes = path.read_bytes()
    dataset_sha256 = sha256(raw_bytes).hexdigest()
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
        spec = DatasetSpec.model_validate(payload)
    except Exception as exc:
        raise EvalConfigError(f"Dataset inválido ({path.name}): {exc}") from None

    base_dir = path.parent
    doc_texts_by_id: dict[str, str] = {}
    for doc in spec.documents:
        doc_path = (base_dir / doc.relative_path).resolve()
        if not doc_path.is_relative_to(base_dir) or not doc_path.is_file():
            raise EvalConfigError(f"Documento do dataset ausente ou fora do diretório: {doc.relative_path}")
        raw_doc_bytes = doc_path.read_bytes()
        actual_sha = sha256(raw_doc_bytes).hexdigest()
        if actual_sha != doc.sha256:
            raise EvalConfigError(
                f"hash sha256 divergente para {doc.relative_path}: esperado {doc.sha256}, atual {actual_sha}"
            )
        doc_texts_by_id[doc.doc_id] = raw_doc_bytes.decode("utf-8")

    # Validação determinística de integridade do dataset: toda substring esperada (`expected_evidence_substrings`)
    # deve existir literalmente no texto fonte do respectivo documento (`doc_id`), impedindo erro de transcrição/paráfrase.
    for case in spec.cases:
        source_text = doc_texts_by_id[case.doc_id]
        for expected_sub in case.expected_evidence_substrings:
            if expected_sub not in source_text:
                raise EvalConfigError(
                    f"Integridade do dataset falhou no caso {case.case_id}: "
                    f"expected_evidence_substring {expected_sub!r} não existe literalmente no documento {case.doc_id}"
                )
    return spec, dataset_sha256


@dataclass(frozen=True)
class EvalJudgeConfig:
    """Configuração estrita do juiz RAGAS via LiteLLM/OpenRouter (ADR-0006)."""

    judge_model: str
    provider: Literal["openrouter"]
    provider_model: str
    api_key: str = field(default="", repr=False)
    temperature: float = 0.0
    top_p: float = 1.0
    seed: int = 42
    embedding_key: str = "gemini-embedding-2@3072@fmt-v1"
    config_version: str = "ragas-0.4-litellm-instructor-v1"

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def require_api_key(self) -> str:
        if not self.has_api_key:
            raise EvalBlockedError(
                "Bloqueado: credencial OPENROUTER_API_KEY ausente para o juiz EVAL_JUDGE_MODEL"
            )
        return self.api_key

    def config_payload(self) -> dict[str, Any]:
        return {
            "adapter": "litellm",
            "config_version": self.config_version,
            "embedding_key": self.embedding_key,
            "judge_model": self.judge_model,
            "provider": self.provider,
            "provider_model": self.provider_model,
            "seed": self.seed,
            "temperature": self.temperature,
            "top_p": self.top_p,
        }

    @property
    def config_sha256(self) -> str:
        raw = json.dumps(self.config_payload(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(raw).hexdigest()

    @classmethod
    def from_mapping(cls, env: dict[str, str]) -> EvalJudgeConfig:
        raw_model = env.get("EVAL_JUDGE_MODEL", "").strip()
        if not raw_model:
            raise EvalConfigError("EVAL_JUDGE_MODEL não configurado; não existe juiz padrão (ADR-0006)")
        prefix, sep, provider_model = raw_model.partition("/")
        if not sep or not provider_model.strip():
            raise EvalConfigError(
                "EVAL_JUDGE_MODEL inválido: deve incluir prefixo de provedor (ex.: openrouter/openai/gpt-6.1-sol)"
            )
        if prefix.lower() == "gemini" or "gemini" in provider_model.lower():
            raise EvalConfigError(
                "EVAL_JUDGE_MODEL não pode usar família Gemini (proibida autoavaliação; exige openrouter/)"
            )
        if prefix.lower() != "openrouter":
            raise EvalConfigError("EVAL_JUDGE_MODEL deve usar provedor openrouter/ via LiteLLM (ADR-0006)")
        embedding_model = env.get("EMBEDDING_MODEL", "gemini-embedding-2")
        embedding_dim = int(env.get("EMBEDDING_DIM", "3072"))
        format_version = env.get("EMBEDDING_FORMAT_VERSION", "fmt-v1")
        embedding_key = f"{embedding_model}@{embedding_dim}@{format_version}"
        if embedding_key != "gemini-embedding-2@3072@fmt-v1":
            raise EvalConfigError("Chave de embedding incompatível com ADR-0002")
        return cls(
            judge_model=raw_model,
            provider="openrouter",
            provider_model=provider_model.strip(),
            api_key=env.get("OPENROUTER_API_KEY", "").strip(),
            embedding_key=embedding_key,
        )

    @classmethod
    def from_env(cls, env_file: str | Path = ".env") -> EvalJudgeConfig:
        _guard_sdk_logging()
        env = read_settings(str(env_file))
        return cls.from_mapping(env)


CALIBRATION_PROTOCOL_VERSION = "ressalvas-calibration-paired-positive-negative-v1"


@dataclass(frozen=True)
class EvalThresholds:
    context_precision_with_reference: float = 0.80
    context_recall: float = 0.85
    min_case_context_precision: float = 0.65
    min_case_context_recall: float = 0.75
    deterministic_provenance_hit_rate: float = 1.00
    max_negative_control_score: float = 0.00
    max_allowed_regression_drop: float = 0.02
    approved_by_human: bool = False
    approval_ref: str | None = None

    def protocol_thresholds_payload(self) -> dict[str, Any]:
        return {
            "calibration_protocol_version": CALIBRATION_PROTOCOL_VERSION,
            "context_precision_with_reference": float(self.context_precision_with_reference),
            "context_recall": float(self.context_recall),
            "min_case_context_precision": float(self.min_case_context_precision),
            "min_case_context_recall": float(self.min_case_context_recall),
            "deterministic_provenance_hit_rate": float(self.deterministic_provenance_hit_rate),
            "max_negative_control_score": float(self.max_negative_control_score),
            "max_allowed_regression_drop": float(self.max_allowed_regression_drop),
        }

    def validate_ready(self) -> None:
        if not self.approved_by_human or not (self.approval_ref and self.approval_ref.strip()):
            raise EvalBlockedError(
                "Bloqueado: dataset, calibração e limiares ainda não foram aprovados pelo humano"
            )
        for name, value in (
            ("context_precision_with_reference", self.context_precision_with_reference),
            ("context_recall", self.context_recall),
            ("min_case_context_precision", self.min_case_context_precision),
            ("min_case_context_recall", self.min_case_context_recall),
            ("deterministic_provenance_hit_rate", self.deterministic_provenance_hit_rate),
        ):
            if (
                type(value) is bool
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not (0.0 < float(value) <= 1.0)
            ):
                raise EvalConfigError(f"Limiar {name} fora do intervalo (0.0, 1.0]: {value}")
        if (
            type(self.max_negative_control_score) is bool
            or not isinstance(self.max_negative_control_score, (int, float))
            or not math.isfinite(float(self.max_negative_control_score))
            or not (0.0 <= float(self.max_negative_control_score) <= 0.25)
        ):
            raise EvalConfigError(
                f"max_negative_control_score inválido: {self.max_negative_control_score}"
            )


@dataclass(frozen=True)
class RetrievedChunkRef:
    chunk_id: str
    document_version_id: str
    canonical_id: str | None
    section_path: tuple[str, ...]
    start_offset: int
    end_offset: int
    text_sha256: str
    decision_event_id: str
    decision_revision: int
    input_sha256: str
    span_ids: tuple[str, ...] = ()
    span_section_paths: tuple[tuple[str, ...], ...] = ()
    mapping_status: Literal["exact", "insufficient"] = "exact"

    def all_covered_section_paths(self) -> set[tuple[str, ...]]:
        paths: set[tuple[str, ...]] = set()
        if self.section_path:
            paths.add(tuple(self.section_path))
        for sp in self.span_section_paths:
            if sp:
                paths.add(tuple(sp))
        return paths

    def to_sanitized_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "document_version_id": self.document_version_id,
            "canonical_id": self.canonical_id,
            "section_path": list(self.section_path),
            "span_ids": list(self.span_ids),
            "span_section_paths": [list(p) for p in self.span_section_paths],
            "mapping_status": self.mapping_status,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "text_sha256": self.text_sha256,
            "decision_event_id": self.decision_event_id,
            "decision_revision": self.decision_revision,
            "input_sha256": self.input_sha256,
        }


@dataclass(frozen=True)
class RetrievalCaseRecord:
    case_id: str
    doc_id: str
    document_version_id: str
    canonical_id: str | None
    retrieved_contexts: tuple[str, ...]
    retrieved_chunks: tuple[RetrievedChunkRef, ...]


class CalibrationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    calibration_run_id: str = Field(min_length=1)
    timestamp_utc: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: str = Field(min_length=1)
    ragas_version: str = Field(min_length=1)
    litellm_version: str = Field(min_length=1)
    instructor_version: str = Field(min_length=1)
    judge_model: str = Field(min_length=1)
    judge_provider: str = Field(min_length=1)
    judge_config_version: str = Field(min_length=1)
    judge_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_key: str = Field(min_length=1)
    metric_prompts_sha256: dict[str, str]
    calibration_protocol_version: str = Field(min_length=1)
    protocol_thresholds: dict[str, Any]
    calibration_protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    positive_controls_count: int = Field(ge=1)
    negative_controls_count: int = Field(ge=1)
    thresholds_approval_ref: str = Field(min_length=1)


class EvaluationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    timestamp_utc: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: str = Field(min_length=1)
    ragas_version: str = Field(min_length=1)
    litellm_version: str = Field(min_length=1)
    instructor_version: str = Field(min_length=1)
    judge_model: str = Field(min_length=1)
    judge_provider: str = Field(min_length=1)
    judge_config_version: str = Field(min_length=1)
    judge_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    temperature: float
    top_p: float
    seed: int
    embedding_key: str = Field(min_length=1)
    metric_prompts_sha256: dict[str, str]
    calibration_protocol_version: str = Field(min_length=1)
    calibration_protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    thresholds_approval_ref: str | None = None
    calibration_run_id: str | None = None
    calibration_manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def compute_metric_prompts_sha256() -> dict[str, str]:
    cp_prompt = ContextPrecisionPrompt()
    cr_prompt = ContextRecallPrompt()
    cp_repr = json.dumps(
        {
            "metric_class": f"{ContextPrecisionWithReference.__module__}.{ContextPrecisionWithReference.__qualname__}",
            "prompt_class": f"{ContextPrecisionPrompt.__module__}.{ContextPrecisionPrompt.__qualname__}",
            "ragas_version": pkg_version("ragas"),
            "instruction": cp_prompt.instruction,
            "examples_count": len(cp_prompt.examples),
            "output_schema": ContextPrecisionPrompt.output_model.model_json_schema(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    cr_repr = json.dumps(
        {
            "metric_class": f"{ContextRecall.__module__}.{ContextRecall.__qualname__}",
            "prompt_class": f"{ContextRecallPrompt.__module__}.{ContextRecallPrompt.__qualname__}",
            "ragas_version": pkg_version("ragas"),
            "instruction": cr_prompt.instruction,
            "examples_count": len(cr_prompt.examples),
            "output_schema": ContextRecallPrompt.output_model.model_json_schema(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "context_precision_with_reference": sha256(cp_repr).hexdigest(),
        "context_recall": sha256(cr_repr).hexdigest(),
    }


def _canonical_json_sha256(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256(raw).hexdigest()


def _select_displaced_negative_control_record(
    case: EvaluationCaseSpec,
    retrieval_records: Sequence[RetrievalCaseRecord],
) -> RetrievalCaseRecord:
    """Seleciona deterministicamente o registro de controle negativo pareado de outro documento
    (`doc_id != case.doc_id`), ordenando por `(doc_id, case_id)` sem adicionar prefixo ou pista ao texto.
    """
    candidates = sorted(
        (r for r in retrieval_records if r.doc_id != case.doc_id and r.retrieved_contexts),
        key=lambda r: (r.doc_id, r.case_id),
    )
    if not candidates:
        raise EvalBlockedError(
            f"Não há documento distinto para construir controle negativo do caso {case.case_id}"
        )
    return candidates[0]


def compute_calibration_protocol_sha256(
    dataset_spec: DatasetSpec,
    thresholds: EvalThresholds,
) -> str:
    """Calcula o hash determinístico da receita de controles positivos/negativos, limiares efetivos e versão do RAGAS."""
    docs_in_order = sorted({c.doc_id for c in dataset_spec.cases})
    negative_pairing: list[dict[str, str]] = []
    for case in dataset_spec.cases:
        other_docs = [d for d in docs_in_order if d != case.doc_id]
        negative_pairing.append(
            {
                "case_id": case.case_id,
                "positive_doc_id": case.doc_id,
                "negative_displaced_doc_id": other_docs[0] if other_docs else "",
                "negative_context_transform": "identity_bytes_no_marker",
            }
        )
    recipe = {
        "calibration_protocol_version": CALIBRATION_PROTOCOL_VERSION,
        "dataset_id": dataset_spec.dataset_id,
        "dataset_version": dataset_spec.dataset_version,
        "ragas_version": pkg_version("ragas"),
        "applicable_metrics": list(dataset_spec.applicable_metrics),
        "metric_prompts_sha256": compute_metric_prompts_sha256(),
        "thresholds": thresholds.protocol_thresholds_payload(),
        "negative_pairing": negative_pairing,
    }
    return _canonical_json_sha256(recipe)


def build_evaluation_manifest(
    *,
    dataset_spec: DatasetSpec,
    dataset_sha256: str,
    judge_config: EvalJudgeConfig,
    thresholds: EvalThresholds | None = None,
    run_id: str | None = None,
    thresholds_approval_ref: str | None = None,
    calibration_run_id: str | None = None,
    calibration_manifest_sha256: str | None = None,
) -> EvaluationManifest:
    effective_thresholds = thresholds if thresholds is not None else EvalThresholds()
    return EvaluationManifest(
        run_id=run_id or f"ragas-run-{uuid4().hex[:12]}",
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        dataset_id=dataset_spec.dataset_id,
        dataset_version=dataset_spec.dataset_version,
        dataset_sha256=dataset_sha256,
        scope=dataset_spec.scope,
        ragas_version=pkg_version("ragas"),
        litellm_version=pkg_version("litellm"),
        instructor_version=pkg_version("instructor"),
        judge_model=judge_config.judge_model,
        judge_provider=judge_config.provider,
        judge_config_version=judge_config.config_version,
        judge_config_sha256=judge_config.config_sha256,
        temperature=judge_config.temperature,
        top_p=judge_config.top_p,
        seed=judge_config.seed,
        embedding_key=judge_config.embedding_key,
        metric_prompts_sha256=compute_metric_prompts_sha256(),
        calibration_protocol_version=CALIBRATION_PROTOCOL_VERSION,
        calibration_protocol_sha256=compute_calibration_protocol_sha256(
            dataset_spec, effective_thresholds
        ),
        thresholds_approval_ref=thresholds_approval_ref,
        calibration_run_id=calibration_run_id,
        calibration_manifest_sha256=calibration_manifest_sha256,
    )


def _build_real_litellm_instructor_client(judge_config: EvalJudgeConfig) -> Any:
    """Constrói o cliente assíncrono oficial `instructor.from_litellm(litellm.acompletion)`
    respeitando a guarda de logging de `training_llm` e sem expor credenciais.
    """
    _guard_sdk_logging()
    os.environ.setdefault("LITELLM_LOG", "WARNING")
    import litellm

    _guard_sdk_logging()
    api_key = judge_config.require_api_key()

    async def _guarded_acompletion(*args: Any, **kwargs: Any) -> Any:
        _guard_sdk_logging()
        call_kwargs = dict(kwargs)
        call_kwargs.setdefault("api_key", api_key)
        call_kwargs.setdefault("no-log", True)
        call_kwargs.setdefault("num_retries", 0)
        call_kwargs.setdefault("timeout", 60)
        return await litellm.acompletion(*args, **call_kwargs)

    return instructor.from_litellm(_guarded_acompletion)


def build_ragas_judge_And_metrics(
    judge_config: EvalJudgeConfig,
    *,
    client: Any | None = None,
) -> tuple[LiteLLMStructuredLLM, dict[str, Any]]:
    """Instancia o juiz via `ragas.llms.llm_factory(..., adapter='litellm')` e as métricas v2."""
    judge_config.require_api_key()
    active_client = client if client is not None else _build_real_litellm_instructor_client(judge_config)
    llm = llm_factory(
        model=judge_config.judge_model,
        provider=judge_config.provider,
        client=active_client,
        adapter="litellm",
        temperature=judge_config.temperature,
        top_p=judge_config.top_p,
        seed=judge_config.seed,
    )
    if not isinstance(llm, LiteLLMStructuredLLM):
        raise EvalConfigError(f"llm_factory não retornou LiteLLMStructuredLLM: {type(llm).__name__}")
    metrics = {
        "context_precision_with_reference": ContextPrecisionWithReference(llm=llm),
        "context_recall": ContextRecall(llm=llm),
    }
    return llm, metrics


def execute_scope_ingestion_and_retrieval(
    dataset_spec: DatasetSpec,
    *,
    dataset_dir: Path,
    engine: Any,
    processor: Any,
) -> list[RetrievalCaseRecord]:
    """Executa a ingestão real (`Ingestor`), reconciliação (`Reconciler`) e consulta segura
    (`SafeRetriever`) para todos os documentos e casos do dataset, carregando também os spans
    canônicos intersectados para verificação determinística de proveniência.
    """
    from training_ingestion import ChunkConfig, DoclingParser, Ingestor
    from training_ingestion.reconciliation import Reconciler
    from training_ingestion.retrieval.eligibility import SafeRetriever
    from training_lineage import LineageRepository

    chunk_cfg = ChunkConfig(
        target_tokens=dataset_spec.chunk_config.target_tokens,
        max_tokens=dataset_spec.chunk_config.max_tokens,
        overlap_tokens=dataset_spec.chunk_config.overlap_tokens,
    )
    ingestor = Ingestor(engine, parser=DoclingParser(), config=chunk_cfg, processor=processor)
    reconciler = Reconciler(engine, processor)
    retriever = SafeRetriever(engine, processor)

    doc_versions: dict[str, tuple[str, str | None]] = {}
    doc_spans_by_id: dict[str, dict[str, tuple[str, ...]]] = {}

    for doc in dataset_spec.documents:
        doc_path = (dataset_dir / doc.relative_path).resolve()
        result = _ingest_document_with_resumable_retries(
            ingestor, doc_path, doc.owner_id, document_id=doc.doc_id
        )
        rec_state = reconciler.reconcile_version(doc.owner_id, result.version.document_version_id)
        if rec_state.get("state") != "complete":
            raise EvalBlockedError(
                f"Reconciliação não completou para {doc.doc_id}: estado={rec_state.get('state')}"
            )
        with engine.connect() as conn:
            repo = LineageRepository(conn)
            canonical = repo.get_canonical_for_version(doc.owner_id, result.version.document_version_id)
            canonical_id = canonical.canonical_id if canonical is not None else None
            spans_map: dict[str, tuple[str, ...]] = {}
            if canonical_id is not None and hasattr(repo, "list_canonical_spans"):
                for sp in repo.list_canonical_spans(doc.owner_id, canonical_id):
                    spans_map[sp.span_id] = tuple(sp.section_path)
        doc_versions[doc.doc_id] = (result.version.document_version_id, canonical_id)
        doc_spans_by_id[doc.doc_id] = spans_map

    records: list[RetrievalCaseRecord] = []
    for case in dataset_spec.cases:
        version_id, canonical_id = doc_versions[case.doc_id]
        spans_map = doc_spans_by_id[case.doc_id]
        hits = retriever.search(case.owner_id, case.user_input, limit=dataset_spec.retrieval_top_k)
        if not hits:
            raise EvalBlockedError(
                f"SafeRetriever não retornou candidatos autorizados para o caso {case.case_id}"
            )
        chunk_ids = [c.chunk_id for c in hits]
        with engine.connect() as conn:
            repo = LineageRepository(conn)
            decisions = repo.get_effective_decisions(case.owner_id, chunk_ids)
            inputs = repo.load_embedding_inputs(
                case.owner_id, chunk_ids, embedding_key=processor.index.settings.embedding_key
            )
            provenance = repo.load_chunk_provenance(case.owner_id, chunk_ids)

        chunk_refs: list[RetrievedChunkRef] = []
        for hit in hits:
            decision = decisions[hit.chunk_id]
            emb_input = inputs[hit.chunk_id]
            prov = provenance.get(hit.chunk_id)
            span_ids = tuple(prov.span_ids) if prov is not None else ()
            span_paths: list[tuple[str, ...]] = []
            for sid in span_ids:
                sp_path = spans_map.get(sid)
                if sp_path and sp_path not in span_paths:
                    span_paths.append(sp_path)
            if not span_paths and hit.section_path:
                span_paths.append(tuple(hit.section_path))
            mapping_status: Literal["exact", "insufficient"] = (
                prov.mapping_status if prov is not None else "insufficient"
            )
            chunk_refs.append(
                RetrievedChunkRef(
                    chunk_id=hit.chunk_id,
                    document_version_id=hit.document_version_id,
                    canonical_id=prov.canonical_id if prov is not None else canonical_id,
                    section_path=tuple(hit.section_path),
                    start_offset=hit.start_offset,
                    end_offset=hit.end_offset,
                    text_sha256=sha256(hit.text.encode("utf-8")).hexdigest(),
                    decision_event_id=decision.decision_event_id,
                    decision_revision=decision.revision,
                    input_sha256=emb_input.input_sha256,
                    span_ids=span_ids,
                    span_section_paths=tuple(span_paths),
                    mapping_status=mapping_status,
                )
            )
        records.append(
            RetrievalCaseRecord(
                case_id=case.case_id,
                doc_id=case.doc_id,
                document_version_id=version_id,
                canonical_id=canonical_id,
                retrieved_contexts=tuple(h.text for h in hits),
                retrieved_chunks=tuple(chunk_refs),
            )
        )
    return records


def _validate_metric_score_value(raw_value: Any) -> float:
    """Valida estritamente que o score retornado pelo RAGAS é numérico finito em [0.0, 1.0]."""
    if (
        raw_value is None
        or type(raw_value) is bool
        or not isinstance(raw_value, (int, float))
    ):
        raise EvalProviderError("invalid_output")
    val = float(raw_value)
    if not math.isfinite(val) or val < 0.0 or val > 1.0:
        raise EvalProviderError("invalid_output")
    return val


async def _ascore_case_metrics(
    metrics: dict[str, Any],
    *,
    user_input: str,
    reference: str,
    retrieved_contexts: list[str],
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for metric_name, metric_obj in metrics.items():
        try:
            result = await metric_obj.ascore(
                user_input=user_input,
                reference=reference,
                retrieved_contexts=retrieved_contexts,
            )
        except EvalProviderError:
            raise
        except Exception as exc:
            raise _sanitize_provider_exception(exc) from None
        val = _validate_metric_score_value(getattr(result, "value", None))
        scores[metric_name] = val
    return scores


def _evaluate_case_deterministic_provenance(
    case: EvaluationCaseSpec,
    record: RetrievalCaseRecord,
) -> tuple[bool, list[str]]:
    """Verifica deterministicamente se os chunks recuperados:
    1. Possuem `mapping_status == 'exact'`, `canonical_id` não nulo e `start_offset < end_offset`;
    2. Cobrem todas as substrings esperadas (`expected_evidence_substrings`);
    3. Cobrem todos os caminhos de seção esperados (`expected_section_paths`) nos spans ou `section_path`.
    """
    reasons: list[str] = []
    if not record.retrieved_chunks or len(record.retrieved_chunks) != len(record.retrieved_contexts):
        reasons.append(f"{case.case_id}: retrieved_chunks ausentes ou desalinhados com retrieved_contexts")
        return False, reasons

    covered_paths: set[tuple[str, ...]] = set()
    for chunk_ref, ctx_text in zip(record.retrieved_chunks, record.retrieved_contexts):
        actual_sha = sha256(ctx_text.encode("utf-8")).hexdigest()
        if chunk_ref.text_sha256 != actual_sha:
            reasons.append(f"{case.case_id}: chunk {chunk_ref.chunk_id} com text_sha256 divergente")
        if (
            chunk_ref.mapping_status != "exact"
            or not chunk_ref.canonical_id
            or chunk_ref.end_offset <= chunk_ref.start_offset
        ):
            reasons.append(
                f"{case.case_id}: chunk {chunk_ref.chunk_id} sem proveniência canônica exata "
                f"(mapping_status={chunk_ref.mapping_status!r})"
            )
        covered_paths.update(chunk_ref.all_covered_section_paths())

    combined_text = "\n".join(record.retrieved_contexts)
    missing_subs = [sub for sub in case.expected_evidence_substrings if sub not in combined_text]
    if missing_subs:
        reasons.append(
            f"{case.case_id}: deterministic_provenance falhou ({len(missing_subs)} substring(s) de evidência ausente(s))"
        )

    missing_paths = [
        list(exp_path)
        for exp_path in case.expected_section_paths
        if tuple(exp_path) not in covered_paths
    ]
    if missing_paths:
        reasons.append(
            f"{case.case_id}: deterministic_provenance falhou (expected_section_paths ausentes nos spans/chunks: {missing_paths})"
        )

    return len(reasons) == 0, reasons


def validate_calibration_report(
    calibration_report: dict[str, Any],
    *,
    dataset_spec: DatasetSpec,
    dataset_sha256: str,
    judge_config: EvalJudgeConfig,
    thresholds: EvalThresholds | None = None,
) -> tuple[str, str]:
    """Valida que um relatório de calibração executada passou nos controles positivos e negativos
    e é compatível com o dataset, o modelo/config do juiz, a versão do RAGAS, os prompts das métricas
    e os limiares/protocolo efetivos (`calibration_protocol_sha256` e `protocol_thresholds`).
    Retorna `(calibration_run_id, calibration_manifest_sha256)`.
    """
    if not isinstance(calibration_report, dict):
        raise EvalBlockedError("Relatório de calibração inválido ou ausente")
    if calibration_report.get("status") != "Passou":
        raise EvalBlockedError(
            f"Calibração do juiz não está aprovada/Passou (status={calibration_report.get('status')!r})"
        )
    if not calibration_report.get("positive_controls_passed") or not calibration_report.get(
        "negative_controls_passed"
    ):
        raise EvalBlockedError("Calibração do juiz falhou nos controles positivos ou negativos")

    raw_manifest = calibration_report.get("manifest")
    if not isinstance(raw_manifest, dict):
        raise EvalBlockedError("Relatório de calibração sem manifesto estruturado")
    try:
        manifest = CalibrationManifest.model_validate(raw_manifest)
    except Exception as exc:
        raise EvalBlockedError(f"Manifesto de calibração inválido: {exc}") from None

    effective_thresholds = thresholds if thresholds is not None else EvalThresholds()
    expected_prompts = compute_metric_prompts_sha256()
    expected_protocol_sha = compute_calibration_protocol_sha256(dataset_spec, effective_thresholds)
    checks: list[tuple[str, Any, Any]] = [
        ("dataset_id", manifest.dataset_id, dataset_spec.dataset_id),
        ("dataset_version", manifest.dataset_version, dataset_spec.dataset_version),
        ("dataset_sha256", manifest.dataset_sha256, dataset_sha256),
        ("scope", manifest.scope, dataset_spec.scope),
        ("ragas_version", manifest.ragas_version, pkg_version("ragas")),
        ("judge_model", manifest.judge_model, judge_config.judge_model),
        ("judge_provider", manifest.judge_provider, judge_config.provider),
        ("judge_config_sha256", manifest.judge_config_sha256, judge_config.config_sha256),
        ("embedding_key", manifest.embedding_key, judge_config.embedding_key),
        ("metric_prompts_sha256", manifest.metric_prompts_sha256, expected_prompts),
        ("calibration_protocol_version", manifest.calibration_protocol_version, CALIBRATION_PROTOCOL_VERSION),
        ("protocol_thresholds", manifest.protocol_thresholds, effective_thresholds.protocol_thresholds_payload()),
        ("calibration_protocol_sha256", manifest.calibration_protocol_sha256, expected_protocol_sha),
    ]
    if effective_thresholds.approval_ref is not None:
        checks.append(
            ("thresholds_approval_ref", manifest.thresholds_approval_ref, effective_thresholds.approval_ref)
        )
    for field_name, actual, expected in checks:
        if actual != expected:
            raise EvalBlockedError(
                f"Manifesto de calibração incompatível no campo '{field_name}': "
                f"calibração={actual!r} != esperado={expected!r}"
            )
    manifest_sha = _canonical_json_sha256(manifest.model_dump())
    return manifest.calibration_run_id, manifest_sha


def run_judge_calibration(
    *,
    dataset_spec: DatasetSpec,
    dataset_sha256: str,
    judge_config: EvalJudgeConfig,
    retrieval_records: Sequence[RetrievalCaseRecord],
    thresholds: EvalThresholds,
    client: Any | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Executa o protocolo real de calibração do juiz (`EVAL_JUDGE_MODEL`) com controles positivos
    (chunks recuperados reais do documento correto) e controles negativos (chunks originais deslocados
    de outro documento do corpus, SEM prefixo ou marcador que revele o rótulo esperado ao juiz),
    falhando fechado caso o juiz não discrimine ou a proveniência determinística falhe.
    """
    thresholds.validate_ready()
    judge_config.require_api_key()
    _, metrics = build_ragas_judge_And_metrics(judge_config, client=client)

    records_by_case = {r.case_id: r for r in retrieval_records}
    missing_cases = [c.case_id for c in dataset_spec.cases if c.case_id not in records_by_case]
    if missing_cases:
        raise EvalBlockedError(f"Casos do dataset sem registros para calibração: {missing_cases}")

    protocol_sha = compute_calibration_protocol_sha256(dataset_spec, thresholds)
    manifest = CalibrationManifest(
        calibration_run_id=run_id or f"ragas-calib-{uuid4().hex[:12]}",
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        dataset_id=dataset_spec.dataset_id,
        dataset_version=dataset_spec.dataset_version,
        dataset_sha256=dataset_sha256,
        scope=dataset_spec.scope,
        ragas_version=pkg_version("ragas"),
        litellm_version=pkg_version("litellm"),
        instructor_version=pkg_version("instructor"),
        judge_model=judge_config.judge_model,
        judge_provider=judge_config.provider,
        judge_config_version=judge_config.config_version,
        judge_config_sha256=judge_config.config_sha256,
        embedding_key=judge_config.embedding_key,
        metric_prompts_sha256=compute_metric_prompts_sha256(),
        calibration_protocol_version=CALIBRATION_PROTOCOL_VERSION,
        protocol_thresholds=thresholds.protocol_thresholds_payload(),
        calibration_protocol_sha256=protocol_sha,
        positive_controls_count=len(dataset_spec.cases),
        negative_controls_count=len(dataset_spec.cases),
        thresholds_approval_ref=thresholds.approval_ref or "",
    )

    positive_controls: list[dict[str, Any]] = []
    negative_controls: list[dict[str, Any]] = []
    violations: list[str] = []

    for case in dataset_spec.cases:
        pos_record = records_by_case[case.case_id]
        prov_ok, prov_reasons = _evaluate_case_deterministic_provenance(case, pos_record)
        if not prov_ok:
            violations.extend(prov_reasons)

        pos_scores = asyncio.run(
            _ascore_case_metrics(
                metrics,
                user_input=case.user_input,
                reference=case.reference,
                retrieved_contexts=list(pos_record.retrieved_contexts),
            )
        )
        pos_case_ok = (
            prov_ok
            and pos_scores["context_precision_with_reference"] >= thresholds.min_case_context_precision
            and pos_scores["context_recall"] >= thresholds.min_case_context_recall
        )
        if not pos_case_ok:
            violations.append(
                f"positive_control:{case.case_id}: scores={pos_scores}, prov_ok={prov_ok}"
            )
        positive_controls.append(
            {
                "case_id": case.case_id,
                "control_kind": "positive_in_domain",
                "doc_id": case.doc_id,
                "deterministic_provenance_ok": prov_ok,
                "scores": pos_scores,
                "passed": pos_case_ok,
            }
        )

        # Controle negativo pareado: contexto original deslocado de outro documento (doc_id diferente),
        # enviado sem qualquer prefixo ou marcador que revele o rótulo esperado ao juiz.
        displaced_record = _select_displaced_negative_control_record(case, retrieval_records)
        displaced_contexts = list(displaced_record.retrieved_contexts)
        neg_scores = asyncio.run(
            _ascore_case_metrics(
                metrics,
                user_input=case.user_input,
                reference=case.reference,
                retrieved_contexts=displaced_contexts,
            )
        )
        neg_case_ok = (
            neg_scores["context_precision_with_reference"] <= thresholds.max_negative_control_score
            and neg_scores["context_recall"] <= thresholds.max_negative_control_score
        )
        if not neg_case_ok:
            violations.append(
                f"negative_control:{case.case_id} (displaced={displaced_record.doc_id}): "
                f"juiz não discriminou contexto irrelevante: {neg_scores}"
            )
        negative_controls.append(
            {
                "case_id": case.case_id,
                "control_kind": "negative_displaced_document",
                "source_doc_id": case.doc_id,
                "displaced_doc_id": displaced_record.doc_id,
                "displaced_case_id": displaced_record.case_id,
                "displaced_context_sha256s": [
                    sha256(ctx.encode("utf-8")).hexdigest() for ctx in displaced_contexts
                ],
                "scores": neg_scores,
                "passed": neg_case_ok,
            }
        )

    pos_avg_cp = sum(c["scores"]["context_precision_with_reference"] for c in positive_controls) / len(
        positive_controls
    )
    pos_avg_cr = sum(c["scores"]["context_recall"] for c in positive_controls) / len(
        positive_controls
    )
    if pos_avg_cp < thresholds.context_precision_with_reference:
        violations.append(
            f"positive_controls_aggregate: context_precision_with_reference={pos_avg_cp:.4f} "
            f"< {thresholds.context_precision_with_reference:.4f}"
        )
    if pos_avg_cr < thresholds.context_recall:
        violations.append(
            f"positive_controls_aggregate: context_recall={pos_avg_cr:.4f} "
            f"< {thresholds.context_recall:.4f}"
        )

    positive_controls_passed = (
        all(c["passed"] for c in positive_controls)
        and pos_avg_cp >= thresholds.context_precision_with_reference
        and pos_avg_cr >= thresholds.context_recall
    )
    negative_controls_passed = all(c["passed"] for c in negative_controls)
    status: Literal["Passou", "Falhou"] = (
        "Passou" if (positive_controls_passed and negative_controls_passed and not violations) else "Falhou"
    )

    manifest_dict = manifest.model_dump()
    return {
        "mode": "judge_calibration",
        "status": status,
        "manifest": manifest_dict,
        "manifest_sha256": _canonical_json_sha256(manifest_dict),
        "positive_controls_passed": positive_controls_passed,
        "negative_controls_passed": negative_controls_passed,
        "positive_aggregate_scores": {
            "context_precision_with_reference": pos_avg_cp,
            "context_recall": pos_avg_cr,
        },
        "positive_controls": positive_controls,
        "negative_controls": negative_controls,
        "violations": violations,
    }


def run_ragas_evaluation(
    *,
    dataset_spec: DatasetSpec,
    dataset_sha256: str,
    judge_config: EvalJudgeConfig,
    retrieval_records: Sequence[RetrievalCaseRecord],
    thresholds: EvalThresholds,
    calibration_report: dict[str, Any] | None = None,
    require_calibration: bool = False,
    baseline_report: dict[str, Any] | None = None,
    client: Any | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Executa as métricas reais do RAGAS sobre os resultados recuperados, valida cobertura
    determinística de proveniência (`1.00`), vincula o manifesto de calibração quando exigido
    e agrega eventual diff contra baseline ao `status` final.
    """
    thresholds.validate_ready()
    judge_config.require_api_key()

    calibration_run_id: str | None = None
    calibration_manifest_sha256: str | None = None
    if require_calibration and calibration_report is None:
        raise EvalBlockedError(
            "Bloqueado: estabelecimento de baseline exige relatório de calibração aprovada (--calibration-report)"
        )
    if calibration_report is not None:
        calibration_run_id, calibration_manifest_sha256 = validate_calibration_report(
            calibration_report,
            dataset_spec=dataset_spec,
            dataset_sha256=dataset_sha256,
            judge_config=judge_config,
            thresholds=thresholds,
        )

    _, metrics = build_ragas_judge_And_metrics(judge_config, client=client)

    records_by_case = {r.case_id: r for r in retrieval_records}
    missing_cases = [c.case_id for c in dataset_spec.cases if c.case_id not in records_by_case]
    if missing_cases:
        raise EvalBlockedError(f"Casos do dataset sem registros de recuperação: {missing_cases}")

    manifest = build_evaluation_manifest(
        dataset_spec=dataset_spec,
        dataset_sha256=dataset_sha256,
        judge_config=judge_config,
        thresholds=thresholds,
        run_id=run_id,
        thresholds_approval_ref=thresholds.approval_ref,
        calibration_run_id=calibration_run_id,
        calibration_manifest_sha256=calibration_manifest_sha256,
    )

    case_results: list[dict[str, Any]] = []
    threshold_violations: list[str] = []
    provenance_hits = 0

    for case in dataset_spec.cases:
        record = records_by_case[case.case_id]
        if not record.retrieved_contexts:
            raise EvalBlockedError(f"Caso {case.case_id} não possui retrieved_contexts")
        scores = asyncio.run(
            _ascore_case_metrics(
                metrics,
                user_input=case.user_input,
                reference=case.reference,
                retrieved_contexts=list(record.retrieved_contexts),
            )
        )
        prov_ok, prov_reasons = _evaluate_case_deterministic_provenance(case, record)
        if prov_ok:
            provenance_hits += 1
        else:
            threshold_violations.extend(prov_reasons)

        if scores["context_precision_with_reference"] < thresholds.min_case_context_precision:
            threshold_violations.append(
                f"{case.case_id}: context_precision_with_reference={scores['context_precision_with_reference']:.4f} "
                f"< piso_caso={thresholds.min_case_context_precision:.4f}"
            )
        if scores["context_recall"] < thresholds.min_case_context_recall:
            threshold_violations.append(
                f"{case.case_id}: context_recall={scores['context_recall']:.4f} "
                f"< piso_caso={thresholds.min_case_context_recall:.4f}"
            )

        case_results.append(
            {
                "case_id": case.case_id,
                "doc_id": case.doc_id,
                "owner_id": case.owner_id,
                "user_input_sha256": sha256(case.user_input.encode("utf-8")).hexdigest(),
                "reference_sha256": sha256(case.reference.encode("utf-8")).hexdigest(),
                "document_version_id": record.document_version_id,
                "canonical_id": record.canonical_id,
                "retrieved_context_sha256s": [
                    sha256(ctx.encode("utf-8")).hexdigest() for ctx in record.retrieved_contexts
                ],
                "retrieved_chunks": [c.to_sanitized_dict() for c in record.retrieved_chunks],
                "deterministic_evidence_covered": prov_ok,
                "deterministic_provenance_errors": prov_reasons,
                "scores": scores,
            }
        )

    agg_precision = sum(c["scores"]["context_precision_with_reference"] for c in case_results) / len(
        case_results
    )
    agg_recall = sum(c["scores"]["context_recall"] for c in case_results) / len(case_results)
    provenance_hit_rate = provenance_hits / len(case_results)

    if agg_precision < thresholds.context_precision_with_reference:
        threshold_violations.append(
            f"aggregate: context_precision_with_reference={agg_precision:.4f} "
            f"< limiar={thresholds.context_precision_with_reference:.4f}"
        )
    if agg_recall < thresholds.context_recall:
        threshold_violations.append(
            f"aggregate: context_recall={agg_recall:.4f} "
            f"< limiar={thresholds.context_recall:.4f}"
        )
    if provenance_hit_rate < thresholds.deterministic_provenance_hit_rate:
        threshold_violations.append(
            f"aggregate: deterministic_provenance_hit_rate={provenance_hit_rate:.4f} "
            f"< limiar={thresholds.deterministic_provenance_hit_rate:.4f}"
        )

    candidate_status: Literal["Passou", "Falhou"] = "Passou" if not threshold_violations else "Falhou"

    report: dict[str, Any] = {
        "manifest": manifest.model_dump(),
        "status": candidate_status,
        "candidate_status": candidate_status,
        "scope_evaluated": dataset_spec.scope,
        "global_pipeline_success": False,
        "global_pipeline_note": (
            "Avaliação restrita ao escopo de ingestão, reconciliação e consulta segura (SafeRetriever). "
            "Estágios de geração (E8) e juiz de saída (E10) não estão implementados neste corte."
        ),
        "aggregate_scores": {
            "context_precision_with_reference": agg_precision,
            "context_recall": agg_recall,
        },
        "deterministic_provenance_hit_rate": provenance_hit_rate,
        "not_applicable_metrics": {
            k: v.model_dump() for k, v in dataset_spec.not_applicable_metrics.items()
        },
        "thresholds": {
            "context_precision_with_reference": thresholds.context_precision_with_reference,
            "context_recall": thresholds.context_recall,
            "min_case_context_precision": thresholds.min_case_context_precision,
            "min_case_context_recall": thresholds.min_case_context_recall,
            "deterministic_provenance_hit_rate": thresholds.deterministic_provenance_hit_rate,
            "approval_ref": thresholds.approval_ref,
        },
        "threshold_violations": threshold_violations,
        "case_results": case_results,
        "security_regression_reference": dataset_spec.security_regression_reference.model_dump(),
    }

    if baseline_report is not None:
        diff = compare_with_baseline(baseline_report, report, thresholds=thresholds)
        report["baseline_diff"] = diff
        if diff["status"] != "Passou":
            report["status"] = "Falhou"

    return report


def compare_with_baseline(
    baseline_report: dict[str, Any],
    candidate_report: dict[str, Any],
    *,
    thresholds: EvalThresholds,
) -> dict[str, Any]:
    """Compara uma execução candidata contra uma baseline compatível (mesmo dataset, juiz, prompts e embedding_key)."""
    thresholds.validate_ready()
    base_manifest = baseline_report.get("manifest")
    cand_manifest = candidate_report.get("manifest")
    if not isinstance(base_manifest, dict) or not isinstance(cand_manifest, dict):
        raise EvalBlockedError("Relatório de baseline ou candidato sem manifesto estruturado")

    compatibility_keys = (
        "dataset_id",
        "dataset_version",
        "dataset_sha256",
        "scope",
        "ragas_version",
        "judge_model",
        "judge_provider",
        "judge_config_sha256",
        "embedding_key",
        "metric_prompts_sha256",
        "calibration_protocol_version",
        "calibration_protocol_sha256",
        "calibration_manifest_sha256",
    )
    for key in compatibility_keys:
        if base_manifest.get(key) != cand_manifest.get(key):
            raise EvalBlockedError(
                f"Baseline incompatível no campo '{key}': "
                f"baseline={base_manifest.get(key)!r} != candidato={cand_manifest.get(key)!r}"
            )

    expected_protocol_sha = compute_calibration_protocol_sha256(
        load_dataset_spec(DEFAULT_DATASET_PATH)[0]
        if cand_manifest.get("dataset_id") == "ressalvas-recuperacao-segura-v1"
        else DatasetSpec.model_validate(
            json.loads(DEFAULT_DATASET_PATH.read_text(encoding="utf-8"))
        ),
        thresholds,
    )
    if cand_manifest.get("calibration_protocol_sha256") != expected_protocol_sha:
        raise EvalBlockedError(
            "Baseline/candidato incompatível com os limiares e protocolo atuais "
            f"(calibration_protocol_sha256={cand_manifest.get('calibration_protocol_sha256')!r} "
            f"!= esperado={expected_protocol_sha!r})"
        )

    if baseline_report.get("status") != "Passou":
        raise EvalBlockedError(
            f"Baseline informada não possui status 'Passou' (atual: {baseline_report.get('status')!r})"
        )

    base_scores = baseline_report.get("aggregate_scores", {})
    cand_scores = candidate_report.get("aggregate_scores", {})
    metric_diffs: dict[str, dict[str, Any]] = {}
    regressions: list[str] = []

    for metric_name, min_threshold in (
        ("context_precision_with_reference", thresholds.context_precision_with_reference),
        ("context_recall", thresholds.context_recall),
    ):
        if metric_name not in base_scores or metric_name not in cand_scores:
            raise EvalBlockedError(f"Métrica {metric_name} ausente para comparação de baseline")
        b_val = _validate_metric_score_value(base_scores[metric_name])
        c_val = _validate_metric_score_value(cand_scores[metric_name])
        delta = c_val - b_val
        passed_threshold = c_val >= min_threshold
        passed_drop = delta >= -thresholds.max_allowed_regression_drop
        if not passed_threshold or not passed_drop:
            regressions.append(
                f"{metric_name}: baseline={b_val:.4f}, candidato={c_val:.4f}, "
                f"delta={delta:+.4f}, limiar={min_threshold:.4f}"
            )
        metric_diffs[metric_name] = {
            "baseline": b_val,
            "candidate": c_val,
            "delta": delta,
            "threshold": min_threshold,
            "passed": passed_threshold and passed_drop,
        }

    base_hit_rate = _validate_metric_score_value(
        baseline_report.get("deterministic_provenance_hit_rate", 1.0)
    )
    cand_hit_rate = _validate_metric_score_value(
        candidate_report.get("deterministic_provenance_hit_rate", 0.0)
    )
    if cand_hit_rate < thresholds.deterministic_provenance_hit_rate or cand_hit_rate < base_hit_rate:
        regressions.append(
            f"deterministic_provenance_hit_rate: baseline={base_hit_rate:.4f}, "
            f"candidato={cand_hit_rate:.4f}, limiar={thresholds.deterministic_provenance_hit_rate:.4f}"
        )

    if candidate_report.get("threshold_violations"):
        regressions.extend(candidate_report["threshold_violations"])

    status: Literal["Passou", "Falhou"] = "Passou" if not regressions else "Falhou"
    return {
        "baseline_run_id": base_manifest["run_id"],
        "candidate_run_id": cand_manifest["run_id"],
        "dataset_sha256": cand_manifest["dataset_sha256"],
        "judge_model": cand_manifest["judge_model"],
        "judge_config_sha256": cand_manifest["judge_config_sha256"],
        "status": status,
        "metric_diffs": metric_diffs,
        "regressions": regressions,
    }


def _start_disposable_qdrant_container() -> tuple[str, str]:
    """Inicia um container Qdrant real descartável (`qdrant/qdrant:v1.20.2`), isolado da coleção compartilhada."""
    container_name = "eval-disposable-qdrant-" + uuid4().hex
    subprocess.run(
        [
            "docker",
            "run",
            "--pull=never",
            "--rm",
            "-d",
            "--name",
            container_name,
            "-p",
            "127.0.0.1::6333",
            "-e",
            "QDRANT__TELEMETRY_DISABLED=true",
            "qdrant/qdrant:v1.20.2",
        ],
        check=True,
        capture_output=True,
    )
    try:
        addr = subprocess.check_output(
            ["docker", "port", container_name, "6333/tcp"], text=True
        ).strip()
        qdrant_url = "http://" + addr
        deadline = time.monotonic() + 30.0
        while True:
            try:
                with urlopen(qdrant_url + "/readyz", timeout=1) as resp:
                    if resp.status == 200:
                        break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise EvalBlockedError("Qdrant descartável de avaliação não ficou pronto em 30s")
            time.sleep(0.2)
        return container_name, qdrant_url
    except Exception:
        _stop_disposable_qdrant_container(container_name)
        raise


def _stop_disposable_qdrant_container(container_name: str) -> None:
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        check=True,
        capture_output=True,
    )


@contextmanager
def _disposable_evaluation_sandbox(
    *,
    env_file: Path,
    init_pipeline: bool = True,
) -> Iterator[tuple[Any, Any]]:
    """Context manager transacional que provisiona Qdrant descartável + DB PostgreSQL descartável.

    Se qualquer etapa falhar (inclusive antes ou durante a ingestão parcial), todos os recursos
    alocados são fechados e destruídos em ordem reversa, sem tocar na coleção compartilhada e sem
    silenciar erros de limpeza (`except Exception: pass` proibido).
    """
    from dataclasses import replace
    from qdrant_client import QdrantClient
    from sqlalchemy import create_engine, text as sql_text
    from training_ingestion.indexing import IndexingPipeline, QdrantIndex
    from training_lineage import create_lineage_engine, migrate
    from training_llm import GeminiModels

    container_name: str | None = None
    admin: Any | None = None
    db_created = False
    temp_db = "eval_ressalvas_" + uuid4().hex
    engine: Any | None = None
    qdrant_client: Any | None = None
    model_client: Any | None = None
    cleanup_errors: list[str] = []

    try:
        container_name, qdrant_url = _start_disposable_qdrant_container()
        admin = create_lineage_engine()
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(sql_text(f'CREATE DATABASE "{temp_db}" TEMPLATE template0'))
        db_created = True

        engine = create_engine(admin.url.set(database=temp_db), hide_parameters=True)
        migrate(engine, "head")

        pipeline: Any = None
        if init_pipeline:
            base_settings = ModelSettings.from_env(str(env_file))
            isolated_settings = replace(base_settings, qdrant_url=qdrant_url)
            model_client = GeminiModels(isolated_settings)
            qdrant_client = QdrantClient(url=qdrant_url, cloud_inference=True)
            pipeline = IndexingPipeline(model_client, QdrantIndex(qdrant_client, isolated_settings))

        yield engine, pipeline
    finally:
        if qdrant_client is not None:
            try:
                qdrant_client.close()
            except Exception as exc:
                cleanup_errors.append(f"qdrant_client.close: {type(exc).__name__}")
        if model_client is not None and hasattr(model_client, "client"):
            try:
                model_client.client.close()
            except Exception as exc:
                cleanup_errors.append(f"model_client.close: {type(exc).__name__}")
        if engine is not None:
            try:
                engine.dispose()
            except Exception as exc:
                cleanup_errors.append(f"engine.dispose: {type(exc).__name__}")
        if db_created and admin is not None:
            try:
                with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    conn.execute(sql_text(f'DROP DATABASE IF EXISTS "{temp_db}" WITH (FORCE)'))
            except Exception as exc:
                cleanup_errors.append(f"drop_database: {type(exc).__name__}")
        if admin is not None:
            try:
                admin.dispose()
            except Exception as exc:
                cleanup_errors.append(f"admin.dispose: {type(exc).__name__}")
        if container_name is not None:
            try:
                _stop_disposable_qdrant_container(container_name)
            except Exception as exc:
                cleanup_errors.append(f"stop_qdrant_container: {type(exc).__name__}")

        if cleanup_errors:
            if sys.exc_info()[0] is None:
                raise EvalError(f"Falha na limpeza da sandbox descartável: {', '.join(cleanup_errors)}")
            active_exc = sys.exc_info()[1]
            if active_exc is not None:
                active_exc.add_note(
                    f"Erros adicionais durante limpeza da sandbox descartável: {', '.join(cleanup_errors)}"
                )


def execute_isolated_evaluation_or_calibration(
    *,
    mode: Literal["calibrate", "evaluate"],
    dataset_spec: DatasetSpec,
    dataset_sha256: str,
    dataset_dir: Path,
    env_file: Path,
    judge_config: EvalJudgeConfig,
    thresholds: EvalThresholds,
    calibration_report: dict[str, Any] | None = None,
    baseline_report: dict[str, Any] | None = None,
    sandbox_runner: Any | None = None,
) -> dict[str, Any]:
    """Executa calibração ou avaliação completa dentro da sandbox descartável (Postgres + Qdrant)."""
    thresholds.validate_ready()
    judge_config.require_api_key()
    if mode == "evaluate":
        if calibration_report is None:
            raise EvalBlockedError(
                "Bloqueado: execução real (--execute) exige relatório de calibração aprovada (--calibration-report)"
            )
        validate_calibration_report(
            calibration_report,
            dataset_spec=dataset_spec,
            dataset_sha256=dataset_sha256,
            judge_config=judge_config,
            thresholds=thresholds,
        )

    if sandbox_runner is not None:
        return sandbox_runner(
            mode=mode,
            dataset_spec=dataset_spec,
            dataset_sha256=dataset_sha256,
            dataset_dir=dataset_dir,
            env_file=env_file,
            judge_config=judge_config,
            thresholds=thresholds,
            calibration_report=calibration_report,
            baseline_report=baseline_report,
        )

    with _disposable_evaluation_sandbox(env_file=env_file, init_pipeline=True) as (engine, pipeline):
        records = execute_scope_ingestion_and_retrieval(
            dataset_spec,
            dataset_dir=dataset_dir,
            engine=engine,
            processor=pipeline,
        )
        if mode == "calibrate":
            return run_judge_calibration(
                dataset_spec=dataset_spec,
                dataset_sha256=dataset_sha256,
                judge_config=judge_config,
                retrieval_records=records,
                thresholds=thresholds,
            )
        return run_ragas_evaluation(
            dataset_spec=dataset_spec,
            dataset_sha256=dataset_sha256,
            judge_config=judge_config,
            retrieval_records=records,
            thresholds=thresholds,
            calibration_report=calibration_report,
            require_calibration=True,
            baseline_report=baseline_report,
        )


def inspect_security_regression_evidence(dataset_spec: DatasetSpec) -> dict[str, Any]:
    """Lê e referencia a evidência de SEC-ATQ-001 / C09 sem repetir chamadas ao juiz de ingestão."""
    ref_path = (REPO_ROOT / dataset_spec.security_regression_reference.evidence_ref).resolve()
    if not ref_path.is_file():
        return {
            "requirement_id": dataset_spec.security_regression_reference.requirement_id,
            "evidence_ref": dataset_spec.security_regression_reference.evidence_ref,
            "exists": False,
            "status": "Bloqueado",
            "reason": "Arquivo de evidência referenciado não encontrado",
        }
    raw_bytes = ref_path.read_bytes()
    evidence_sha256 = sha256(raw_bytes).hexdigest()
    payload = json.loads(raw_bytes.decode("utf-8"))
    results = payload.get("results", [])
    statuses = {r.get("status") for r in results if isinstance(r, dict)}
    overall = "Passou" if statuses == {"Passou"} and len(results) >= 20 else "Bloqueado"
    return {
        "requirement_id": dataset_spec.security_regression_reference.requirement_id,
        "case_group": dataset_spec.security_regression_reference.case_group,
        "evidence_ref": dataset_spec.security_regression_reference.evidence_ref,
        "evidence_sha256": evidence_sha256,
        "exists": True,
        "recorded_cases": len(results),
        "status": overall,
    }


def run_preflight(
    *,
    dataset_path: str | Path = DEFAULT_DATASET_PATH,
    env_file: str | Path = ".env",
    thresholds_approved: bool = False,
    approval_ref: str | None = None,
    calibration_report_path: str | Path | None = None,
) -> dict[str, Any]:
    """Executa pré-flight determinístico (sem chamadas LLM e sem produzir score de avaliação)."""
    _guard_sdk_logging()
    spec, dataset_sha = load_dataset_spec(dataset_path)
    env = read_settings(str(env_file))
    judge_config = EvalJudgeConfig.from_mapping(env)
    gemini_key_present = bool(env.get("GEMINI_API_KEY", "").strip())
    openrouter_key_present = judge_config.has_api_key

    blockers: list[str] = []
    if not gemini_key_present:
        blockers.append("GEMINI_API_KEY ausente no ambiente local (necessária para embeddings de ingestão/consulta)")
    if not openrouter_key_present:
        blockers.append(
            f"OPENROUTER_API_KEY ausente no ambiente local (necessária para o juiz {judge_config.judge_model})"
        )
    if not thresholds_approved or not (approval_ref and approval_ref.strip()):
        blockers.append(
            "Dataset, calibração e limiares propostos em evals/datasets/ressalvas/ aguardam aprovação explícita do humano"
        )

    effective_thresholds = EvalThresholds(
        approved_by_human=thresholds_approved,
        approval_ref=approval_ref if thresholds_approved else None,
    )
    calibration_run_id: str | None = None
    calibration_manifest_sha256: str | None = None
    if calibration_report_path is not None:
        calib_path = Path(calibration_report_path)
        if not calib_path.is_file():
            blockers.append(f"Relatório de calibração não encontrado: {calib_path}")
        else:
            try:
                calib_data = json.loads(calib_path.read_text(encoding="utf-8"))
                calibration_run_id, calibration_manifest_sha256 = validate_calibration_report(
                    calib_data,
                    dataset_spec=spec,
                    dataset_sha256=dataset_sha,
                    judge_config=judge_config,
                    thresholds=effective_thresholds,
                )
            except Exception as exc:
                blockers.append(f"Relatório de calibração inválido ou incompatível: {exc}")
    else:
        blockers.append(
            "Calibração do juiz ainda não executada (--calibrate / --calibration-report ausente antes da baseline)"
        )

    manifest = build_evaluation_manifest(
        dataset_spec=spec,
        dataset_sha256=dataset_sha,
        judge_config=judge_config,
        thresholds=effective_thresholds,
        run_id=f"preflight-{uuid4().hex[:12]}",
        thresholds_approval_ref=approval_ref if thresholds_approved else None,
        calibration_run_id=calibration_run_id,
        calibration_manifest_sha256=calibration_manifest_sha256,
    )
    sec_ref = inspect_security_regression_evidence(spec)

    return {
        "mode": "preflight_dry_run",
        "is_evaluation_score": False,
        "evaluation_status": "Bloqueado" if blockers else "Não executado",
        "blockers": blockers,
        "manifest": manifest.model_dump(),
        "credentials_present": {
            "GEMINI_API_KEY": gemini_key_present,
            "OPENROUTER_API_KEY": openrouter_key_present,
        },
        "dataset_summary": {
            "dataset_id": spec.dataset_id,
            "dataset_version": spec.dataset_version,
            "dataset_sha256": dataset_sha,
            "documents_count": len(spec.documents),
            "cases_count": len(spec.cases),
            "applicable_metrics": list(spec.applicable_metrics),
            "not_applicable_metrics": {
                k: v.model_dump() for k, v in spec.not_applicable_metrics.items()
            },
        },
        "security_regression_reference": sec_ref,
    }


def export_schemas() -> dict[str, Any]:
    return {
        "DatasetSpec": DatasetSpec.model_json_schema(),
        "CalibrationManifest": CalibrationManifest.model_json_schema(),
        "EvaluationManifest": EvaluationManifest.model_json_schema(),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="executar-avaliacao-ressalvas",
        description=(
            "Runner RAGAS 0.4.x para o escopo de ingestão, reconciliação e consulta segura (T-015). "
            "Pré-flight verifica dataset, hashes e configuração sem chamar LLMs e sem inventar scores."
        ),
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help="Caminho para o dataset.json versionado.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Caminho para o arquivo .env local (valores nunca são impressos).",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        help="Imprime os JSON Schemas de DatasetSpec, CalibrationManifest e EvaluationManifest e encerra.",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Executa verificação dry-run de pré-flight sem chamar LLMs (não é score de avaliação).",
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="Executa a calibração real do juiz (controles positivos e negativos) em sandbox isolada.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Executa avaliação real RAGAS em sandbox isolada (exige --approval-ref e --calibration-report).",
    )
    parser.add_argument(
        "--approval-ref",
        type=str,
        default=None,
        help="Referência literal da aprovação humana de dataset, calibração e limiares.",
    )
    parser.add_argument(
        "--calibration-report",
        type=Path,
        default=None,
        help="Relatório JSON de calibração aprovada do juiz (obrigatório para --execute).",
    )
    parser.add_argument(
        "--baseline-report",
        type=Path,
        default=None,
        help="Relatório JSON de baseline compatível para calcular diff.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Arquivo de saída para gravar o relatório JSON sanitizado.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.schema:
        text = json.dumps(export_schemas(), indent=2, ensure_ascii=False)
        print(text)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        return 0

    if args.preflight or (not args.execute and not args.calibrate):
        report = run_preflight(
            dataset_path=args.dataset,
            env_file=args.env_file,
            thresholds_approved=bool(args.approval_ref and args.approval_ref.strip()),
            approval_ref=args.approval_ref,
            calibration_report_path=args.calibration_report,
        )
        text = json.dumps(report, indent=2, ensure_ascii=False)
        print(text)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        return 0

    spec, dataset_sha = load_dataset_spec(args.dataset)
    judge_config = EvalJudgeConfig.from_env(args.env_file)
    thresholds = EvalThresholds(
        approved_by_human=bool(args.approval_ref and args.approval_ref.strip()),
        approval_ref=args.approval_ref,
    )
    thresholds.validate_ready()
    judge_config.require_api_key()

    calibration_data: dict[str, Any] | None = None
    if args.calibration_report is not None:
        if not args.calibration_report.is_file():
            raise EvalBlockedError(f"Arquivo de calibração não encontrado: {args.calibration_report}")
        calibration_data = json.loads(args.calibration_report.read_text(encoding="utf-8"))

    baseline_data: dict[str, Any] | None = None
    if args.baseline_report is not None:
        if not args.baseline_report.is_file():
            raise EvalBlockedError(f"Arquivo de baseline não encontrado: {args.baseline_report}")
        baseline_data = json.loads(args.baseline_report.read_text(encoding="utf-8"))

    mode: Literal["calibrate", "evaluate"] = "calibrate" if args.calibrate else "evaluate"
    result_report = execute_isolated_evaluation_or_calibration(
        mode=mode,
        dataset_spec=spec,
        dataset_sha256=dataset_sha,
        dataset_dir=Path(args.dataset).resolve().parent,
        env_file=Path(args.env_file),
        judge_config=judge_config,
        thresholds=thresholds,
        calibration_report=calibration_data,
        baseline_report=baseline_data,
    )

    text = json.dumps(result_report, indent=2, ensure_ascii=False)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if result_report.get("status") == "Passou" else 1


if __name__ == "__main__":
    sys.exit(main())

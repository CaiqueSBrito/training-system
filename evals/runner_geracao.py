"""Avaliação da geração real do chat e calibração inicial do juiz de saída (T-022).

Extensão aditiva: não altera `runner_ressalvas.py`, o dataset `ressalvas` 1.0.1 nem a baseline
histórica `ragas-run-b647f4ac987c`, que cobrem só ingestão/consulta segura. Nada aqui compara
com essa baseline.

Fluxo:
1. `capture_artifact` monta o artefato `chat-generation-artifact/v1` a partir das saídas reais
   da T-021: o `ChatResponse` do stdout, o arquivo `--diagnostics` e a captura do contexto
   efetivamente enviado ao E8 com os itens originais (antes do escape). O artefato só nasce
   verificado: contra a linhagem Postgres enquanto o sandbox existir, ou contra os hashes do
   manifesto do sandbox. Sem verificação não há artefato.
2. `run_faithfulness_evaluation` aplica `Faithfulness` do RAGAS (ADR-0006): `user_input` é a
   frente ou o enunciado; `response` é o verso, ou a alternativa correta mais a explicação;
   `retrieved_contexts` são os trechos citados, com o texto enviado ao E8. Usa o conteúdo
   original, não o escapado. Não há limiar aprovado, então o relatório é só uma medição.
3. `run_output_judge_calibration` mede a concordância do juiz de saída real (E10, rubrica
   versionada atual) com os rótulos técnicos do dataset `geracao`. Os rótulos são do
   implementador, não humanos. O relatório nunca declara calibração humana nem prontidão
   para produção.

Regras: sem mock, score padrão ou clamp; sem retentativa (uma tentativa por chamada, para no
primeiro erro retryable); orçamento de chamadas verificado antes de qualquer chamada; sem
chave, mensagem de provedor ou traceback nos relatórios.
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
import logging
import math
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any, Iterator, Literal, Protocol, Sequence
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from training_contracts import ChatResponse, ContextChunk, NumberedContext, validar_item
from training_contracts.geracao import Item
from training_contracts.rendering import render_item
from training_generation import load_prompts
from training_generation.agents import GenerationError
from training_generation.validation import normalize_evidence

from evals.runner_ressalvas import (
    EvalBlockedError,
    EvalConfigError,
    EvalError,
    EvalJudgeConfig,
    EvalProviderError,
    _canonical_json_sha256,
    _validate_metric_score_value,
)
from training_llm import _guard_sdk_logging

__all__ = [
    "ARTIFACT_SCHEMA",
    "DEFAULT_DATASET_PATH",
    "EvalBlockedError",
    "EvalConfigError",
    "EvalJudgeConfig",
    "GenerationEvalArtifact",
    "VerifiedArtifact",
    "load_verified_artifact",
    "build_arg_parser",
    "capture_artifact",
    "check_rubric_binding",
    "compare_faithfulness_reports",
    "faithfulness_inputs",
    "load_generation_dataset",
    "main",
    "run_faithfulness_evaluation",
    "run_output_judge_calibration",
]

EVALS_ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET_PATH = EVALS_ROOT / "datasets" / "geracao" / "dataset.json"
ARTIFACT_SCHEMA = "training-evals/chat-generation-artifact/v1"
FAITHFULNESS_REPORT_SCHEMA = "training-evals/faithfulness-report/v1"
JUDGE_REPORT_SCHEMA = "training-evals/output-judge-calibration-report/v1"
COMPARISON_SCHEMA = "training-evals/faithfulness-comparison/v1"
EXPECTED_EMBEDDING_KEY = "gemini-embedding-2@3072@fmt-v1"
CALLS_PER_FAITHFULNESS_ITEM = 2  # geração de afirmações + NLI, sem retentativa
CRITERIA = ("R1", "R2", "R3", "R4")
_CRITERION = re.compile(r"^\s*(R[1-4])\b")
_MOTIVO_MAX = 300
REQUIRED_MANIFEST_FILES = {"response": "response.json", "diagnostics": "diagnostics.json",
                           "e8_capture": "e8-capture.json"}
_SHA256 = r"^[0-9a-f]{64}$"
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
# Namespaces de SDK cujos registros de log podem carregar resposta/mensagem do provedor.
_SDK_LOG_NAMESPACES = ("instructor", "litellm", "LiteLLM", "LiteLLM Router", "LiteLLM Proxy", "ragas", "httpx",
                       "httpcore", "openai", "google_adk", "google.adk", "google_genai", "google.genai",
                       "google.auth")
_REDACTED_LOG = "registro de SDK suprimido pela avaliação"


class EvalBudgetExceeded(EvalBlockedError):
    """Chamada recusada na fronteira do transporte: o orçamento real de chamadas acabou."""


class EvalRetryPolicyError(EvalBlockedError):
    """Chamada do produto sem retentativa zero explícita; recusada antes de chegar ao transporte."""


def _sha256_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def _is_gemini(model: str) -> bool:
    return "gemini" in model.lower()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ====================================================================== dataset


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LabelSource(_Strict):
    kind: Literal["rotulagem_tecnica_implementador"]
    provider: str
    human_labels: bool
    note: str


class RubricBinding(_Strict):
    output_judge: str
    output_rubric: str


class DatasetDocument(_Strict):
    doc_id: str
    title: str
    relative_path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class JudgeContext(_Strict):
    doc_id: str
    section: str
    text: str = Field(min_length=1)


class ExpectedVerdict(_Strict):
    aprovado: bool
    fundamentado: bool | None
    conforme_rubrica: bool | None
    criterios_esperados: list[str]

    @model_validator(mode="after")
    def _coerente(self) -> ExpectedVerdict:
        if any(c not in CRITERIA for c in self.criterios_esperados):
            raise ValueError("critério desconhecido; use R1–R4")
        if self.aprovado:
            if self.fundamentado is not True or self.conforme_rubrica is not True:
                raise ValueError("item aprovado exige fundamentado e conforme_rubrica verdadeiros")
            if self.criterios_esperados:
                raise ValueError("item aprovado não tem critério violado")
        else:
            if not self.criterios_esperados:
                raise ValueError("item reprovado exige critério esperado")
            if self.fundamentado is not False and self.conforme_rubrica is not False:
                raise ValueError("item reprovado exige ao menos um campo rotulado como falso")
        return self


class JudgeCase(_Strict):
    case_id: str
    context: list[JudgeContext] = Field(min_length=1, max_length=1)
    item: dict[str, Any]
    expected: ExpectedVerdict
    label_rationale: str = Field(min_length=1)


class ChatCase(_Strict):
    case_id: str
    doc_id: str
    message: str = Field(min_length=1)
    count: int = Field(ge=1, le=20)
    expected_tipo: Literal["flashcard", "question"]


class GenerationDatasetSpec(_Strict):
    dataset_id: str
    dataset_version: str
    scope: str
    status: str
    description: str
    label_source: LabelSource
    rubric_binding: RubricBinding
    documents: list[DatasetDocument] = Field(min_length=1)
    judge_cases: list[JudgeCase] = Field(min_length=1)
    chat_cases: list[ChatCase] = Field(default_factory=list)


def load_generation_dataset(path: str | Path = DEFAULT_DATASET_PATH) -> tuple[GenerationDatasetSpec, str]:
    """Carrega e valida o dataset, os hashes dos documentos e a literalidade de contextos e evidências."""
    path = Path(path)
    raw = path.read_bytes()
    try:
        spec = GenerationDatasetSpec.model_validate_json(raw)
    except ValidationError as exc:
        raise EvalConfigError(f"dataset de geração inválido: {exc.error_count()} erro(s) de schema") from None
    if spec.label_source.human_labels:
        raise EvalConfigError("dataset declara rótulo humano; este corpus só tem rotulagem técnica sem humano")
    textos: dict[str, str] = {}
    for posicao, doc in enumerate(spec.documents):
        arquivo = (path.parent / doc.relative_path).resolve()
        if path.parent.resolve() not in arquivo.parents:
            raise EvalConfigError(f"documento {posicao} fora do dataset")
        dados = arquivo.read_bytes()
        if _sha256_bytes(dados) != doc.sha256:
            raise EvalConfigError(f"sha256 divergente do documento {posicao}")
        textos[doc.doc_id] = dados.decode("utf-8")
    ids = [c.case_id for c in spec.judge_cases] + [c.case_id for c in spec.chat_cases]
    if len(ids) != len(set(ids)):
        raise EvalConfigError("case_id repetido no dataset")
    # Mensagens só com posição: valores do arquivo (case_id, doc_id) não são confiáveis.
    for posicao, caso in enumerate(spec.judge_cases):
        contexto = caso.context[0]
        if contexto.doc_id not in textos:
            raise EvalConfigError(f"judge_cases[{posicao}]: documento desconhecido")
        if contexto.text not in textos[contexto.doc_id]:
            raise EvalConfigError(f"judge_cases[{posicao}]: contexto não existe literalmente no documento")
        try:
            item = validar_item(caso.item)
        except ValidationError:
            raise EvalConfigError(f"judge_cases[{posicao}]: item não valida contra o contrato Item") from None
        for citacao in item.citations:
            if citacao.chunk_ref != "C1":
                raise EvalConfigError(f"judge_cases[{posicao}]: citação deve apontar para C1")
            if citacao.evidence not in contexto.text:
                raise EvalConfigError(f"judge_cases[{posicao}]: evidência citada ausente do contexto")
    for posicao, caso in enumerate(spec.chat_cases):
        if caso.doc_id not in textos:
            raise EvalConfigError(f"chat_cases[{posicao}]: documento desconhecido")
    return spec, _sha256_bytes(raw)


def check_rubric_binding(spec: GenerationDatasetSpec, prompt_versions: dict[str, str]) -> None:
    """Rótulos valem para a rubrica a que foram vinculados; outra versão bloqueia a avaliação."""
    for nome in ("output_judge", "output_rubric"):
        esperado = getattr(spec.rubric_binding, nome)
        if prompt_versions.get(nome) != esperado:
            raise EvalBlockedError(
                f"Bloqueado: rubrica/prompt {nome} atual difere do vínculo do dataset; rótulos não se aplicam"
            )


# ====================================================================== artefato


class RerankerInfo(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    model: str
    revision: str | None = None
    cutoff: float | None = None
    manifest_sha256: str


class ChatDiagnostics(BaseModel):
    """`pipeline.last_execution` da T-021 (`--diagnostics`). Chaves extras são preservadas."""

    model_config = ConfigDict(extra="allow", frozen=True)
    chat_turn_id: str
    trace_id: str
    durations_seconds: dict[str, float]
    models: dict[str, str]
    prompt_versions: dict[str, str]
    status: str
    errors: list[Any]
    model_calls_admitted: int = Field(ge=0)
    embedding_key: str
    reranker: RerankerInfo

    @model_validator(mode="after")
    def _latencias(self) -> ChatDiagnostics:
        for etapa, valor in self.durations_seconds.items():
            if not math.isfinite(valor) or valor < 0:
                raise ValueError("latência inválida")
        return self


class E8Context(_Strict):
    """Uma chamada E8 (inicial ou reparo) com o `NumberedContext` exato passado a `generate()`.

    `context_ref_mapping` leva a ref local do contexto da chamada à ref global do turno, usada
    pelo `ItemRecord.content`, pelos itens entregues e por `fontes`.
    """

    call_id: str = Field(min_length=1)
    call: Literal["inicial", "reparo"]
    context: NumberedContext
    context_ref_mapping: dict[str, str]
    status: Literal["completed", "failed"]

    @model_validator(mode="after")
    def _mapa(self) -> E8Context:
        refs = [c.ref for c in self.context.chunks]
        if sorted(self.context_ref_mapping) != sorted(refs):
            raise ValueError("context_ref_mapping deve cobrir exatamente as refs do contexto")
        if len(set(self.context_ref_mapping.values())) != len(refs):
            raise ValueError("context_ref_mapping não é injetivo")
        return self

    def chunk_for_global_ref(self, ref: str) -> ContextChunk | None:
        local = next((k for k, v in self.context_ref_mapping.items() if v == ref), None)
        return None if local is None else self.context.chunk(local)


class OriginalItem(_Strict):
    item_id: str
    content: Item
    call_id: str = Field(min_length=1)
    generation_item_index: int = Field(ge=0)
    attempt: Literal[1, 2]


class E8Capture(_Strict):
    chat_turn_id: str
    contexts: list[E8Context]
    original_items: list[OriginalItem]

    @model_validator(mode="after")
    def _vinculos(self) -> E8Capture:
        chamadas = [c.call_id for c in self.contexts]
        if len(set(chamadas)) != len(chamadas):
            raise ValueError("call_id repetido na captura E8")
        por_id = {c.call_id: c for c in self.contexts}
        vistos = set()
        for posicao, item in enumerate(self.original_items):
            chamada = por_id.get(item.call_id)
            if chamada is None or chamada.status != "completed":
                raise ValueError(f"item {posicao}: sem chamada E8 concluída correspondente")
            if (chamada.call == "reparo") != (item.attempt == 2):
                raise ValueError(f"item {posicao}: attempt incoerente com a chamada E8")
            chave = (item.call_id, item.generation_item_index)
            if chave in vistos:
                raise ValueError(f"item {posicao}: índice de geração repetido na mesma chamada")
            vistos.add(chave)
        return self

    def call(self, call_id: str) -> E8Context:
        return next(c for c in self.contexts if c.call_id == call_id)


class DocumentBinding(_Strict):
    document_version_id: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ArtifactVerification(_Strict):
    method: Literal["lineage_postgres", "sandbox_manifest_hashes"]
    detail: dict[str, Any]


class GenerationEvalArtifact(_Strict):
    artifact_schema: Literal["training-evals/chat-generation-artifact/v1"]
    source_task: Literal["T-021"]
    source_dispatch: str = Field(min_length=1)
    case_id: str | None = None
    owner_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    document_version_ids: list[str] = Field(min_length=1)
    response: ChatResponse
    response_sha256: str
    diagnostics: ChatDiagnostics
    diagnostics_sha256: str
    e8: E8Capture
    e8_capture_sha256: str
    documents: list[DocumentBinding] = Field(min_length=1)
    verification: ArtifactVerification
    case_protocol: dict[str, Any] | None = None
    assembled_at: str

    @model_validator(mode="after")
    def _coerente(self) -> GenerationEvalArtifact:
        _check_internal_consistency(self.response, self.diagnostics, self.e8, self.document_version_ids)
        if {d.document_version_id for d in self.documents} != set(self.document_version_ids):
            raise ValueError("documents deve cobrir exatamente document_version_ids")
        return self


def _contains_evidence(text: str, evidence: str) -> bool:
    alvo = normalize_evidence(evidence)
    return bool(alvo) and f" {alvo} " in f" {normalize_evidence(text)} "


def _cited_chunks(response: ChatResponse, e8: E8Capture, original: OriginalItem) -> list[ContextChunk]:
    """Chunks citados pelo item, só do contexto da própria chamada E8 (nunca a união das chamadas)."""
    fontes = {f.chunk_ref: f for f in response.fontes}
    chamada = e8.call(original.call_id)
    resultado: dict[str, ContextChunk] = {}
    for citacao in original.content.citations:
        fonte = fontes.get(citacao.chunk_ref)
        if fonte is None:
            raise EvalConfigError(f"citação {citacao.chunk_ref} sem fonte na resposta")
        chunk = chamada.chunk_for_global_ref(citacao.chunk_ref)
        if chunk is None or chunk.chunk_id != fonte.chunk_id:
            raise EvalConfigError(f"chunk citado {citacao.chunk_ref} ausente ou divergente no contexto E8 da chamada")
        if not _contains_evidence(chunk.text, citacao.evidence):
            raise EvalConfigError(f"evidência de {citacao.chunk_ref} ausente do contexto E8")
        resultado.setdefault(fonte.chunk_id, chunk)
    return list(resultado.values())


def _check_internal_consistency(response: ChatResponse, diagnostics: ChatDiagnostics, e8: E8Capture,
                                versions: Sequence[str]) -> None:
    if response.chat_turn_id != diagnostics.chat_turn_id or e8.chat_turn_id != diagnostics.chat_turn_id:
        raise EvalConfigError("chat_turn_id diverge entre resposta, diagnóstico e captura E8")
    if response.trace_id != diagnostics.trace_id:
        raise EvalConfigError("trace_id diverge entre resposta e diagnóstico")
    if response.status.value != diagnostics.status:
        raise EvalConfigError("status diverge entre resposta e diagnóstico")
    entregues = [i.item_id for i in response.itens]
    originais = [o.item_id for o in e8.original_items]
    if entregues != originais:
        raise EvalConfigError("itens originais da captura não correspondem aos itens entregues")
    if response.itens and not e8.contexts:
        raise EvalConfigError("resposta com itens sem contexto E8 capturado")
    for posicao, (entregue, original) in enumerate(zip(response.itens, e8.original_items)):
        if render_item(original.content).model_dump(mode="json") != entregue.conteudo.model_dump(mode="json"):
            raise EvalConfigError(f"item {posicao}: entrega não é render_item do conteúdo original")
        _cited_chunks(response, e8, original)
    for fonte in response.fontes:
        if fonte.document_version_id not in versions:
            raise EvalConfigError("fonte fora das versões de documento declaradas")


class LineageReader(Protocol):
    def item_lineage(self, owner_id: str, item_id: str) -> Any: ...
    def chunk(self, chunk_id: str) -> Any: ...
    def document_version(self, document_version_id: str) -> Any: ...


class PostgresLineageReader:
    """Leitor real da linhagem do sandbox (somente leitura, conexão do chamador)."""

    def __init__(self, connection: Any) -> None:
        from training_lineage import LineageRepository
        from training_lineage.chat import ChatLineageRepository

        self._chat = ChatLineageRepository(connection)
        self._lineage = LineageRepository(connection)

    def item_lineage(self, owner_id: str, item_id: str) -> Any:
        resultado = self._chat.get_item_lineage(owner_id, item_id)
        return None if resultado is None else resultado["lineage"]

    def chunk(self, chunk_id: str) -> Any:
        return self._lineage.get_chunk(chunk_id)

    def document_version(self, document_version_id: str) -> Any:
        return self._lineage.get_document_version(document_version_id)


def _verify_with_lineage(reader: LineageReader, *, owner_id: str, response: ChatResponse, e8: E8Capture,
                         versions: Sequence[str]) -> tuple[list[DocumentBinding], ArtifactVerification]:
    documentos = []
    for versao in versions:
        registro = reader.document_version(versao)
        if registro is None or registro.owner_id != owner_id:
            raise EvalConfigError("versão de documento ausente da linhagem ou de outro dono")
        documentos.append(DocumentBinding(document_version_id=versao, sha256=registro.sha256))
    fontes = {f.chunk_ref: f.chunk_id for f in response.fontes}
    for posicao, original in enumerate(e8.original_items):
        linhagem = reader.item_lineage(owner_id, original.item_id)
        if linhagem is None:
            raise EvalConfigError(f"item {posicao}: item entregue ausente da linhagem do dono")
        if linhagem.chat_turn_id != response.chat_turn_id or linhagem.trace_id != response.trace_id:
            raise EvalConfigError(f"item {posicao}: turno/trace da linhagem diverge do artefato")
        if linhagem.content.model_dump(mode="json") != original.content.model_dump(mode="json"):
            raise EvalConfigError(f"item {posicao}: conteúdo original diverge da linhagem persistida")
        esperados = {fontes[c.chunk_ref] for c in original.content.citations}
        if {c.chunk_id for c in linhagem.citations} != esperados:
            raise EvalConfigError(f"item {posicao}: chunks citados divergem da linhagem")
    verificados = 0
    for contexto in e8.contexts:
        for chunk in contexto.context.chunks:
            registro = reader.chunk(chunk.chunk_id)
            if registro is None or registro.document_version_id not in versions:
                raise EvalConfigError("chunk do contexto E8 ausente da linhagem ou fora do escopo")
            partes = [p for p in chunk.text.split("\n\n") if p.strip()]
            if not partes or any(p not in registro.text for p in partes):
                raise EvalConfigError("contexto E8 não é recorte do chunk persistido")
            verificados += 1
    return documentos, ArtifactVerification(method="lineage_postgres", detail={
        "items_checked": len(e8.original_items), "chunks_checked": verificados,
        "document_versions_checked": len(documentos), "checked_at": _now()})


class ManifestRequest(_Strict):
    """Pedido efetivamente executado pela T-021, sem texto: hash da mensagem, quantidade e escopo."""

    message_sha256: str = Field(pattern=_SHA256)
    count: int = Field(ge=1, le=20)
    document_version_ids: list[str] = Field(min_length=1)
    section_prefix: list[str] = Field(default_factory=list)


class SandboxManifest(BaseModel):
    """Manifesto do sandbox da T-021. Identidade completa é obrigatória; chaves extras são preservadas."""

    model_config = ConfigDict(extra="allow", frozen=True)
    run_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    chat_turn_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    document_version_ids: list[str] = Field(min_length=1)
    files: dict[str, str]
    source_sha256: str | None = Field(default=None, pattern=_SHA256)
    documents: list[DocumentBinding] | None = None
    response_status: str | None = None
    request: ManifestRequest | None = None

    @model_validator(mode="after")
    def _coerente(self) -> SandboxManifest:
        normalizados = []
        for chave, valor in self.files.items():
            caminho = PurePosixPath(chave)
            if (not chave or "\\" in chave or caminho.is_absolute() or ".." in caminho.parts
                    or str(caminho) != chave or not re.fullmatch(_SHA256, valor)):
                raise ValueError("entrada de arquivo do manifesto inválida")
            normalizados.append(str(caminho).casefold())
        if len(set(normalizados)) != len(normalizados):
            raise ValueError("arquivos do manifesto duplicados ou ambíguos")
        if any(nome not in self.files for nome in REQUIRED_MANIFEST_FILES.values()):
            raise ValueError("manifesto sem os arquivos obrigatórios da captura")
        if (self.documents is None) == (self.source_sha256 is None):
            raise ValueError("manifesto exige documents ou source_sha256, não ambos")
        if self.source_sha256 is not None and len(self.document_version_ids) != 1:
            raise ValueError("source_sha256 só vale para uma versão de documento")
        if self.request is not None and self.request.document_version_ids != self.document_version_ids:
            raise ValueError("pedido do manifesto com versões diferentes da execução")
        return self

    def document_bindings(self) -> list[DocumentBinding]:
        if self.documents is not None:
            return list(self.documents)
        return [DocumentBinding(document_version_id=self.document_version_ids[0], sha256=self.source_sha256 or "")]


def _parse_manifest(bruto: bytes) -> SandboxManifest:
    try:
        return SandboxManifest.model_validate_json(bruto)
    except ValidationError as exc:
        raise EvalConfigError(f"manifesto do sandbox fora do contrato: {exc.error_count()} erro(s)") from None


def _verify_with_manifest(manifest_path: Path, paths: dict[str, Path], versions: Sequence[str],
                          identidade: dict[str, Any], response_status: str) -> tuple[list[DocumentBinding], ArtifactVerification, SandboxManifest]:
    bruto = manifest_path.read_bytes()
    manifest = _parse_manifest(bruto)
    base = manifest_path.resolve().parent
    for papel, caminho in paths.items():
        nome = REQUIRED_MANIFEST_FILES[papel]
        if caminho.resolve() != (base / nome).resolve():
            raise EvalConfigError(f"manifesto não declara o arquivo usado como {papel}")
        if manifest.files[nome] != _sha256_bytes(caminho.read_bytes()):
            raise EvalConfigError(f"manifesto não confirma o sha256 de {papel}")
    for chave, valor in identidade.items():
        if getattr(manifest, chave) != valor:
            raise EvalConfigError(f"manifesto diverge do artefato em {chave}")
    if manifest.response_status is not None and manifest.response_status != response_status:
        raise EvalConfigError("manifesto diverge do artefato em response_status")
    documentos = manifest.document_bindings()
    if {d.document_version_id for d in documentos} != set(versions):
        raise EvalConfigError("manifesto não cobre exatamente as versões de documento declaradas")
    return documentos, ArtifactVerification(method="sandbox_manifest_hashes", detail={
        "manifest_sha256": _sha256_bytes(bruto), "manifest_name": manifest_path.name,
        "manifest_run_id": manifest.run_id,
        "files_confirmed": sorted(REQUIRED_MANIFEST_FILES.values()), "checked_at": _now()}), manifest


def _case_protocol(manifest: SandboxManifest | None, case_id: str | None, dataset: GenerationDatasetSpec | None,
                   dataset_sha256: str | None, documentos: Sequence[DocumentBinding]) -> dict[str, Any] | None:
    """Protocolo comparável do caso: dataset/caso/pedido executado. Sem pedido verificável, não há protocolo."""
    if dataset is None:
        if manifest is None or manifest.request is None:
            return None
        return {"kind": "external", "case_id": case_id,
                "request_sha256": _canonical_json_sha256(manifest.request.model_dump(mode="json"))}
    if dataset_sha256 is None or not re.fullmatch(_SHA256, dataset_sha256):
        raise EvalConfigError("vínculo ao dataset exige o sha256 do dataset carregado")
    if case_id is None:
        raise EvalConfigError("vínculo ao dataset exige case_id")
    caso = next((c for c in dataset.chat_cases if c.case_id == case_id), None)
    if caso is None:
        raise EvalConfigError("case_id do artefato ausente do dataset")
    doc = next(d for d in dataset.documents if d.doc_id == caso.doc_id)
    if doc.sha256 not in {d.sha256 for d in documentos}:
        raise EvalConfigError("sha256 do documento ingerido difere do dataset")
    if manifest is None or manifest.request is None:
        raise EvalConfigError("vínculo ao dataset exige o pedido executado no manifesto")
    pedido = manifest.request
    if pedido.message_sha256 != _sha256_bytes(caso.message.encode("utf-8")) or pedido.count != caso.count:
        raise EvalConfigError("pedido executado difere do caso do dataset")
    return {"kind": "dataset", "dataset_id": dataset.dataset_id, "dataset_version": dataset.dataset_version,
            "dataset_sha256": dataset_sha256, "case_id": case_id,
            "case_spec_sha256": _canonical_json_sha256(caso.model_dump(mode="json")),
            "request_sha256": _canonical_json_sha256(pedido.model_dump(mode="json"))}


def capture_artifact(response_path: str | Path, diagnostics_path: str | Path, e8_capture_path: str | Path, *,
                     owner_id: str, session_id: str, document_version_ids: Sequence[str], source_dispatch: str,
                     lineage_reader: LineageReader | None = None, sandbox_manifest: str | Path | None = None,
                     case_id: str | None = None, dataset: GenerationDatasetSpec | None = None,
                     dataset_sha256: str | None = None) -> GenerationEvalArtifact:
    """Monta o artefato verificado a partir das saídas reais da T-021; não faz chamada de modelo."""
    if (lineage_reader is None) == (sandbox_manifest is None):
        raise EvalConfigError("informe exatamente uma verificação: linhagem Postgres ou manifesto do sandbox")
    paths = {"response": Path(response_path), "diagnostics": Path(diagnostics_path), "e8_capture": Path(e8_capture_path)}
    try:
        brutos = {k: p.read_bytes() for k, p in paths.items()}
    except OSError:
        raise EvalConfigError("arquivo da captura T-021 ilegível ou ausente") from None
    try:
        response = ChatResponse.model_validate_json(brutos["response"])
        diagnostics = ChatDiagnostics.model_validate_json(brutos["diagnostics"])
        e8 = E8Capture.model_validate_json(brutos["e8_capture"])
    except ValidationError as exc:
        raise EvalConfigError(f"saída da T-021 fora do contrato: {exc.error_count()} erro(s)") from None
    versions = list(document_version_ids)
    _check_internal_consistency(response, diagnostics, e8, versions)
    manifest = None
    if lineage_reader is not None:
        documentos, verificacao = _verify_with_lineage(lineage_reader, owner_id=owner_id, response=response,
                                                       e8=e8, versions=versions)
    else:
        identidade = {"owner_id": owner_id, "session_id": session_id, "chat_turn_id": response.chat_turn_id,
                      "trace_id": response.trace_id, "document_version_ids": versions}
        documentos, verificacao, manifest = _verify_with_manifest(Path(sandbox_manifest), paths, versions,
                                                                  identidade, response.status.value)
        if case_id is None:
            case_id = manifest.case_id
        elif case_id != manifest.case_id:
            raise EvalConfigError("manifesto diverge do artefato em case_id")
    modo = paths["e8_capture"].stat().st_mode & 0o777
    verificacao = verificacao.model_copy(update={"detail": dict(verificacao.detail, e8_capture_mode=oct(modo))})
    protocolo = _case_protocol(manifest, case_id, dataset, dataset_sha256, documentos)
    return GenerationEvalArtifact(
        artifact_schema=ARTIFACT_SCHEMA, source_task="T-021", source_dispatch=source_dispatch, case_id=case_id,
        owner_id=owner_id, session_id=session_id, document_version_ids=versions,
        response=response, response_sha256=_sha256_bytes(brutos["response"]),
        diagnostics=diagnostics, diagnostics_sha256=_sha256_bytes(brutos["diagnostics"]),
        e8=e8, e8_capture_sha256=_sha256_bytes(brutos["e8_capture"]),
        documents=documentos, verification=verificacao, case_protocol=protocolo, assembled_at=_now(),
    )


_VERIFIED = object()
_COMPARED_FIELDS = ("case_id", "owner_id", "session_id", "document_version_ids", "response", "response_sha256",
                    "diagnostics", "diagnostics_sha256", "e8", "e8_capture_sha256", "documents")


@dataclass(frozen=True)
class VerifiedArtifact:
    """Artefato reverificado agora contra as fontes e o manifesto fixado; só `load_verified_artifact` cria."""

    artifact: GenerationEvalArtifact
    manifest_sha256: str
    stored_artifact_sha256: str | None
    _token: object = field(repr=False, compare=False, default=None)

    def __post_init__(self) -> None:
        if self._token is not _VERIFIED:
            raise EvalConfigError("VerifiedArtifact só é criado por load_verified_artifact")


def load_verified_artifact(manifest_path: str | Path, *, manifest_sha256: str, source_dispatch: str,
                           artifact_path: str | Path | None = None, dataset: GenerationDatasetSpec | None = None,
                           dataset_sha256: str | None = None) -> VerifiedArtifact:
    """Revalida tudo antes de qualquer rede: sha256 fixado do manifesto, bytes das fontes e identidade.

    O sha256 do manifesto vem de fora do artefato (registro revisado na governança). O artefato
    é remontado das fontes; um artefato armazenado, se informado, precisa ser idêntico ao remontado.
    """
    caminho = Path(manifest_path)
    if not re.fullmatch(_SHA256, manifest_sha256 or ""):
        raise EvalConfigError("sha256 fixado do manifesto ausente ou inválido")
    try:
        bruto = caminho.read_bytes()
    except OSError:
        raise EvalConfigError("manifesto do sandbox ilegível ou ausente") from None
    if _sha256_bytes(bruto) != manifest_sha256:
        raise EvalBlockedError("Bloqueado: manifesto difere do sha256 fixado; artefato não verificado")
    manifest = _parse_manifest(bruto)
    base = caminho.resolve().parent
    fontes = {papel: base / nome for papel, nome in REQUIRED_MANIFEST_FILES.items()}
    remontado = capture_artifact(fontes["response"], fontes["diagnostics"], fontes["e8_capture"],
                                 owner_id=manifest.owner_id, session_id=manifest.session_id,
                                 document_version_ids=manifest.document_version_ids, source_dispatch=source_dispatch,
                                 sandbox_manifest=caminho, case_id=manifest.case_id, dataset=dataset,
                                 dataset_sha256=dataset_sha256)
    armazenado_sha = None
    if artifact_path is not None:
        try:
            dados = Path(artifact_path).read_bytes()
            armazenado = GenerationEvalArtifact.model_validate_json(dados)
        except OSError:
            raise EvalConfigError("artefato armazenado ilegível ou ausente") from None
        except ValidationError as exc:
            raise EvalConfigError(f"artefato armazenado fora do contrato: {exc.error_count()} erro(s)") from None
        for campo in _COMPARED_FIELDS:
            if getattr(armazenado, campo) != getattr(remontado, campo):
                raise EvalConfigError(f"artefato armazenado diverge das fontes verificadas ({campo})")
        armazenado_sha = _sha256_bytes(dados)
    return VerifiedArtifact(artifact=remontado, manifest_sha256=manifest_sha256,
                            stored_artifact_sha256=armazenado_sha, _token=_VERIFIED)


# ====================================================================== Faithfulness


@dataclass(frozen=True)
class FaithfulnessInput:
    item_id: str
    chat_turn_id: str
    call_id: str
    attempt: int
    tipo: str
    user_input: str
    response: str
    retrieved_contexts: list[str]

    @property
    def contexts_sha256(self) -> str:
        return _canonical_json_sha256({"contexts": self.retrieved_contexts})


def faithfulness_inputs(artifact: GenerationEvalArtifact) -> list[FaithfulnessInput]:
    """Mapeamento da ADR-0006 sobre o conteúdo original e os trechos citados enviados ao E8."""
    entradas = []
    for original in artifact.e8.original_items:
        item = original.content
        if item.tipo == "flashcard":
            pergunta, resposta = item.front, item.back
        else:
            pergunta, resposta = item.stem, f"{item.options[item.correct_index]}\n{item.explanation}"
        contextos = [c.text for c in _cited_chunks(artifact.response, artifact.e8, original)]
        entradas.append(FaithfulnessInput(item_id=original.item_id, chat_turn_id=artifact.response.chat_turn_id or "",
                                          call_id=original.call_id, attempt=original.attempt, tipo=item.tipo, user_input=pergunta, response=resposta,
                                          retrieved_contexts=contextos))
    return entradas


def compute_faithfulness_prompt_sha256() -> str:
    from ragas.metrics.collections.faithfulness.metric import Faithfulness
    from ragas.metrics.collections.faithfulness.util import NLIStatementPrompt, StatementGeneratorPrompt

    partes = {"metric_class": f"{Faithfulness.__module__}.{Faithfulness.__qualname__}",
              "ragas_version": pkg_version("ragas")}
    for nome, classe in (("statement_generator", StatementGeneratorPrompt), ("nli_statement", NLIStatementPrompt)):
        prompt = classe()
        partes[nome] = {"prompt_class": f"{classe.__module__}.{classe.__qualname__}",
                        "instruction": prompt.instruction, "examples_count": len(prompt.examples),
                        "output_schema": classe.output_model.model_json_schema()}
    return _canonical_json_sha256(partes)


def _is_sdk_logger(name: str) -> bool:
    return any(name == ns or name.startswith(ns + ".") for ns in _SDK_LOG_NAMESPACES)


@contextmanager
def redacted_sdk_logging() -> Iterator[None]:
    """Redige na criação todo registro de log dos SDKs (Instructor, LiteLLM, RAGAS, HTTP, ADK).

    A redação acontece na fábrica de `LogRecord`, antes de qualquer handler, filtro ou
    `lastResort`: cobre loggers filhos criados depois, níveis explícitos e handlers próprios.
    Mensagem, argumentos, exceção e stack saem do registro; nome e nível ficam.
    """
    _guard_sdk_logging()
    anterior = logging.getLogRecordFactory()

    def fabrica(name, level, fn, lno, msg, args, exc_info, func=None, sinfo=None, **kwargs):
        if _is_sdk_logger(name):
            msg, args, exc_info, sinfo = _REDACTED_LOG, (), None, None
        return anterior(name, level, fn, lno, msg, args, exc_info, func, sinfo, **kwargs)

    logging.setLogRecordFactory(fabrica)
    try:
        yield
    finally:
        logging.setLogRecordFactory(anterior)


class CallBudget:
    """Admissão real na fronteira do transporte: cada chamada ao provedor consome uma vaga."""

    def __init__(self, limit: int) -> None:
        if type(limit) is not int or limit < 0:
            raise EvalConfigError("orçamento de chamadas inválido")
        self.limit = limit
        self.calls = 0

    def admit(self) -> None:
        if self.calls >= self.limit:
            raise EvalBudgetExceeded("Bloqueado: orçamento real de chamadas esgotado na fronteira do transporte")
        self.calls += 1


def _real_litellm_transport() -> Any:
    _guard_sdk_logging()
    import litellm

    litellm.suppress_debug_info = True
    litellm.set_verbose = False
    return litellm.acompletion


def _admitted_transport(transport: Any, budget: CallBudget, api_key: str) -> Any:
    async def chamar(*args: Any, **kwargs: Any) -> Any:
        budget.admit()
        argumentos = dict(kwargs)
        # Uma tentativa no transporte: sem retentativa do LiteLLM nem do cliente HTTP.
        argumentos.update({"api_key": api_key, "no-log": True, "num_retries": 0, "max_retries": 0, "timeout": 60})
        return await transport(*args, **argumentos)

    return chamar


def build_faithfulness_metric(judge_config: EvalJudgeConfig, *, budget: CallBudget, transport: Any | None = None) -> Any:
    """Faithfulness real: RAGAS → Instructor (`max_retries=0`) → transporte admitido pelo orçamento."""
    import instructor
    from ragas.llms import llm_factory
    from ragas.llms.litellm_llm import LiteLLMStructuredLLM
    from ragas.metrics.collections import Faithfulness

    api_key = judge_config.require_api_key()
    cliente = instructor.from_litellm(_admitted_transport(transport or _real_litellm_transport(), budget, api_key))
    # `max_retries=0` chega a `client.chat.completions.create` pelos model_args do RAGAS: o Instructor
    # faz uma única tentativa e não reenvia o pedido para reparar a estrutura (padrão seria 3).
    llm = llm_factory(model=judge_config.judge_model, provider=judge_config.provider, client=cliente,
                      adapter="litellm", temperature=judge_config.temperature, top_p=judge_config.top_p,
                      seed=judge_config.seed, max_retries=0)
    if not isinstance(llm, LiteLLMStructuredLLM):
        raise EvalConfigError("llm_factory não retornou LiteLLMStructuredLLM")
    return Faithfulness(llm=llm)


def _exception_chain(exc: BaseException) -> list[BaseException]:
    cadeia, atual = [], exc
    while atual is not None and atual not in cadeia and len(cadeia) < 16:
        cadeia.append(atual)
        atual = atual.__cause__ or atual.__context__
    return cadeia


def classify_eval_exception(exc: BaseException) -> dict[str, Any]:
    """Categoria sanitizada percorrendo a cadeia (InstructorRetryException embrulha o erro HTTP)."""
    cadeia = _exception_chain(exc)
    for erro in cadeia:
        if isinstance(erro, EvalBudgetExceeded):
            return {"category": "budget", "status_code": None, "retryable": False}
        if isinstance(erro, EvalRetryPolicyError):
            return {"category": "retry_policy", "status_code": None, "retryable": False}
    for erro in cadeia:
        status = getattr(erro, "status_code", None)
        if type(status) is int and 100 <= status <= 599:
            return {"category": "provider", "status_code": status, "retryable": status in _RETRYABLE_STATUS}
        if isinstance(erro, TimeoutError):
            return {"category": "timeout", "status_code": None, "retryable": True}
    if any(isinstance(e, (ValidationError, json.JSONDecodeError)) or type(e).__name__ == "InstructorRetryException"
           for e in cadeia):
        return {"category": "invalid_output", "status_code": None, "retryable": False}
    return {"category": "provider", "status_code": None, "retryable": None}


def _current_prompt_versions() -> dict[str, str]:
    return {nome: a.reference for nome, a in load_prompts().items()}


def _check_artifact_policy(artifact: GenerationEvalArtifact, current: dict[str, str]) -> None:
    diag = artifact.diagnostics
    atuais = set(current.values())
    divergentes = sorted(v for v in diag.prompt_versions.values() if v not in atuais)
    if divergentes:
        raise EvalBlockedError("Bloqueado: artefato usa prompt de outra versão; avaliar exige a versão atual")
    if artifact.e8.original_items:
        if current["generation"] not in diag.prompt_versions.values():
            raise EvalBlockedError("Bloqueado: artefato com itens não registra o prompt de geração atual")
        juiz = diag.models.get("E10")
        if not juiz:
            raise EvalBlockedError("Bloqueado: artefato com itens sem modelo do juiz de saída E10")
        if _is_gemini(juiz):
            raise EvalBlockedError("Bloqueado: juiz de saída Gemini no artefato (RAG-JUI-002)")
    if diag.embedding_key != EXPECTED_EMBEDDING_KEY:
        raise EvalBlockedError("Bloqueado: chave de embedding do artefato difere da ADR-0002")


def _artifact_summary(artifact: GenerationEvalArtifact) -> dict[str, Any]:
    d = artifact.diagnostics
    return {
        "artifact_sha256": _canonical_json_sha256(artifact.model_dump(mode="json")),
        "case_id": artifact.case_id, "chat_turn_id": artifact.response.chat_turn_id,
        "trace_id": artifact.response.trace_id, "response_status": artifact.response.status.value,
        "source_dispatch": artifact.source_dispatch, "verification_method": artifact.verification.method,
        "response_sha256": artifact.response_sha256, "diagnostics_sha256": artifact.diagnostics_sha256,
        "e8_capture_sha256": artifact.e8_capture_sha256,
        "documents": [doc.model_dump() for doc in artifact.documents],
        "durations_seconds": dict(d.durations_seconds), "models": dict(d.models),
        "reranker": d.reranker.model_dump(), "embedding_key": d.embedding_key,
        "model_calls_admitted": d.model_calls_admitted, "case_protocol": artifact.case_protocol,
        "e8_calls": [{"call_id": c.call_id, "call": c.call, "status": c.status} for c in artifact.e8.contexts],
    }


async def run_faithfulness_evaluation(verified: Sequence[VerifiedArtifact], judge_config: EvalJudgeConfig, *,
                                      max_calls: int, metric: Any | None = None,
                                      transport: Any | None = None) -> dict[str, Any]:
    """Faithfulness por item entregue; uma tentativa por etapa, para no primeiro erro retryable.

    Só aceita artefatos reverificados por `load_verified_artifact`. `transport` substitui apenas
    a fronteira de rede (LiteLLM); `metric` substitui a métrica inteira e não conta chamadas.
    """
    if any(not isinstance(v, VerifiedArtifact) for v in verified):
        raise EvalConfigError("avaliação exige artefatos reverificados por load_verified_artifact")
    artifacts = [v.artifact for v in verified]
    current = _current_prompt_versions()
    for artifact in artifacts:
        _check_artifact_policy(artifact, current)
    entradas = [e for a in artifacts for e in faithfulness_inputs(a)]
    previstas = CALLS_PER_FAITHFULNESS_ITEM * len(entradas)
    if previstas > max_calls:
        raise EvalBlockedError(f"Bloqueado: orçamento de {max_calls} chamadas menor que as {previstas} previstas")
    orcamento = CallBudget(max_calls)
    with redacted_sdk_logging():
        itens = await _score_entries(entradas, judge_config, orcamento, metric, transport)
    return _faithfulness_report(verified, artifacts, itens, judge_config, current, max_calls,
                                orcamento.calls if metric is None else None)


async def _score_entries(entradas: Sequence[FaithfulnessInput], judge_config: EvalJudgeConfig, orcamento: CallBudget,
                         metric: Any | None, transport: Any | None) -> list[dict[str, Any]]:
    ativa = metric
    if ativa is None and entradas:
        ativa = build_faithfulness_metric(judge_config, budget=orcamento, transport=transport)
    itens: list[dict[str, Any]] = []
    parar = False
    for entrada in entradas:
        registro: dict[str, Any] = {"item_id": entrada.item_id, "chat_turn_id": entrada.chat_turn_id,
                                    "call_id": entrada.call_id, "attempt": entrada.attempt,
                                    "tipo": entrada.tipo, "contexts_sha256": entrada.contexts_sha256,
                                    "n_contexts": len(entrada.retrieved_contexts), "score": None}
        if parar:
            itens.append(dict(registro, status="Não executado"))
            continue
        try:
            resultado = await ativa.ascore(user_input=entrada.user_input, response=entrada.response,
                                           retrieved_contexts=entrada.retrieved_contexts)
        except Exception as exc:  # fronteira do provedor: sanitiza sem mensagem nem repr
            erro = classify_eval_exception(exc)
            if erro["category"] == "invalid_output":
                itens.append(dict(registro, status="invalid_output", provider_category="invalid_output"))
                continue
            itens.append(dict(registro, status="Bloqueado", provider_category=erro["category"],
                              provider_status_code=erro["status_code"], retryable=erro["retryable"]))
            parar = erro["retryable"] is True or erro["category"] == "budget"
            continue
        try:
            valor = _validate_metric_score_value(getattr(resultado, "value", None))
        except EvalProviderError:
            itens.append(dict(registro, status="invalid_output"))
            continue
        itens.append(dict(registro, status="scored", score=valor))
    return itens


def _faithfulness_report(verified: Sequence[VerifiedArtifact], artifacts: Sequence[GenerationEvalArtifact],
                         itens: list[dict[str, Any]], judge_config: EvalJudgeConfig, current: dict[str, str],
                         max_calls: int, calls_made: int | None) -> dict[str, Any]:
    scores = [i["score"] for i in itens if i["status"] == "scored"]
    resumos = []
    for v, a in zip(verified, artifacts):
        resumos.append(dict(_artifact_summary(a), manifest_sha256=v.manifest_sha256,
                            stored_artifact_sha256=v.stored_artifact_sha256))
    return {
        "schema": FAITHFULNESS_REPORT_SCHEMA, "run_id": f"faith-{uuid4().hex[:12]}", "created_at": _now(),
        "config": {"metric": "faithfulness", "ragas_version": pkg_version("ragas"),
                   "faithfulness_prompt_sha256": compute_faithfulness_prompt_sha256(),
                   "judge": judge_config.config_payload(), "judge_config_sha256": judge_config.config_sha256,
                   "prompt_versions": current, "calls_per_item": CALLS_PER_FAITHFULNESS_ITEM,
                   "max_calls": max_calls, "calls_made": calls_made, "retries": 0,
                   "retry_policy": "Instructor max_retries=0; LiteLLM num_retries=0/max_retries=0; admissão por chamada",
                   "mapping": "ADR-0006: user_input=frente|enunciado; response=verso|alternativa correta+explicação; "
                              "retrieved_contexts=trechos citados do contexto enviado ao E8; conteúdo original pré-escape"},
        "artifacts": resumos,
        "items": itens,
        "summary": {"n_items": len(itens), "n_scored": len(scores),
                    "mean": sum(scores) / len(scores) if scores else None, "min": min(scores) if scores else None,
                    "complete": all(i["status"] == "scored" for i in itens)},
        "thresholds": None,
        "interpretation": {"scope": "avaliação inicial da geração real; sem limiar aprovado pelo humano",
                           "historical_comparison": "Não se aplica",
                           "historical_reason": "dataset ressalvas 1.0.1 e baseline ragas-run-b647f4ac987c não cobrem geração",
                           "production_ready": False},
    }


class _ReportConfigView(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    judge_config_sha256: str
    faithfulness_prompt_sha256: str
    prompt_versions: dict[str, str]
    ragas_version: str


class _ReportArtifactView(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    case_id: str | None
    chat_turn_id: str
    case_protocol: dict[str, Any] | None
    models: dict[str, str]
    embedding_key: str
    documents: list[dict[str, Any]]
    reranker: dict[str, Any]


class _ReportItemView(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    chat_turn_id: str
    status: str
    score: Any = None

    @model_validator(mode="after")
    def _score(self) -> _ReportItemView:
        # Mesmo critério das métricas: int/float finito em [0,1]; nunca bool, string, NaN ou clamp.
        if self.status == "scored":
            try:
                self.score = _validate_metric_score_value(self.score)
            except EvalProviderError:
                raise ValueError("item com score inválido") from None
        elif self.score is not None:
            raise ValueError("item sem score avaliado não pode trazer score")
        return self


class _FaithfulnessReportView(BaseModel):
    """Contrato mínimo de um relatório Faithfulness para a comparação; extras são preservados."""

    model_config = ConfigDict(extra="allow", populate_by_name=True, strict=True)
    report_schema: Literal["training-evals/faithfulness-report/v1"] = Field(alias="schema")
    run_id: str
    config: _ReportConfigView
    artifacts: list[_ReportArtifactView]
    items: list[_ReportItemView]

    @model_validator(mode="after")
    def _itens_ligados(self) -> _FaithfulnessReportView:
        turnos = [a.chat_turn_id for a in self.artifacts]
        if len(set(turnos)) != len(turnos):
            raise ValueError("chat_turn_id repetido nos artefatos")
        if any(i.chat_turn_id not in turnos for i in self.items):
            raise ValueError("item ligado a turno ausente dos artefatos")
        return self


def _validate_report(report: Any) -> dict[str, Any]:
    """Relatório de entrada não confiável: fora do contrato vira erro de entrada categórico."""
    if not isinstance(report, dict):
        raise EvalConfigError("relatório fora do contrato do Faithfulness")
    try:
        vista = _FaithfulnessReportView.model_validate(report)
    except ValidationError as exc:
        raise EvalConfigError(f"relatório fora do contrato do Faithfulness: {exc.error_count()} erro(s)") from None
    # Devolve cópia com os scores já validados e normalizados para float; o bruto não é usado.
    validado = json.loads(json.dumps(report))
    for item, visto in zip(validado["items"], vista.items):
        item["score"] = visto.score
    return validado


def _report_by_case(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    casos: dict[str, dict[str, Any]] = {}
    turnos = {}
    for artefato in report["artifacts"]:
        if not artefato.get("case_id"):
            raise EvalBlockedError("Bloqueado: comparação exige case_id em todos os artefatos")
        protocolo = artefato.get("case_protocol")
        if not protocolo or not protocolo.get("request_sha256") or protocolo.get("case_id") != artefato["case_id"]:
            raise EvalBlockedError("Bloqueado: comparação exige protocolo do caso (dataset/pedido executado)")
        if artefato["case_id"] in casos:
            raise EvalBlockedError("Bloqueado: case_id repetido no relatório")
        casos[artefato["case_id"]] = {"artifact": artefato, "scores": []}
        turnos[artefato["chat_turn_id"]] = artefato["case_id"]
    for item in report["items"]:
        if item["status"] != "scored":
            raise EvalBlockedError("Bloqueado: comparação exige relatórios completos (todos os itens com score)")
        casos[turnos[item["chat_turn_id"]]]["scores"].append(item["score"])
    return casos


def compare_faithfulness_reports(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Antes/depois só quando tudo, exceto a recuperação, é idêntico. Senão, bloqueia."""
    before, after = _validate_report(before), _validate_report(after)
    for chave in ("judge_config_sha256", "faithfulness_prompt_sha256", "prompt_versions", "ragas_version"):
        if before["config"].get(chave) != after["config"].get(chave):
            raise EvalBlockedError(f"Bloqueado: relatórios com configuração incompatível ({chave})")
    a, b = _report_by_case(before), _report_by_case(after)
    if set(a) != set(b):
        raise EvalBlockedError("Bloqueado: relatórios com conjuntos de case_id incompatível")
    diferencas: set[str] = set()
    casos = {}
    for caso in sorted(a):
        xa, xb = a[caso]["artifact"], b[caso]["artifact"]
        for chave in ("case_protocol", "models", "embedding_key", "documents"):
            if xa[chave] != xb[chave]:
                raise EvalBlockedError(f"Bloqueado: caso com {chave} incompatível")
        if xa["reranker"] != xb["reranker"]:
            diferencas.add("reranker")
        if not a[caso]["scores"] or not b[caso]["scores"]:
            raise EvalBlockedError("Bloqueado: caso sem itens avaliados em um dos relatórios")
        ma = sum(a[caso]["scores"]) / len(a[caso]["scores"])
        mb = sum(b[caso]["scores"]) / len(b[caso]["scores"])
        casos[caso] = {"mean_before": ma, "mean_after": mb, "delta_mean": mb - ma,
                       "n_before": len(a[caso]["scores"]), "n_after": len(b[caso]["scores"])}
    return {"schema": COMPARISON_SCHEMA, "before_run_id": before["run_id"], "after_run_id": after["run_id"],
            "differences": sorted(diferencas), "cases": casos, "thresholds": None,
            "note": "Geração estocástica: diferença sem repetição/intervalo não prova melhoria."}


# ====================================================================== calibração do juiz


def _judge_context(spec: GenerationDatasetSpec, caso: JudgeCase) -> NumberedContext:
    contexto = caso.context[0]
    titulo = next(d.title for d in spec.documents if d.doc_id == contexto.doc_id)
    return NumberedContext(chunks=[ContextChunk(ref="C1", chunk_id=f"{caso.case_id}:{contexto.doc_id}",
                                                page=None, section_path=[titulo, contexto.section],
                                                text=contexto.text)])


def _accuracy(pares: list[tuple[bool, bool]]) -> dict[str, Any]:
    return {"n": len(pares), "accuracy": sum(e == o for e, o in pares) / len(pares) if pares else None}


async def run_output_judge_calibration(spec: GenerationDatasetSpec, service: Any, *, max_calls: int,
                                       dataset_sha256: str) -> dict[str, Any]:
    """Uma chamada real do juiz E10 por caso, com a rubrica atual; mede concordância com rótulos técnicos."""
    if not re.fullmatch(_SHA256, dataset_sha256 or ""):
        raise EvalConfigError("calibração exige o sha256 do dataset carregado")
    orcamento = CallBudget(max_calls)
    observadas: list[dict[str, Any]] = []
    with redacted_sdk_logging(), _observed_litellm_transport(orcamento, observadas):
        return await _run_output_judge_calibration(spec, service, max_calls=max_calls, dataset_sha256=dataset_sha256,
                                                   budget=orcamento, observed=observadas)


@contextmanager
def _observed_litellm_transport(budget: CallBudget, observed: list[dict[str, Any]]) -> Iterator[None]:
    """Observa a fronteira `litellm.acompletion` usada pelo produto durante a calibração.

    Não substitui o transporte: registra a política de retentativa efetivamente pedida em cada
    chamada, recusa antes da rede a que não tiver `num_retries=0` e `max_retries=0` explícitos e
    admite cada requisição real no orçamento. Restaura o atributo original ao sair.
    """
    _guard_sdk_logging()
    import litellm

    original = litellm.acompletion

    async def observar(*args: Any, **kwargs: Any) -> Any:
        politica = {"num_retries": kwargs.get("num_retries"), "max_retries": kwargs.get("max_retries")}
        observed.append(politica)
        if politica["num_retries"] != 0 or politica["max_retries"] != 0:
            raise EvalRetryPolicyError("Bloqueado: chamada sem retentativa zero explícita; recusada antes do transporte")
        budget.admit()
        return await original(*args, **kwargs)

    litellm.acompletion = observar
    try:
        yield
    finally:
        litellm.acompletion = original


async def _run_output_judge_calibration(spec: GenerationDatasetSpec, service: Any, *, max_calls: int,
                                        dataset_sha256: str, budget: CallBudget,
                                        observed: list[dict[str, Any]]) -> dict[str, Any]:
    meta = service.metadata()
    juiz = meta.models.get("E10", "")
    if not juiz or _is_gemini(juiz):
        raise EvalBlockedError("Bloqueado: juiz de saída ausente ou Gemini (RAG-JUI-002)")
    check_rubric_binding(spec, dict(meta.prompt_versions))
    if len(spec.judge_cases) > max_calls:
        raise EvalBlockedError(f"Bloqueado: orçamento de {max_calls} chamadas menor que {len(spec.judge_cases)} casos")
    casos: list[dict[str, Any]] = []
    parar = False
    chamadas = 0
    for caso in spec.judge_cases:
        e = caso.expected
        registro: dict[str, Any] = {"case_id": caso.case_id, "expected": e.model_dump(), "observed": None,
                                    "agreement": None}
        if parar:
            casos.append(dict(registro, status="Não executado"))
            continue
        chamadas += 1
        antes = len(observed)
        try:
            resultado = await service.judge([validar_item(caso.item)], _judge_context(spec, caso))
        except GenerationError as exc:
            # O produto suprime a causa (`from None`), mas o contexto preserva recusas da fronteira.
            interno = classify_eval_exception(exc)
            if interno["category"] in {"budget", "retry_policy"}:
                casos.append(dict(registro, status="Bloqueado", error_kind=interno["category"]))
                parar = True
                continue
            casos.append(dict(registro, status="Bloqueado", error_kind=exc.kind, provider_status_code=exc.status_code,
                              retryable=exc.retryable))
            parar = exc.retryable is True
            continue
        except Exception as exc:  # fronteira externa, sem mensagem
            erro = classify_eval_exception(exc)
            casos.append(dict(registro, status="Bloqueado", error_kind=erro["category"],
                              provider_status_code=erro["status_code"], retryable=erro["retryable"]))
            parar = erro["retryable"] is True
            continue
        if len(observed) == antes:
            # Sem passagem pela fronteira observada não há prova de política nem de contagem: fecha.
            casos.append(dict(registro, status="Bloqueado", error_kind="transporte_nao_observado"))
            parar = True
            continue
        if len(resultado.vereditos) != 1 or resultado.vereditos[0].item_index != 0:
            casos.append(dict(registro, status="invalid_output"))
            continue
        v = resultado.vereditos[0]
        aprovado = v.fundamentado and v.conforme_rubrica
        criterio = None
        if v.motivo:
            achado = _CRITERION.match(v.motivo)
            criterio = achado.group(1) if achado else None
        casos.append(dict(registro, status="judged", agreement=aprovado == e.aprovado, observed={
            "aprovado": aprovado, "fundamentado": v.fundamentado, "conforme_rubrica": v.conforme_rubrica,
            "criterio_motivo": criterio, "motivo": (v.motivo or "")[:_MOTIVO_MAX] or None}))
    julgados = [c for c in casos if c["status"] == "judged"]
    aprov = [(c["expected"]["aprovado"], c["observed"]["aprovado"]) for c in julgados]
    campos = {}
    for campo in ("fundamentado", "conforme_rubrica"):
        campos[campo] = _accuracy([(c["expected"][campo], c["observed"][campo]) for c in julgados
                                   if c["expected"][campo] is not None])
    rejeitados = [c for c in julgados if not c["expected"]["aprovado"] and not c["observed"]["aprovado"]]
    acertos = [c["observed"]["criterio_motivo"] in c["expected"]["criterios_esperados"] for c in rejeitados]
    return {
        "schema": JUDGE_REPORT_SCHEMA, "run_id": f"judge-calib-{uuid4().hex[:12]}", "created_at": _now(),
        "binding": {"dataset_id": spec.dataset_id, "dataset_version": spec.dataset_version,
                    "dataset_sha256": dataset_sha256,
                    "output_judge": spec.rubric_binding.output_judge, "output_rubric": spec.rubric_binding.output_rubric,
                    "judge_model": juiz, "prompt_source": getattr(meta, "prompt_source", None),
                    "label_source": spec.label_source.model_dump()},
        "protocol": {"calls_per_case": 1, "items_per_call": 1, "max_calls": max_calls,
                     "retries": {"eval_runner": 0,
                                 "observed_transport_policies": sorted({json.dumps(o, sort_keys=True) for o in observed}),
                                 "enforced": "num_retries=0 e max_retries=0 exigidos em cada litellm.acompletion"},
                     "calls_made": chamadas,
                     "calls_made_scope": "chamadas a GenerationService.judge",
                     "http_calls_admitted": budget.calls,
                     "transport_calls_observed": len(observed),
                     "note": "Lote de 1 item por chamada para independência entre casos; produção julga o lote do turno."},
        "cases": casos,
        "metrics": {
            "aprovado": dict(_accuracy(aprov),
                             positives=sum(1 for x, _ in aprov if x), negatives=sum(1 for x, _ in aprov if not x),
                             false_approval=sum(1 for x, o in aprov if not x and o),
                             false_rejection=sum(1 for x, o in aprov if x and not o)),
            **campos,
            "criterion_match_rate": sum(acertos) / len(acertos) if acertos else None,
        },
        "summary": {"n_cases": len(casos), "n_judged": len(julgados), "complete": len(julgados) == len(casos)},
        "thresholds": None,
        "interpretation": {"scope": "avaliação inicial com rótulos técnicos do implementador",
                           "human_calibration": False, "production_ready": False,
                           "note": "Não substitui calibração com 50–100 casos rotulados por humano (ADR-0016)."},
    }


# ====================================================================== CLI


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="executar-avaliacao-geracao",
                                     description="Avaliação da geração real e calibração inicial do juiz de saída (T-022).")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("validar-dataset", help="Valida dataset, hashes e vínculo da rubrica; sem rede.").add_argument(
        "--dataset", default=str(DEFAULT_DATASET_PATH))
    cap = sub.add_parser("capturar", help="Monta artefato verificado das saídas da T-021; sem chamada de modelo.")
    cap.add_argument("--response", required=True)
    cap.add_argument("--diagnostics", required=True)
    cap.add_argument("--e8-capture", required=True)
    cap.add_argument("--owner", required=True)
    cap.add_argument("--session", required=True)
    cap.add_argument("--version", action="append", required=True, dest="versions")
    cap.add_argument("--source-dispatch", required=True)
    cap.add_argument("--case-id")
    cap.add_argument("--dataset", help="Vincula case_id ao dataset geracao; exige --manifest com o pedido executado")
    grupo = cap.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--lineage-env-file", help=".env do sandbox para ler a linhagem Postgres")
    grupo.add_argument("--manifest", help="Manifesto do sandbox com identidade e sha256 dos arquivos")
    cap.add_argument("--out", required=True)
    for nome, ajuda in (("faithfulness", "Faithfulness RAGAS real sobre artefatos reverificados."),
                        ("calibrar-juiz", "Juiz de saída real E10 contra rótulos técnicos.")):
        p = sub.add_parser(nome, help=ajuda)
        if nome == "faithfulness":
            p.add_argument("--manifest", action="append", required=True, dest="manifests")
            p.add_argument("--manifest-sha256", action="append", required=True, dest="manifest_sha256s",
                           help="sha256 do manifesto registrado fora do artefato (um por --manifest)")
            p.add_argument("--artifact", action="append", dest="artifacts",
                           help="Artefato armazenado a conferir contra as fontes (opcional, um por --manifest)")
            p.add_argument("--source-dispatch", required=True)
            p.add_argument("--dataset")
        else:
            p.add_argument("--dataset", default=str(DEFAULT_DATASET_PATH))
        p.add_argument("--env-file", default=".env")
        p.add_argument("--max-calls", type=int)
        p.add_argument("--executar-chamadas-reais", action="store_true",
                       help="Confirmação explícita: faz chamadas pagas ao provedor, uma tentativa cada.")
        p.add_argument("--out", required=True)
    comp = sub.add_parser("comparar", help="Antes/depois só com protocolo e configuração compatíveis.")
    comp.add_argument("--antes", required=True)
    comp.add_argument("--depois", required=True)
    comp.add_argument("--out", required=True)
    return parser


def _write(path: str, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: str) -> dict[str, Any]:
    try:
        dados = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise EvalConfigError("relatório ilegível ou não é JSON") from None
    if not isinstance(dados, dict):
        raise EvalConfigError("relatório não é um objeto JSON")
    return dados


def _run(args: argparse.Namespace) -> int:
    if args.comando == "validar-dataset":
        spec, digest = load_generation_dataset(args.dataset)
        check_rubric_binding(spec, _current_prompt_versions())
        print(json.dumps({"dataset_id": spec.dataset_id, "dataset_version": spec.dataset_version, "sha256": digest,
                          "judge_cases": len(spec.judge_cases), "chat_cases": len(spec.chat_cases),
                          "human_labels": spec.label_source.human_labels}, ensure_ascii=False))
        return 0
    if args.comando == "capturar":
        dataset, digest = load_generation_dataset(args.dataset) if args.dataset else (None, None)
        comum = dict(owner_id=args.owner, session_id=args.session, document_version_ids=args.versions,
                     source_dispatch=args.source_dispatch, case_id=args.case_id, dataset=dataset,
                     dataset_sha256=digest)
        if args.manifest:
            artefato = capture_artifact(args.response, args.diagnostics, args.e8_capture,
                                        sandbox_manifest=args.manifest, **comum)
        else:
            from training_lineage import create_lineage_engine

            engine = create_lineage_engine(args.lineage_env_file)
            try:
                with engine.connect() as conexao:
                    artefato = capture_artifact(args.response, args.diagnostics, args.e8_capture,
                                                lineage_reader=PostgresLineageReader(conexao), **comum)
            finally:
                engine.dispose()
        Path(args.out).write_text(artefato.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return 0
    if args.comando == "comparar":
        _write(args.out, compare_faithfulness_reports(_read_json(args.antes), _read_json(args.depois)))
        return 0
    if args.max_calls is None:
        raise EvalConfigError("--max-calls é obrigatório para chamadas reais")
    if args.comando == "faithfulness":
        if len(args.manifests) != len(args.manifest_sha256s) or (args.artifacts and len(args.artifacts) != len(args.manifests)):
            raise EvalConfigError("informe um --manifest-sha256 (e um --artifact, se usado) por --manifest")
        dataset, digest = load_generation_dataset(args.dataset) if args.dataset else (None, None)
        armazenados = args.artifacts or [None] * len(args.manifests)
        # Reverificação completa antes de ler credenciais ou construir qualquer cliente de rede.
        verificados = [load_verified_artifact(m, manifest_sha256=h, source_dispatch=args.source_dispatch,
                                              artifact_path=a, dataset=dataset, dataset_sha256=digest)
                       for m, h, a in zip(args.manifests, args.manifest_sha256s, armazenados)]
        config = EvalJudgeConfig.from_env(args.env_file)
        _write(args.out, asyncio.run(run_faithfulness_evaluation(verificados, config, max_calls=args.max_calls)))
        return 0
    from training_generation.agents import GenerationService
    from training_generation.settings import GenerationSettings

    spec, digest = load_generation_dataset(args.dataset)
    servico = GenerationService(GenerationSettings.from_env(args.env_file))
    _write(args.out, asyncio.run(run_output_judge_calibration(spec, servico, max_calls=args.max_calls,
                                                              dataset_sha256=digest)))
    return 0


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """Fronteira do CLI: só categorias e mensagens próprias; nunca payload, repr, input_value ou traceback."""
    args = build_arg_parser().parse_args(argv)
    if args.comando in {"faithfulness", "calibrar-juiz"} and not args.executar_chamadas_reais:
        print("Bloqueado: chamadas reais exigem --executar-chamadas-reais e coordenação do principal.", file=sys.stderr)
        return 2
    try:
        with redacted_sdk_logging():
            return _run(args)
    except EvalBlockedError as exc:
        _emit({"status": "Bloqueado", "motivo": str(exc)})
        return 3
    except EvalError as exc:
        _emit({"status": "erro", "motivo": str(exc)})
        return 1
    except ValidationError as exc:
        _emit({"status": "erro", "categoria": "entrada_fora_do_contrato", "erros": exc.error_count()})
        return 1
    except (OSError, ValueError):
        _emit({"status": "erro", "categoria": "entrada_ou_configuracao_invalida"})
        return 1
    except Exception:  # noqa: BLE001 — fronteira final: categoria, sem detalhe
        _emit({"status": "erro", "categoria": "interna"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

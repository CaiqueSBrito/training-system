"""Utilitários para spans manuais, trace chat_turn e filtro de exportação OTel."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Any

from langfuse import LangfuseOtelSpanAttributes, is_default_export_span
from opentelemetry import trace as trace_api
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, format_trace_id

TRACER_NAME = "training_observability"
_INTERNAL_SCOPE_PREFIXES = (TRACER_NAME, "training_", "training.")

logger = logging.getLogger("training_observability")


def should_export_span(span: ReadableSpan) -> bool:
    """Seleciona spans de LLM/ADK (OpenInference, Langfuse) e spans manuais do projeto."""
    if is_default_export_span(span):
        return True

    scope = span.instrumentation_scope
    if scope is None:
        return False

    nome = scope.name
    return any(
        nome == prefixo or nome.startswith(prefixo)
        for prefixo in _INTERNAL_SCOPE_PREFIXES
    )


def _serializar_json(valor: Any) -> str:
    return json.dumps(valor, ensure_ascii=False, default=str)


def _montar_atributos_span(
    *,
    observation_type: str | None = "span",
    input: Any = None,
    output: Any = None,
    metadata: Mapping[str, Any] | None = None,
    prompt_name: str | None = None,
    prompt_version: int | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    trace_name: str | None = None,
    trace_metadata: Mapping[str, Any] | None = None,
    tags: Sequence[str] | None = None,
    attributes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    atributos: dict[str, Any] = {}

    if attributes:
        for chave, valor in attributes.items():
            if valor is not None:
                atributos[chave] = valor

    if observation_type is not None:
        atributos[LangfuseOtelSpanAttributes.OBSERVATION_TYPE] = observation_type
    if input is not None:
        atributos[LangfuseOtelSpanAttributes.OBSERVATION_INPUT] = _serializar_json(input)
    if output is not None:
        atributos[LangfuseOtelSpanAttributes.OBSERVATION_OUTPUT] = _serializar_json(
            output
        )
    if metadata:
        for chave, valor in metadata.items():
            if valor is not None:
                atributos[
                    f"{LangfuseOtelSpanAttributes.OBSERVATION_METADATA}.{chave}"
                ] = _serializar_json(valor)
    if prompt_name is not None:
        atributos[LangfuseOtelSpanAttributes.OBSERVATION_PROMPT_NAME] = prompt_name
    if prompt_version is not None:
        atributos[LangfuseOtelSpanAttributes.OBSERVATION_PROMPT_VERSION] = (
            prompt_version
        )
    if trace_name is not None:
        atributos[LangfuseOtelSpanAttributes.TRACE_NAME] = trace_name
    if user_id is not None:
        atributos[LangfuseOtelSpanAttributes.TRACE_USER_ID] = user_id
    if session_id is not None:
        atributos[LangfuseOtelSpanAttributes.TRACE_SESSION_ID] = session_id
    if trace_metadata:
        for chave, valor in trace_metadata.items():
            if valor is not None:
                atributos[
                    f"{LangfuseOtelSpanAttributes.TRACE_METADATA}.{chave}"
                ] = _serializar_json(valor)
    if tags:
        atributos[LangfuseOtelSpanAttributes.TRACE_TAGS] = list(tags)

    return atributos


class _ManualSpanContext:
    """Context manager síncrono e assíncrono para spans manuais OpenTelemetry."""

    def __init__(
        self,
        name: str,
        *,
        tracer_provider: TracerProvider | None = None,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        self._name = name
        self._tracer_provider = tracer_provider
        self._kind = kind
        self._attributes = dict(attributes) if attributes else None
        self._cm: AbstractContextManager[Span] | None = None
        self._span: Span | None = None

    def _obter_tracer(self) -> trace_api.Tracer:
        if self._tracer_provider is not None:
            return self._tracer_provider.get_tracer(TRACER_NAME)
        return trace_api.get_tracer(TRACER_NAME)

    def __enter__(self) -> Span:
        tracer = self._obter_tracer()
        self._cm = tracer.start_as_current_span(
            self._name,
            kind=self._kind,
            attributes=self._attributes,
            record_exception=False,
            set_status_on_exception=False,
        )
        self._span = self._cm.__enter__()
        return self._span

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        try:
            if self._span is not None and exc_val is not None:
                self._span.record_exception(exc_val)
                self._span.set_status(Status(StatusCode.ERROR, str(exc_val)))
        except Exception as obs_exc:
            logger.warning(
                "Falha ao registrar exceção no span '%s': %s", self._name, obs_exc
            )
        finally:
            if self._cm is not None:
                try:
                    self._cm.__exit__(exc_type, exc_val, exc_tb)
                except Exception as obs_exc:
                    logger.warning(
                        "Falha ao encerrar o span '%s': %s", self._name, obs_exc
                    )
        return False

    async def __aenter__(self) -> Span:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        return self.__exit__(exc_type, exc_val, exc_tb)


def manual_span(
    name: str,
    *,
    tracer_provider: TracerProvider | None = None,
    observation_type: str | None = "span",
    input: Any = None,
    output: Any = None,
    metadata: Mapping[str, Any] | None = None,
    prompt_name: str | None = None,
    prompt_version: int | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    trace_name: str | None = None,
    trace_metadata: Mapping[str, Any] | None = None,
    tags: Sequence[str] | None = None,
    attributes: Mapping[str, Any] | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
) -> _ManualSpanContext:
    """Abre um span manual compatível com `with` e `async with` e atributos Langfuse."""
    atributos = _montar_atributos_span(
        observation_type=observation_type,
        input=input,
        output=output,
        metadata=metadata,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        user_id=user_id,
        session_id=session_id,
        trace_name=trace_name,
        trace_metadata=trace_metadata,
        tags=tags,
        attributes=attributes,
    )
    return _ManualSpanContext(
        name,
        tracer_provider=tracer_provider,
        kind=kind,
        attributes=atributos,
    )


def chat_turn_span(
    chat_turn_id: str,
    *,
    user_id: str,
    session_id: str,
    tracer_provider: TracerProvider | None = None,
    input: Any = None,
    output: Any = None,
    metadata: Mapping[str, Any] | None = None,
    tags: Sequence[str] | None = None,
    attributes: Mapping[str, Any] | None = None,
) -> _ManualSpanContext:
    """Abre o span raiz `chat_turn` com user_id, session_id e chat_turn_id nos metadados."""
    metadados_trace: dict[str, Any] = dict(metadata) if metadata else {}
    metadados_trace["chat_turn_id"] = chat_turn_id

    return manual_span(
        "chat_turn",
        tracer_provider=tracer_provider,
        observation_type="span",
        input=input,
        output=output,
        metadata=metadados_trace,
        user_id=user_id,
        session_id=session_id,
        trace_name="chat_turn",
        trace_metadata=metadados_trace,
        tags=tags,
        attributes=attributes,
    )


def get_current_trace_id() -> str | None:
    """Retorna o trace_id hexadecimal (32 chars) do span ativo, ou None se não houver."""
    span = trace_api.get_current_span()
    contexto = span.get_span_context()
    if not contexto.is_valid:
        return None
    return format_trace_id(contexto.trace_id)

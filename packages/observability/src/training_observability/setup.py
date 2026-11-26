"""Inicialização do OpenTelemetry com Langfuse e instrumentação do Google ADK."""

from __future__ import annotations

import importlib.util
import logging
import threading
from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Any

from langfuse import Langfuse
from langfuse._client.resource_manager import LangfuseResourceManager
from openinference.instrumentation.google_adk import GoogleADKInstrumentor
from opentelemetry import trace as trace_api
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter

from training_observability.config import ObservabilityConfig
from training_observability.exporter import (
    FaultTolerantSpanExporter,
    build_langfuse_otlp_exporter,
)
from training_observability.spans import (
    TRACER_NAME,
    _ManualSpanContext,
    chat_turn_span,
    manual_span,
    should_export_span,
)

logger = logging.getLogger("training_observability")

_active_lock = threading.Lock()
_active_handle: ObservabilityHandle | None = None


def instrument_google_adk(
    tracer_provider: TracerProvider,
    *,
    instrumentor: Any = None,
    require_adk: bool = False,
) -> bool:
    """Instrumenta o Google ADK via OpenInference usando o TracerProvider informado."""
    if instrumentor is None:
        if importlib.util.find_spec("google.adk") is None:
            mensagem = (
                "Pacote 'google-adk' não está instalado neste ambiente; "
                "GoogleADKInstrumentor será ativado quando google-adk estiver presente."
            )
            if require_adk:
                raise RuntimeError(mensagem)
            logger.info(mensagem)
            return False
        instrumentor = GoogleADKInstrumentor()

    if not getattr(instrumentor, "is_instrumented_by_opentelemetry", False):
        instrumentor.instrument(tracer_provider=tracer_provider)

    instrumentado = bool(
        getattr(instrumentor, "is_instrumented_by_opentelemetry", False)
    )
    if require_adk and not instrumentado:
        raise RuntimeError("Falha ao ativar GoogleADKInstrumentor.")
    return instrumentado


class ObservabilityHandle:
    """Ciclo de vida da observabilidade configurada (OTel, Langfuse e ADK)."""

    def __init__(
        self,
        *,
        config: ObservabilityConfig,
        tracer_provider: TracerProvider,
        langfuse: Langfuse,
        exporter: FaultTolerantSpanExporter,
        adk_instrumentor: Any = None,
        adk_instrumented: bool = False,
    ) -> None:
        self.config = config
        self.tracer_provider = tracer_provider
        self.tracer = tracer_provider.get_tracer(TRACER_NAME)
        self.langfuse = langfuse
        self.exporter = exporter
        self.adk_instrumented = adk_instrumented
        self._adk_instrumentor = adk_instrumentor
        self._shutdown_lock = threading.Lock()
        self._is_shutdown = False

    def span(
        self,
        name: str,
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
    ) -> _ManualSpanContext:
        """Abre um span manual vinculado ao TracerProvider desta instância."""
        return manual_span(
            name,
            tracer_provider=self.tracer_provider,
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

    def chat_turn(
        self,
        chat_turn_id: str,
        *,
        user_id: str,
        session_id: str,
        input: Any = None,
        output: Any = None,
        metadata: Mapping[str, Any] | None = None,
        tags: Sequence[str] | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> _ManualSpanContext:
        """Abre o span raiz `chat_turn` vinculado a esta instância."""
        return chat_turn_span(
            chat_turn_id,
            user_id=user_id,
            session_id=session_id,
            tracer_provider=self.tracer_provider,
            input=input,
            output=output,
            metadata=metadata,
            tags=tags,
            attributes=attributes,
        )

    def flush(self, timeout_millis: int = 5_000) -> bool:
        """Força o envio assíncrono dos spans pendentes sem propagar falhas do Langfuse."""
        ok = True
        try:
            ok = bool(self.tracer_provider.force_flush(timeout_millis))
        except Exception as exc:
            logger.warning(
                "Falha ao fazer flush do TracerProvider; continuando normalmente: %s",
                exc,
            )
            ok = False

        try:
            self.langfuse.flush()
        except Exception as exc:
            logger.warning(
                "Falha ao fazer flush do cliente Langfuse; continuando normalmente: %s",
                exc,
            )
            ok = False

        return ok

    def shutdown(self) -> None:
        """Encerra instrumentação, Langfuse e TracerProvider de forma idempotente e segura."""
        global _active_handle

        with self._shutdown_lock:
            if self._is_shutdown:
                return
            self._is_shutdown = True

        if self.adk_instrumented and self._adk_instrumentor is not None:
            try:
                if getattr(
                    self._adk_instrumentor, "is_instrumented_by_opentelemetry", False
                ):
                    self._adk_instrumentor.uninstrument()
            except Exception as exc:
                logger.warning("Falha ao desinstrumentar Google ADK: %s", exc)

        resources = getattr(self.langfuse, "_resources", None)
        try:
            self.langfuse.shutdown()
        except Exception as exc:
            logger.warning("Falha ao encerrar cliente Langfuse: %s", exc)
        finally:
            if resources is not None:
                with LangfuseResourceManager._lock:
                    if (
                        LangfuseResourceManager._instances.get(self.config.public_key)
                        is resources
                    ):
                        LangfuseResourceManager._instances.pop(
                            self.config.public_key, None
                        )

        try:
            self.tracer_provider.shutdown()
        except Exception as exc:
            logger.warning("Falha ao encerrar TracerProvider: %s", exc)

        with _active_lock:
            if _active_handle is self:
                _active_handle = None

    def __enter__(self) -> ObservabilityHandle:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        self.shutdown()
        return False


def configure_observability(
    *,
    config: ObservabilityConfig | None = None,
    env: Mapping[str, str] | None = None,
    tracer_provider: TracerProvider | None = None,
    span_exporter: SpanExporter | None = None,
    adk_instrumentor: Any = None,
    set_global_tracer_provider: bool = True,
    **config_overrides: Any,
) -> ObservabilityHandle:
    """Configura OpenTelemetry exportando ao Langfuse e instrumenta o Google ADK."""
    global _active_handle

    resolved_config = (
        config
        if config is not None
        else ObservabilityConfig.from_env(env, **config_overrides)
    )

    if tracer_provider is None:
        resource_attrs: dict[str, str] = {"service.name": resolved_config.service_name}
        if resolved_config.environment:
            resource_attrs["deployment.environment"] = resolved_config.environment
        tracer_provider = TracerProvider(resource=Resource.create(resource_attrs))

    if set_global_tracer_provider:
        trace_api.set_tracer_provider(tracer_provider)

    base_exporter = (
        span_exporter
        if span_exporter is not None
        else build_langfuse_otlp_exporter(resolved_config)
    )
    fault_tolerant_exporter = FaultTolerantSpanExporter(base_exporter)

    langfuse_client = Langfuse(
        public_key=resolved_config.public_key,
        secret_key=resolved_config.secret_key,
        base_url=resolved_config.base_url,
        timeout=resolved_config.timeout,
        flush_at=resolved_config.flush_at,
        flush_interval=resolved_config.flush_interval,
        environment=resolved_config.environment,
        release=resolved_config.release,
        tracer_provider=tracer_provider,
        span_exporter=fault_tolerant_exporter,
        should_export_span=should_export_span,
    )

    adk_instrumented = False
    instrumentor_usado = adk_instrumentor
    if resolved_config.instrument_adk:
        if instrumentor_usado is None and importlib.util.find_spec("google.adk") is not None:
            instrumentor_usado = GoogleADKInstrumentor()
        adk_instrumented = instrument_google_adk(
            tracer_provider,
            instrumentor=instrumentor_usado,
        )

    handle = ObservabilityHandle(
        config=resolved_config,
        tracer_provider=tracer_provider,
        langfuse=langfuse_client,
        exporter=fault_tolerant_exporter,
        adk_instrumentor=instrumentor_usado,
        adk_instrumented=adk_instrumented,
    )

    if set_global_tracer_provider:
        with _active_lock:
            _active_handle = handle

    return handle


def get_observability() -> ObservabilityHandle | None:
    """Retorna o ObservabilityHandle global ativo, se configurado."""
    with _active_lock:
        return _active_handle


def shutdown_observability() -> None:
    """Encerra o ObservabilityHandle global ativo, se existir."""
    handle = get_observability()
    if handle is not None:
        handle.shutdown()

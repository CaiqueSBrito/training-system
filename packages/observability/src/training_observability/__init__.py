"""Observabilidade do Training System (OpenTelemetry, Langfuse e Google ADK)."""

from training_observability.config import (
    ObservabilityConfig,
    ObservabilityConfigError,
)
from training_observability.exporter import (
    FaultTolerantSpanExporter,
    build_langfuse_otlp_exporter,
)
from training_observability.setup import (
    ObservabilityHandle,
    configure_observability,
    get_observability,
    instrument_google_adk,
    shutdown_observability,
)
from training_observability.spans import (
    TRACER_NAME,
    chat_turn_span,
    get_current_trace_id,
    manual_span,
    should_export_span,
)

__all__ = [
    "FaultTolerantSpanExporter",
    "ObservabilityConfig",
    "ObservabilityConfigError",
    "ObservabilityHandle",
    "TRACER_NAME",
    "build_langfuse_otlp_exporter",
    "chat_turn_span",
    "configure_observability",
    "get_current_trace_id",
    "get_observability",
    "instrument_google_adk",
    "manual_span",
    "should_export_span",
    "shutdown_observability",
]

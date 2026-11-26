"""Exportador OTLP para o Langfuse tolerante a falha com registro de descarte."""

from __future__ import annotations

import base64
import logging
import os
import threading
from collections.abc import Sequence

from langfuse._version import __version__ as langfuse_version
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from training_observability.config import ObservabilityConfig

logger = logging.getLogger("training_observability")


def build_langfuse_otlp_exporter(config: ObservabilityConfig) -> OTLPSpanExporter:
    """Constrói o OTLPSpanExporter autenticado para o endpoint OTLP do Langfuse."""
    credencial = f"{config.public_key}:{config.secret_key}".encode("utf-8")
    basic_auth = "Basic " + base64.b64encode(credencial).decode("ascii")

    headers = {
        "Authorization": basic_auth,
        "x-langfuse-sdk-name": "python",
        "x-langfuse-sdk-version": langfuse_version,
        "x-langfuse-public-key": config.public_key,
        "x-langfuse-ingestion-version": "4",
    }

    export_path = os.environ.get("LANGFUSE_OTEL_TRACES_EXPORT_PATH", "").strip()
    if export_path:
        endpoint = f"{config.base_url}/{export_path.lstrip('/')}"
    else:
        endpoint = f"{config.base_url}/api/public/otel/v1/traces"

    return OTLPSpanExporter(
        endpoint=endpoint,
        headers=headers,
        timeout=config.timeout,
        compression=Compression.Gzip,
    )


class FaultTolerantSpanExporter(SpanExporter):
    """Encapsula um SpanExporter descartando spans com log quando o destino falha."""

    def __init__(self, exporter: SpanExporter) -> None:
        self._exporter = exporter
        self._lock = threading.Lock()
        self._export_failures = 0
        self._dropped_spans = 0

    @property
    def export_failures(self) -> int:
        with self._lock:
            return self._export_failures

    @property
    def dropped_spans(self) -> int:
        with self._lock:
            return self._dropped_spans

    def _registrar_descarte(
        self, spans: Sequence[ReadableSpan], motivo: str
    ) -> None:
        quantidade = len(spans)
        nomes = [span.name for span in spans[:5]]
        resumo_nomes = ", ".join(nomes) if nomes else "-"
        if quantidade > 5:
            resumo_nomes = f"{resumo_nomes}, +{quantidade - 5}"

        with self._lock:
            self._export_failures += 1
            self._dropped_spans += quantidade

        logger.warning(
            "Langfuse indisponível ou falha na exportação de traces; "
            "descartando %d span(s) [%s]. Motivo: %s",
            quantidade,
            resumo_nomes,
            motivo,
        )

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            resultado = self._exporter.export(spans)
        except Exception as exc:
            self._registrar_descarte(spans, f"{type(exc).__name__}: {exc}")
            return SpanExportResult.FAILURE

        if resultado != SpanExportResult.SUCCESS:
            self._registrar_descarte(spans, f"status={resultado.name}")
            return SpanExportResult.FAILURE

        return resultado

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        try:
            return bool(self._exporter.force_flush(timeout_millis))
        except Exception as exc:
            logger.warning(
                "Falha no force_flush do exportador Langfuse; seguindo sem interromper "
                "a aplicação. Motivo: %s: %s",
                type(exc).__name__,
                exc,
            )
            return False

    def shutdown(self) -> None:
        try:
            self._exporter.shutdown()
        except Exception as exc:
            logger.warning(
                "Falha no shutdown do exportador Langfuse; seguindo sem interromper "
                "a aplicação. Motivo: %s: %s",
                type(exc).__name__,
                exc,
            )

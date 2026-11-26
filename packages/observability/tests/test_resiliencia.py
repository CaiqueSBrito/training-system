"""OBS-TRC-002 — resiliência quando o Langfuse está indisponível e exportação assíncrona."""

import logging
import socket
import threading
import time
from collections.abc import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from training_observability import (
    FaultTolerantSpanExporter,
    ObservabilityConfig,
    configure_observability,
)


def _porta_local_fechada() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_obs_trc_002_langfuse_inacessivel_nao_derruba_aplicacao_e_registra_descarte(
    caplog,
):
    porta = _porta_local_fechada()
    segredo = "sk-lf-nao-pode-vazar-nos-logs"
    config = ObservabilityConfig(
        public_key="pk-lf-indisponivel-teste",
        secret_key=segredo,
        base_url=f"http://127.0.0.1:{porta}",
        timeout=1,
        flush_interval=0.1,
        instrument_adk=False,
    )

    caplog.set_level(logging.WARNING, logger="training_observability")

    obs = configure_observability(config=config, set_global_tracer_provider=False)
    try:
        with obs.chat_turn(
            chat_turn_id="turn-offline-1",
            user_id="aluno-1",
            session_id="sessao-offline",
            input={"prompt": "flashcards de biologia"},
        ):
            with obs.span("E1_higiene", metadata={"etapa": "E1"}) as span:
                resultado_aplicacao = "resposta entregue normalmente"
                span.set_attribute("training.ok", True)

        assert resultado_aplicacao == "resposta entregue normalmente"
        obs.flush(timeout_millis=4000)
    finally:
        obs.shutdown()

    assert obs.exporter.export_failures >= 1
    assert obs.exporter.dropped_spans >= 2

    mensagens = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("descart" in msg.lower() for msg in mensagens), (
        f"Esperava registro em log sobre descarte de spans, obtido: {mensagens}"
    )
    assert all(segredo not in msg for msg in mensagens)


def test_obs_trc_002_exportacao_assincrona_nao_bloqueia_execucao_do_span(caplog):
    evento_parar = threading.Event()
    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.bind(("127.0.0.1", 0))
    porta = servidor.getsockname()[1]
    servidor.listen(5)
    servidor.settimeout(0.2)

    conexoes: list[socket.socket] = []

    def aceitar_e_travar():
        while not evento_parar.is_set():
            try:
                conn, _ = servidor.accept()
                conexoes.append(conn)
            except TimeoutError:
                continue
            except OSError:
                break

    thread = threading.Thread(target=aceitar_e_travar, daemon=True)
    thread.start()

    caplog.set_level(logging.WARNING, logger="training_observability")
    config = ObservabilityConfig(
        public_key="pk-lf-lento-teste",
        secret_key="sk-lf-lento-teste",
        base_url=f"http://127.0.0.1:{porta}",
        timeout=1,
        flush_interval=0.2,
        instrument_adk=False,
    )

    obs = configure_observability(config=config, set_global_tracer_provider=False)
    try:
        inicio = time.monotonic()
        with obs.span("E5_recuperacao_assincrona"):
            trabalho = 2 + 2
        duracao = time.monotonic() - inicio

        assert trabalho == 4
        assert duracao < 0.5, (
            f"Span bloqueou por {duracao:.2f}s; a exportação deveria ser assíncrona."
        )
        obs.flush(timeout_millis=4000)
    finally:
        obs.shutdown()
        evento_parar.set()
        for conn in conexoes:
            conn.close()
        servidor.close()
        thread.join(timeout=2)

    assert obs.exporter.dropped_spans >= 1
    assert any(
        "descart" in r.getMessage().lower()
        for r in caplog.records
        if r.levelno >= logging.WARNING
    )


class _ExporterComExcecao(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        raise RuntimeError("falha simulada de transporte")

    def shutdown(self) -> None:
        raise RuntimeError("falha simulada no shutdown")


def test_obs_trc_002_fault_tolerant_exporter_captura_excecao_e_registra_descarte(
    caplog,
):
    caplog.set_level(logging.WARNING, logger="training_observability")
    wrapper = FaultTolerantSpanExporter(_ExporterComExcecao())

    resultado = wrapper.export(())
    assert resultado == SpanExportResult.FAILURE
    assert wrapper.export_failures == 1

    wrapper.shutdown()

    mensagens = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("descart" in msg.lower() for msg in mensagens)

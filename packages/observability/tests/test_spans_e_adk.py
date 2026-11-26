"""Spans manuais, trace chat_turn, suporte assíncrono e instrumentação real do Google ADK."""

import asyncio
import importlib.util
import json
from collections.abc import AsyncGenerator

import pytest
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from openinference.instrumentation.google_adk import GoogleADKInstrumentor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode, format_trace_id

from training_observability import (
    ObservabilityConfig,
    configure_observability,
    get_current_trace_id,
    instrument_google_adk,
    manual_span,
    should_export_span,
)


class _AgenteEtapa(BaseAgent):
    """Agente ADK determinístico sem chamada de rede para provar a instrumentação."""

    resposta: str

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            content=types.Content(
                role="model",
                parts=[types.Part(text=self.resposta)],
            ),
        )


class _AgenteSequencial(BaseAgent):
    """Agente pai ADK determinístico que encadeia subagentes via run_async."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        for sub_agente in self.sub_agents:
            async for evento in sub_agente.run_async(ctx):
                yield evento


def _config_teste() -> ObservabilityConfig:
    return ObservabilityConfig(
        public_key="pk-lf-unit-spans",
        secret_key="sk-lf-unit-spans",
        base_url="http://localhost:3000",
        timeout=1,
        flush_interval=0.1,
        instrument_adk=False,
    )


def test_manual_span_cria_hierarquia_chat_turn_e_atributos_langfuse():
    memoria = InMemorySpanExporter()
    obs = configure_observability(
        config=_config_teste(),
        span_exporter=memoria,
        set_global_tracer_provider=False,
    )
    try:
        with obs.chat_turn(
            chat_turn_id="turn-abc-123",
            user_id="usuario-42",
            session_id="sessao-99",
            input={"prompt": "baralho sobre mitose"},
            metadata={"origem": "web"},
        ):
            trace_id_atual = get_current_trace_id()
            with manual_span(
                "E1_higiene",
                tracer_provider=obs.tracer_provider,
                observation_type="guardrail",
                input={"raw": "baralho sobre mitose"},
                output={"normalizado": "baralho sobre mitose"},
                metadata={"etapa": "E1"},
                prompt_name="classificador",
                prompt_version=3,
            ):
                pass

        obs.flush(timeout_millis=5000)
    finally:
        obs.shutdown()

    spans = memoria.get_finished_spans()
    assert len(spans) == 2

    por_nome = {s.name: s for s in spans}
    assert set(por_nome) == {"chat_turn", "E1_higiene"}

    raiz = por_nome["chat_turn"]
    filho = por_nome["E1_higiene"]

    assert trace_id_atual == format_trace_id(raiz.context.trace_id)
    assert filho.parent is not None
    assert filho.parent.span_id == raiz.context.span_id

    assert raiz.attributes["langfuse.trace.name"] == "chat_turn"
    assert raiz.attributes["user.id"] == "usuario-42"
    assert raiz.attributes["session.id"] == "sessao-99"
    assert json.loads(raiz.attributes["langfuse.trace.metadata.chat_turn_id"]) == "turn-abc-123"
    assert json.loads(raiz.attributes["langfuse.trace.metadata.origem"]) == "web"

    assert filho.attributes["langfuse.observation.type"] == "guardrail"
    assert filho.attributes["langfuse.observation.prompt.name"] == "classificador"
    assert filho.attributes["langfuse.observation.prompt.version"] == 3
    assert json.loads(filho.attributes["langfuse.observation.metadata.etapa"]) == "E1"
    assert json.loads(filho.attributes["langfuse.observation.output"]) == {
        "normalizado": "baralho sobre mitose"
    }


def test_manual_span_suporta_async_with_e_propaga_excecao_da_aplicacao():
    memoria = InMemorySpanExporter()
    obs = configure_observability(
        config=_config_teste(),
        span_exporter=memoria,
        set_global_tracer_provider=False,
    )

    async def executar():
        with pytest.raises(ValueError, match="erro da aplicacao"):
            async with obs.span("E9_validacao"):
                raise ValueError("erro da aplicacao")

    try:
        asyncio.run(executar())
        obs.flush(timeout_millis=5000)
    finally:
        obs.shutdown()

    spans = memoria.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "E9_validacao"
    assert span.status.status_code == StatusCode.ERROR
    assert any(event.name == "exception" for event in span.events)


def test_instrumentacao_adk_real_emite_spans_hierarquia_atributos_e_desinstrumenta():
    memoria = InMemorySpanExporter()
    obs = configure_observability(
        config=ObservabilityConfig(
            public_key="pk-lf-unit-adk",
            secret_key="sk-lf-unit-adk",
            base_url="http://localhost:3000",
            timeout=1,
            flush_interval=0.1,
            instrument_adk=True,
        ),
        span_exporter=memoria,
        set_global_tracer_provider=False,
    )

    assert obs.adk_instrumented is True
    assert GoogleADKInstrumentor().is_instrumented_by_opentelemetry is True

    async def executar() -> tuple[str | None, list[Event]]:
        agente_raiz = _AgenteSequencial(
            name="pipeline_estudo",
            description="Pipeline determinístico de estudo",
            sub_agents=[
                _AgenteEtapa(
                    name="etapa_classificar",
                    description="Classifica pedido do aluno",
                    resposta="intencao=flashcard",
                ),
                _AgenteEtapa(
                    name="etapa_gerar",
                    description="Gera rascunho de flashcard",
                    resposta="frente: Mitose | verso: Divisão celular",
                ),
            ],
        )
        sessoes = InMemorySessionService()
        sessao = await sessoes.create_session(
            app_name="training_app",
            user_id="aluno-adk-1",
            session_id="sessao-adk-1",
        )
        runner = Runner(
            app_name="training_app",
            agent=agente_raiz,
            session_service=sessoes,
        )

        async with obs.chat_turn(
            chat_turn_id="turn-adk-real-1",
            user_id="aluno-adk-1",
            session_id="sessao-adk-1",
            input={"prompt": "Crie flashcards sobre mitose"},
        ):
            trace_id_ativo = get_current_trace_id()
            eventos: list[Event] = []
            async for evento in runner.run_async(
                user_id="aluno-adk-1",
                session_id=sessao.id,
                new_message=types.Content(
                    role="user",
                    parts=[types.Part(text="Crie flashcards sobre mitose")],
                ),
            ):
                eventos.append(evento)
            return trace_id_ativo, eventos

    try:
        trace_id_ativo, eventos = asyncio.run(executar())
        obs.flush(timeout_millis=5000)
    finally:
        obs.shutdown()

    assert GoogleADKInstrumentor().is_instrumented_by_opentelemetry is False
    assert len(eventos) == 2

    spans = memoria.get_finished_spans()
    por_nome = {span.name: span for span in spans}
    nomes_esperados = {
        "chat_turn",
        "invocation [training_app]",
        "agent_run [pipeline_estudo]",
        "agent_run [etapa_classificar]",
        "agent_run [etapa_gerar]",
    }
    assert set(por_nome) == nomes_esperados
    assert all(should_export_span(span) for span in spans)

    raiz = por_nome["chat_turn"]
    invocacao = por_nome["invocation [training_app]"]
    pipeline = por_nome["agent_run [pipeline_estudo]"]
    classificar = por_nome["agent_run [etapa_classificar]"]
    gerar = por_nome["agent_run [etapa_gerar]"]

    # Mesmo trace_id em toda a árvore (span manual + spans reais do ADK)
    assert trace_id_ativo == format_trace_id(raiz.context.trace_id)
    assert {span.context.trace_id for span in spans} == {raiz.context.trace_id}

    # Escopos de instrumentação
    assert raiz.instrumentation_scope is not None
    assert raiz.instrumentation_scope.name == "training_observability"
    for adk_span in (invocacao, pipeline, classificar, gerar):
        assert adk_span.instrumentation_scope is not None
        assert (
            adk_span.instrumentation_scope.name
            == "openinference.instrumentation.google_adk"
        )

    # Hierarquia pai -> filho preservada via runner.run_async
    assert raiz.parent is None
    assert invocacao.parent is not None
    assert invocacao.parent.span_id == raiz.context.span_id
    assert pipeline.parent is not None
    assert pipeline.parent.span_id == invocacao.context.span_id
    assert classificar.parent is not None
    assert classificar.parent.span_id == pipeline.context.span_id
    assert gerar.parent is not None
    assert gerar.parent.span_id == pipeline.context.span_id

    # Atributos OpenInference nos spans do ADK
    assert invocacao.attributes["openinference.span.kind"] == "CHAIN"
    assert invocacao.attributes["user.id"] == "aluno-adk-1"
    assert invocacao.attributes["session.id"] == "sessao-adk-1"
    assert invocacao.attributes["input.mime_type"] == "application/json"
    assert "Crie flashcards sobre mitose" in invocacao.attributes["input.value"]
    assert invocacao.attributes["output.mime_type"] == "application/json"
    assert "frente: Mitose" in invocacao.attributes["output.value"]

    assert pipeline.attributes["openinference.span.kind"] == "AGENT"
    assert pipeline.attributes["agent.name"] == "pipeline_estudo"
    assert (
        pipeline.attributes["gen_ai.agent.description"]
        == "Pipeline determinístico de estudo"
    )
    assert classificar.attributes["openinference.span.kind"] == "AGENT"
    assert classificar.attributes["agent.name"] == "etapa_classificar"
    assert "intencao=flashcard" in classificar.attributes["output.value"]
    assert gerar.attributes["openinference.span.kind"] == "AGENT"
    assert gerar.attributes["agent.name"] == "etapa_gerar"
    assert "frente: Mitose" in gerar.attributes["output.value"]


def test_instrument_google_adk_ativa_instrumentor_real_e_trata_ausencia_do_pacote(
    monkeypatch,
):
    memoria = InMemorySpanExporter()
    obs = configure_observability(
        config=ObservabilityConfig(
            public_key="pk-lf-unit-adk-direto",
            secret_key="sk-lf-unit-adk-direto",
            base_url="http://localhost:3000",
            timeout=1,
            flush_interval=0.1,
            instrument_adk=False,
        ),
        span_exporter=memoria,
        set_global_tracer_provider=False,
    )

    async def executar_agente_simples() -> None:
        agente = _AgenteEtapa(
            name="agente_direto",
            description="Teste direto de instrument_google_adk",
            resposta="ok-direto",
        )
        sessoes = InMemorySessionService()
        sessao = await sessoes.create_session(
            app_name="app_direto",
            user_id="usuario-direto",
            session_id="sessao-direto",
        )
        runner = Runner(
            app_name="app_direto",
            agent=agente,
            session_service=sessoes,
        )
        async for _ in runner.run_async(
            user_id="usuario-direto",
            session_id=sessao.id,
            new_message=types.Content(
                role="user", parts=[types.Part(text="ping")]
            ),
        ):
            pass

    instrumentor = GoogleADKInstrumentor()
    try:
        assert instrument_google_adk(obs.tracer_provider, require_adk=True) is True
        assert instrumentor.is_instrumented_by_opentelemetry is True

        asyncio.run(executar_agente_simples())
        obs.flush(timeout_millis=5000)
    finally:
        if instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.uninstrument()
        obs.shutdown()

    nomes = {span.name for span in memoria.get_finished_spans()}
    assert {"invocation [app_direto]", "agent_run [agente_direto]"}.issubset(nomes)

    # Quando google-adk está ausente no ambiente: retorna False sem derrubar
    # e falha explicitamente se require_adk=True.
    find_spec_original = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda nome, *args, **kwargs: (
            None if nome == "google.adk" else find_spec_original(nome, *args, **kwargs)
        ),
    )
    memoria_sem_adk = InMemorySpanExporter()
    obs_sem_adk = configure_observability(
        config=_config_teste(),
        span_exporter=memoria_sem_adk,
        set_global_tracer_provider=False,
    )
    try:
        assert instrument_google_adk(obs_sem_adk.tracer_provider) is False
        with pytest.raises(RuntimeError, match="google-adk"):
            instrument_google_adk(obs_sem_adk.tracer_provider, require_adk=True)
    finally:
        obs_sem_adk.shutdown()


def test_should_export_span_aceita_openinference_e_training_observability():
    memoria = InMemorySpanExporter()
    obs = configure_observability(
        config=_config_teste(),
        span_exporter=memoria,
        set_global_tracer_provider=False,
    )
    try:
        with obs.tracer_provider.get_tracer("training_observability").start_as_current_span(
            "manual"
        ):
            pass
        with obs.tracer_provider.get_tracer(
            "openinference.instrumentation.google_adk"
        ).start_as_current_span("adk"):
            pass
        obs.flush(timeout_millis=5000)
    finally:
        obs.shutdown()

    spans = memoria.get_finished_spans()
    assert len(spans) == 2
    assert all(should_export_span(span) for span in spans)

"""Integração real com o Langfuse local (T-003) — OBS-TRC-002 / OBS-CFG-001."""

import asyncio
import os
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from training_observability import (
    ObservabilityConfig,
    configure_observability,
    get_current_trace_id,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


class _AgenteEtapaLangfuse(BaseAgent):
    """Agente ADK determinístico para validar envio de spans reais ao Langfuse."""

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


class _AgenteSequencialLangfuse(BaseAgent):
    """Agente pai ADK determinístico que executa subagentes via run_async."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        for sub_agente in self.sub_agents:
            async for evento in sub_agente.run_async(ctx):
                yield evento


def _carregar_env_local() -> dict[str, str]:
    env = dict(os.environ)
    arquivo_env = REPO_ROOT / ".env"
    if arquivo_env.exists():
        for linha in arquivo_env.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, valor = linha.split("=", 1)
            chave = chave.strip()
            valor = valor.strip().strip('"').strip("'")
            if chave not in env or not env[chave].strip():
                env[chave] = valor
    return env


def test_obs_trc_002_span_de_teste_chega_ao_langfuse_local():
    env = _carregar_env_local()
    faltando = [
        var
        for var in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
        if not env.get(var, "").strip()
    ]
    if not (env.get("LANGFUSE_BASE_URL", "").strip() or env.get("LANGFUSE_HOST", "").strip()):
        faltando.append("LANGFUSE_HOST")

    if faltando:
        pytest.skip(
            f"Bloqueado: credenciais do Langfuse ausentes no .env ({', '.join(faltando)})."
        )

    config = ObservabilityConfig.from_env(
        env,
        timeout=5,
        flush_interval=0.2,
        instrument_adk=True,
    )

    chat_turn_id = f"t004-smoke-{uuid.uuid4().hex[:12]}"
    user_id = "t004-user-local"
    session_id = f"t004-session-{uuid.uuid4().hex[:8]}"

    obs = configure_observability(config=config, set_global_tracer_provider=False)
    try:
        assert obs.langfuse.auth_check() is True, (
            f"Falha na autenticação com o Langfuse local em {config.base_url}"
        )

        with obs.chat_turn(
            chat_turn_id=chat_turn_id,
            user_id=user_id,
            session_id=session_id,
            input={"prompt": "teste de observabilidade T-004"},
            metadata={"tarefa": "T-004"},
        ):
            trace_id = get_current_trace_id()
            with obs.span(
                "E1_smoke_t004",
                observation_type="span",
                input={"texto": "ping"},
                output={"status": "ok"},
                metadata={"etapa": "E1"},
            ):
                pass

        assert trace_id is not None and len(trace_id) == 32
        obs.flush(timeout_millis=10_000)

        observacoes_por_nome: dict[str, object] = {}
        prazo = time.monotonic() + 25.0
        while time.monotonic() < prazo:
            resposta = obs.langfuse.api.observations.get_many(
                trace_id=trace_id,
                limit=20,
            )
            observacoes_por_nome = {item.name: item for item in resposta.data}
            if {"chat_turn", "E1_smoke_t004"}.issubset(observacoes_por_nome):
                break
            time.sleep(0.5)

        assert obs.exporter.dropped_spans == 0
        assert obs.exporter.export_failures == 0
        assert {"chat_turn", "E1_smoke_t004"}.issubset(observacoes_por_nome), (
            f"Spans não chegaram ao Langfuse local para trace_id={trace_id}; "
            f"observações encontradas: {sorted(observacoes_por_nome)}"
        )

        raiz = observacoes_por_nome["chat_turn"]
        assert raiz.user_id == user_id
        assert raiz.session_id == session_id
    finally:
        obs.shutdown()


def test_obs_trc_002_spans_agente_adk_real_chegam_ao_langfuse_local():
    env = _carregar_env_local()
    faltando = [
        var
        for var in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
        if not env.get(var, "").strip()
    ]
    if not (
        env.get("LANGFUSE_BASE_URL", "").strip()
        or env.get("LANGFUSE_HOST", "").strip()
    ):
        faltando.append("LANGFUSE_HOST")

    if faltando:
        pytest.skip(
            f"Bloqueado: credenciais do Langfuse ausentes no .env ({', '.join(faltando)})."
        )

    config = ObservabilityConfig.from_env(
        env,
        timeout=5,
        flush_interval=0.2,
        instrument_adk=True,
    )

    chat_turn_id = f"t004-adk-{uuid.uuid4().hex[:12]}"
    user_id = "t004-adk-user-local"
    session_id = f"t004-adk-session-{uuid.uuid4().hex[:8]}"

    obs = configure_observability(config=config, set_global_tracer_provider=False)
    try:
        assert obs.adk_instrumented is True
        assert obs.langfuse.auth_check() is True, (
            f"Falha na autenticação com o Langfuse local em {config.base_url}"
        )

        async def executar_adk() -> tuple[str | None, list[Event]]:
            agente_raiz = _AgenteSequencialLangfuse(
                name="pipeline_estudo_t004",
                description="Pipeline determinístico ADK para prova no Langfuse",
                sub_agents=[
                    _AgenteEtapaLangfuse(
                        name="etapa_classificar_t004",
                        description="Classifica pedido",
                        resposta="intencao=flashcard",
                    ),
                    _AgenteEtapaLangfuse(
                        name="etapa_gerar_t004",
                        description="Gera flashcard determinístico",
                        resposta="frente: Osmose | verso: Transporte de água",
                    ),
                ],
            )
            sessoes = InMemorySessionService()
            sessao = await sessoes.create_session(
                app_name="training_adk_t004",
                user_id=user_id,
                session_id=session_id,
            )
            runner = Runner(
                app_name="training_adk_t004",
                agent=agente_raiz,
                session_service=sessoes,
            )

            async with obs.chat_turn(
                chat_turn_id=chat_turn_id,
                user_id=user_id,
                session_id=session_id,
                input={"prompt": "Crie flashcards sobre osmose"},
                metadata={"tarefa": "T-004", "prova": "adk_real"},
            ):
                trace_id_local = get_current_trace_id()
                eventos: list[Event] = []
                async for evento in runner.run_async(
                    user_id=user_id,
                    session_id=sessao.id,
                    new_message=types.Content(
                        role="user",
                        parts=[types.Part(text="Crie flashcards sobre osmose")],
                    ),
                ):
                    eventos.append(evento)
                return trace_id_local, eventos

        trace_id, eventos = asyncio.run(executar_adk())
        assert len(eventos) == 2
        assert trace_id is not None and len(trace_id) == 32

        obs.flush(timeout_millis=10_000)

        esperados = {
            "chat_turn",
            "invocation [training_adk_t004]",
            "agent_run [pipeline_estudo_t004]",
            "agent_run [etapa_classificar_t004]",
            "agent_run [etapa_gerar_t004]",
        }
        observacoes_por_nome: dict[str, object] = {}
        prazo = time.monotonic() + 25.0
        while time.monotonic() < prazo:
            resposta = obs.langfuse.api.observations.get_many(
                trace_id=trace_id,
                limit=20,
            )
            observacoes_por_nome = {item.name: item for item in resposta.data}
            if esperados.issubset(observacoes_por_nome):
                break
            time.sleep(0.5)

        assert obs.exporter.dropped_spans == 0
        assert obs.exporter.export_failures == 0
        assert esperados.issubset(observacoes_por_nome), (
            f"Spans do agente ADK não chegaram ao Langfuse local para trace_id={trace_id}; "
            f"observações encontradas: {sorted(observacoes_por_nome)}"
        )

        raiz = observacoes_por_nome["chat_turn"]
        invocacao = observacoes_por_nome["invocation [training_adk_t004]"]
        pipeline = observacoes_por_nome["agent_run [pipeline_estudo_t004]"]
        classificar = observacoes_por_nome["agent_run [etapa_classificar_t004]"]
        gerar = observacoes_por_nome["agent_run [etapa_gerar_t004]"]

        assert raiz.user_id == user_id
        assert raiz.session_id == session_id
        assert invocacao.parent_observation_id == raiz.id
        assert pipeline.parent_observation_id == invocacao.id
        assert classificar.parent_observation_id == pipeline.id
        assert gerar.parent_observation_id == pipeline.id
    finally:
        obs.shutdown()

# Evidência — T-004 (instrumentação real do Google ADK)

Data: 2026-11-24  
Papel: `avaliacao-observabilidade`  
Requisitos: `OBS-TRC-002`, `OBS-CFG-001`

## O que foi corrigido (ressalva da revisão)

A ressalva da revisão apontava que a instrumentação real do ADK não estava provada porque `google-adk` não estava instalado e `tests/test_spans_e_adk.py` usava `_InstrumentorFake` e aceitava qualquer `bool` em `instrument_google_adk`.

### 1. Dependência de desenvolvimento/teste

- Adicionado `google-adk>=2.10,<3` ao grupo `[dependency-groups] dev` de `packages/observability/pyproject.toml` (não entra nas dependências de runtime do pacote).
- Resolvido no `uv.lock` sob a regra de carência de 7 dias (`exclude-newer = "7 days"`, ADR-0007): `google-adk==2.14.1` (e `google-genai==2.34.0`, após o ajuste de restrição em `packages/llm` pelo worker `rag-backend`).

### 2. Prova unitária determinística com agente ADK real (`packages/observability/tests/test_spans_e_adk.py`)

- Removido `_InstrumentorFake` e substituídos os dois testes anteriores por:
  - `test_instrumentacao_adk_real_emite_spans_hierarquia_atributos_e_desinstrumenta`: instancia `configure_observability(..., instrument_adk=True)` (usando o `GoogleADKInstrumentor` real), executa uma hierarquia determinística de subclasses reais de `google.adk.agents.BaseAgent` (`pipeline_estudo` -> `etapa_classificar` e `etapa_gerar`, sem chamadas de rede) via `Runner.run_async` com `InMemorySessionService` dentro de `async with obs.chat_turn(...)`, e verifica:
    - ativação real (`obs.adk_instrumented is True`, `GoogleADKInstrumentor().is_instrumented_by_opentelemetry is True`) e desinstrumentação limpa no `obs.shutdown()` (`is_instrumented_by_opentelemetry is False`);
    - emissão dos 5 spans no mesmo `trace_id`: `chat_turn` (escopo `training_observability`), `invocation [training_app]`, `agent_run [pipeline_estudo]`, `agent_run [etapa_classificar]` e `agent_run [etapa_gerar]` (todos os 4 do ADK com escopo `openinference.instrumentation.google_adk`);
    - hierarquia pai-filho íntegra: `chat_turn` -> `invocation [training_app]` -> `agent_run [pipeline_estudo]` -> `agent_run [etapa_classificar]` e `agent_run [etapa_gerar]`;
    - atributos OpenInference (`openinference.span.kind` `CHAIN`/`AGENT`, `agent.name`, `gen_ai.agent.description`, `user.id`, `session.id`, `input.value`/`output.value` e MIME types `application/json`);
    - aceitação de todos os spans por `should_export_span`.
  - `test_instrument_google_adk_ativa_instrumentor_real_e_trata_ausencia_do_pacote`: valida que `instrument_google_adk(tracer_provider, require_adk=True)` retorna estritamente `True` com `google-adk` instalado e captura spans reais de `Runner.run_async`, além de validar o fallback controlado (`False` sem derrubar a aplicação; `RuntimeError` quando `require_adk=True`) quando `google.adk` está ausente.

### 3. Prova ponta a ponta no Langfuse local (`packages/observability/tests/test_integracao_langfuse.py`)

- Adicionado `test_obs_trc_002_spans_agente_adk_real_chegam_ao_langfuse_local`:
  - executa uma hierarquia real de agentes `BaseAgent` via `runner.run_async` dentro de `async with obs.chat_turn(...)` com `instrument_adk=True` apontando para o Langfuse local (`http://localhost:3000`);
  - força `obs.flush(...)` e consulta `obs.langfuse.api.observations.get_many(trace_id=trace_id)` confirmando a chegada das 5 observações (`chat_turn`, `invocation [training_adk_t004]`, `agent_run [pipeline_estudo_t004]`, `agent_run [etapa_classificar_t004]`, `agent_run [etapa_gerar_t004]`) no mesmo trace, com `parent_observation_id` encadeado corretamente, `user_id`/`session_id` preservados e `dropped_spans == 0` / `export_failures == 0`.

## Validação executada

| Verificação | Comando | Status | Evidência |
| --- | --- | --- | --- |
| Sync do workspace | `uv sync --all-packages` | Passou | `google-adk==2.14.1` resolvido em `uv.lock` |
| Testes do pacote (unitários + integração Langfuse/ADK) | `uv run pytest -v packages/observability` | Passou (`20 passed`, 0 skipped) | `evidencias/T-004/pytest.txt` |
| Validador da governança | `uv run validar-governanca` | Passou | `evidencias/T-004/validador.txt` |

# ADR-0001 — Orquestração com Google ADK e geração com Gemini 3.1 Flash-Lite

Estado: **Aceita** (humano, 2026-11-24)

## Contexto

A pipeline de chat tem agentes especializados (Classificador, Pedagógico, Geração) intercalados com etapas determinísticas (recuperação, validação, sanitização), e todos precisam ser rastreáveis por OpenTelemetry.

## Decisão

- Os agentes são implementados com o **Google ADK** (Python, `google-adk` 2.x). Etapas determinísticas são agentes customizados (`BaseAgent`), sem LLM.
- Classificador, Pedagógico e Geração usam **`gemini-3.1-flash-lite`** via Google AI Studio.
- O runner é chamado com `run_async`, porque `runner.run()` perde o contexto OpenTelemetry.
- Toda saída de agente é estruturada (`output_schema` Pydantic).

## Alternativas consideradas

- Um "3.1 Flash" de texto: **não existe**. A página oficial de modelos lista, na família 3.1, só o `gemini-3.1-flash-lite` como modelo de texto; os outros são variantes de imagem, live e TTS.
- `gemini-3.6-flash` / `gemini-3.8-flash`: mais capazes e mais caros; ficam como opção se o RAGAS mostrar qualidade insuficiente.

## Consequências

- O nome do modelo vem da configuração; trocá-lo não exige mudança de código, mas exige rodar o RAGAS offline (ADR-0006).
- O juiz de saída não pode ser da família Gemini (ADR-0004).

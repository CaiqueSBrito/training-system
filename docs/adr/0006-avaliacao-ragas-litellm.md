# ADR-0006 — Avaliação com RAGAS e juízes via LiteLLM

Estado: **Aceita** (humano, 2026-11-24)

## Decisão

- **RAGAS** (0.4.x) para a avaliação offline (CI e mudanças de modelo, prompt, chunker ou recuperação) e online, por amostragem assíncrona.
- Métricas iniciais:
  - recuperação: ContextPrecision e ContextRecall;
  - item: Faithfulness, com `user_input` = frente/enunciado, `response` = verso/alternativa correta + explicação e `retrieved_contexts` = os chunks **citados**.
- O juiz do RAGAS é chamado via **LiteLLM** (`llm_factory(..., adapter="litellm")`); o modelo inicial é **`openai/gpt-6.1-sol`** via OpenRouter. As variantes `:batch` servem para as rodadas offline.
- O LiteLLM é a camada de acesso a modelos fora do ADK: `gemini/<modelo>` vai para o AI Studio e `openrouter/<provedor>/<modelo>` vai para o OpenRouter.
- Sem `EVAL_JUDGE_MODEL` configurado, a avaliação não inicia; não existe juiz padrão.

## Dataset e limiares

- O dataset de referência fica em `evals/datasets/`, com documentos reais e os fatos esperados. Pode ser semeado pela geração de testset do RAGAS, mas só entra depois de revisão humana.
- A primeira execução define a linha de base. O humano aprova os limiares, e uma queda abaixo deles bloqueia o merge e vira caso de regressão.

## Segurança da cadeia de suprimentos

Em 24/03/2026, as versões `litellm` 1.82.7 e 1.82.8 foram publicadas no PyPI com código malicioso (campanha TeamPCP). Por isso o workspace usa um lockfile com hashes e uma carência de 7 dias para versões novas (ADR-0007).

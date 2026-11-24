# Documentação

| Documento | Conteúdo |
| --- | --- |
| [arquitetura/pipeline-rag.md](arquitetura/pipeline-rag.md) | fluxo do chat (E1–E12), ingestão, guardrails, modelos, rastreabilidade, observabilidade, avaliação, decisões em aberto |
| [../governanca/pipeline.md](../governanca/pipeline.md) | como o trabalho anda: trilhas, etapas, gates do humano, execução multi-provedor |

## Decisões (ADR)

| ADR | Decisão | Estado |
| --- | --- | --- |
| [0001](adr/0001-orquestracao-adk-geracao-gemini.md) | Google ADK + `gemini-3.1-flash-lite` | Aceita |
| [0002](adr/0002-embeddings-qdrant-hibrido.md) | `gemini-embedding-2` @ 3072 + Qdrant híbrido | Aceita |
| [0003](adr/0003-reranker-cross-encoder-local.md) | Reranker `bge-reranker-v2-m3` local | Aceita |
| [0004](adr/0004-guardrails-e-juizes.md) | Guardrails e modelos juízes | Aceita (juiz de saída a calibrar) |
| [0005](adr/0005-observabilidade-langfuse-otel.md) | Langfuse via OpenTelemetry | Aceita |
| [0006](adr/0006-avaliacao-ragas-litellm.md) | RAGAS + juízes via LiteLLM | Aceita |
| [0007](adr/0007-monorepo-python-uv.md) | uv workspace, Python 3.13, carência de 7 dias | Aceita |

Nova ADR: próximo número livre, com as seções Estado, Contexto, Decisão, Alternativas e Consequências. Uma ADR aceita não é editada; uma mudança de decisão gera uma ADR nova que a substitui.

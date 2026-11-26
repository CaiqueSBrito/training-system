# Documentação

| Documento | Conteúdo |
| --- | --- |
| [arquitetura/pipeline-rag.md](arquitetura/pipeline-rag.md) | fluxo do chat (E1–E12), ingestão, guardrails, modelos, rastreabilidade, observabilidade, avaliação, aplicações e stacks, decisões em aberto |
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
| [0008](adr/0008-linhagem-postgres.md) | Linhagem num Postgres da aplicação, separado do Langfuse | Aceita |
| [0009](adr/0009-parser-docling.md) | Docling como parser (PDF, DOCX e Markdown) | Aceita |
| [0010](adr/0010-monolito-modular.md) | Monólito modular: regra nos pacotes, apps como adaptadores finos | Aceita |
| [0011](adr/0011-api-fastapi-sse.md) | API com FastAPI + Uvicorn, progresso do chat por SSE | Aceita |
| [0012](adr/0012-fila-procrastinate-postgres.md) | Fila de jobs com Procrastinate no Postgres da aplicação | Aceita |
| [0013](adr/0013-front-nextjs-typescript.md) | Front com Next.js, React e TypeScript, tipos do OpenAPI | Aceita |
| [0014](adr/0014-contratos-openapi.md) | Contratos publicados pelo OpenAPI (substitui o JSON Schema exportado) | Aceita |
| [0015](adr/0015-canonico-decisoes-indice.md) | Texto canônico, decisões imutáveis e índice derivado reparável | Aprovada para implementação; aceite integral pendente |
| [0016](adr/0016-prompts-versionados-offline-chat.md) | Prompts reais versionados offline enquanto Langfuse está desligado | Decisão sob autonomia; revisão pendente |

Nova ADR: próximo número livre, com as seções Estado, Contexto, Decisão, Alternativas e Consequências. Uma ADR aceita não é editada; uma mudança de decisão gera uma ADR nova que a substitui.

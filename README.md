# Training System

Geração de flashcards e questionários, via chat, a partir do material de estudo do usuário, usando RAG. Todo item cita o trecho da fonte que o sustenta, toda mensagem vira um trace no Langfuse e a qualidade é medida com RAGAS.

## Estado

Fundação: arquitetura, ADRs, governança e workspace com o `litellm`; ainda não há código de aplicação. Agentes (humanos ou IA) começam por [`AGENTS.md`](AGENTS.md).

## Stack

| Peça | Escolha | Decisão |
| --- | --- | --- |
| Agentes | Google ADK (Python) | [ADR-0001](docs/adr/0001-orquestracao-adk-geracao-gemini.md) |
| Geração | `gemini-3.1-flash-lite` (AI Studio) | [ADR-0001](docs/adr/0001-orquestracao-adk-geracao-gemini.md) |
| Embeddings | `gemini-embedding-2`, 3072 dimensões | [ADR-0002](docs/adr/0002-embeddings-qdrant-hibrido.md) |
| Banco vetorial | Qdrant: dense + BM25 em português, fusão RRF | [ADR-0002](docs/adr/0002-embeddings-qdrant-hibrido.md) |
| Reranker | `BAAI/bge-reranker-v2-m3`, local | [ADR-0003](docs/adr/0003-reranker-cross-encoder-local.md) |
| Guardrails e juízes | entrada: Gemini Flash-Lite · saída: Claude Haiku 5.5 (a calibrar) | [ADR-0004](docs/adr/0004-guardrails-e-juizes.md) |
| Observabilidade | Langfuse via OpenTelemetry | [ADR-0005](docs/adr/0005-observabilidade-langfuse-otel.md) |
| Avaliação | RAGAS, com juiz `openai/gpt-6.1-sol` via LiteLLM/OpenRouter | [ADR-0006](docs/adr/0006-avaliacao-ragas-litellm.md) |
| Monorepo | uv workspace, Python 3.13, carência de 7 dias | [ADR-0007](docs/adr/0007-monorepo-python-uv.md) |

## Estrutura

```text
AGENTS.md         regras para agentes (canônico; CLAUDE.md importa, .omp/RULES.md repete o essencial)
governanca/       pipeline, papéis, specs vivas, mudanças e tarefas, progresso
.agents/skills/   procedimentos compartilhados (abrir e arquivar mudança)
packages/llm/     acesso a modelos via LiteLLM (gemini/, openrouter/)
tools/governanca/ validador da governança (uv run validar-governanca)
docs/             arquitetura e ADRs — comece por docs/README.md
```

Os demais pacotes (`contracts`, `ingestion`, `retrieval`, `generation`, `observability`, `evals`) e apps (`api`, `worker`, `web`) entram à medida que forem implementados.

## Ambiente

Requer [uv](https://docs.astral.sh/uv/).

```sh
uv sync --all-packages
cp .env.example .env   # preencha as chaves
```

```sh
uv run pytest               # testes
uv run validar-governanca   # confere governanca/ (precisa passar antes de commit)
```

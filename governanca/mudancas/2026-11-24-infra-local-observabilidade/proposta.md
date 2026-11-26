---
id: 2026-11-24-infra-local-observabilidade
titulo: Infraestrutura local e observabilidade base
trilha: padrao
estado: aprovada
aprovacao: { por: humano, em: 2026-11-24, palavras: "aprovo as duas propostas" }
---

## Por quê

Nenhum serviço que a arquitetura usa existe ainda: o Qdrant para a busca, o Postgres da aplicação para a linhagem e o Langfuse para os traces e prompts. Sem eles, a ingestão e o chat não têm onde gravar nem como ser observados. Esta mudança destrava todas as outras.

## O que muda

1. **ADR-0008** (papel `arquiteto`): a linhagem fica num Postgres da aplicação, num banco separado do Postgres do Langfuse. Decisão do humano em 2026-11-24.
2. **`infra/`** (papel `avaliacao-observabilidade`): `docker compose` local com versões fixas e healthchecks para:
   - Qdrant v1.20.x (BM25 nativo do servidor);
   - Postgres da aplicação;
   - Langfuse self-hosted e as dependências que ele exige (web, worker, Postgres próprio, ClickHouse, Redis/Valkey e MinIO).

   Tudo sobe com um comando e é configurado só pelo `.env`, sem segredo versionado.
3. **`packages/observability`** (papel `avaliacao-observabilidade`): configuração do OpenTelemetry com exportação para o Langfuse, instrumentação do ADK (`openinference-instrumentation-google-adk`), utilitário para spans manuais e exportação assíncrona que não derruba a aplicação quando o Langfuse cai (OBS-TRC-002).
4. Requisitos novos no delta, área `OBS`: a infraestrutura sobe e fica saudável com um comando, e a configuração de observabilidade falha com mensagem clara quando falta credencial.

Tarefas previstas após o G1: ADR-0008 (`arquiteto`, claude; revisor google), `infra/` e `packages/observability` (`avaliacao-observabilidade`, antigravity; revisor openai).

## Fora do escopo

- Tabelas da linhagem: ficam na mudança da ingestão (documento e chunk) e na do E12 (registros do chat).
- Ingestão, chat, dataset de avaliação.
- CI: GitHub Actions foi escolhido pelo humano, mas depende de um remoto; vira mudança própria.
- Deploy fora da máquina local.

## Riscos

- O Langfuse self-hosted é pesado (6 containers). Mitigação: perfis do `docker compose` para subir só o necessário.
- **Dependências novas** (aprovadas junto com o G1): imagens Docker do Qdrant, do Postgres e do Langfuse com as dependências dele; pacotes `langfuse`, `opentelemetry-sdk` e `openinference-instrumentation-google-adk` em `packages/observability`. Todos passam pela carência de 7 dias.

## Esclarecido

- Linhagem em Postgres da aplicação (humano, 2026-11-24).
- CI em GitHub Actions, numa mudança futura (humano, 2026-11-24).

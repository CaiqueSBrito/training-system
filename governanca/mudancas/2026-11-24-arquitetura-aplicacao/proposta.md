---
id: 2026-11-24-arquitetura-aplicacao
titulo: Arquitetura de aplicação e stacks de back e front
trilha: padrao
estado: aprovada
aprovacao: { por: humano, em: 2026-11-24, palavras: "aprovo a proposta de arquitetura, Procrastinate, autenticação depois" }
---

## Por quê

As ADRs 0001–0009 fecharam o pipeline de RAG e a infraestrutura. O formato das aplicações continua sem decisão: a arquitetura (§10) reserva `apps/api`, `apps/worker` e `apps/web`, mas nada define:
- o estilo arquitetural;
- o framework HTTP;
- como o progresso do chat chega ao usuário;
- a fila de jobs;
- a stack do front;
- como o front conhece os contratos.

Sem essas decisões, as próximas mudanças (recuperação, E1–E12, API) começariam por suposição.

## O que muda

Decisões registradas como ADRs (papel `arquiteto`) e refletidas na arquitetura §10–§11:

1. **ADR-0010 — Monólito modular.** Os pacotes de domínio de hoje (`contracts`, `llm`, `lineage`, `ingestion`, `observability` e, depois, `retrieval` e `generation`) concentram a lógica. `apps/api` e `apps/worker` são adaptadores finos sobre eles, sem regra de negócio própria. Um deploy, um banco da aplicação, sem microserviços. Dependência sempre de app para pacote, nunca o contrário.
2. **ADR-0011 — Back HTTP com FastAPI**, assíncrono, servido por Uvicorn.
   - Valida com os modelos Pydantic de `packages/contracts` e chama o ADK com `run_async`.
   - O progresso do chat por etapa (arquitetura §10) vai por **SSE**. Os itens chegam inteiros no evento final, depois do E10.
3. **ADR-0012 — Worker com fila sobre o Postgres da aplicação**, sem serviço novo.
   - Atende a ingestão assíncrona e a avaliação online por amostragem.
   - Jobs duráveis e auditáveis no mesmo banco da linhagem.
4. **ADR-0013 — Front com Next.js, React e TypeScript.**
   - Tipos gerados do **OpenAPI** publicado pela API.
   - Conteúdo gerado sempre renderizado escapado (RAG-SAI-001).
5. **ADR-0014 — Contratos expostos pelo OpenAPI da API.**
   - Deixa de existir o JSON Schema exportado à parte (`packages/contracts/schema/`, `exportar-schema-contratos`, `test_schema.py`).
   - Quando o JSON Schema for necessário em tempo de execução (saída estruturada de modelo), ele vem de `model_json_schema()`.
   - Substitui a decisão do G1 de `2026-11-24-spec-contratos-chat`, que versionava os contratos pelo JSON exportado.
   - A remoção dos arquivos acontece na mudança que criar `apps/api`, junto com o teste que confere os contratos no OpenAPI. Até lá, os arquivos ficam, para os contratos não perderem a guarda de mudança.

Tarefas previstas após o G1: as cinco ADRs e a atualização da arquitetura (`arquiteto`, claude; revisor google).

## Fora do escopo

- Implementar `apps/api`, `apps/worker` ou `apps/web`: cada um vira mudança própria.
- Remover o JSON Schema exportado (item 5): fica para a mudança da `apps/api`.
- Deploy e hospedagem.

## Riscos

- Fila sobre Postgres tem vazão menor que um broker dedicado. Aceitável para ingestão e avaliação por amostragem; a ADR registra o limite e quando revisar.
- SSE em proxies exige desligar o buffering. A ADR deve registrar isso.
- **Dependências novas**: nenhuma nesta mudança. As ADRs só nomeiam as bibliotecas (FastAPI, Uvicorn, Procrastinate, Next.js), que entram na mudança de cada app.

## Esclarecido (humano, G1, 2026-11-24)

- Fila: **Procrastinate** (ADR-0012).
- Autenticação: decidida na mudança da `apps/api`.

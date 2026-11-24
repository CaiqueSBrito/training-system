# ADR-0005 — Observabilidade com Langfuse via OpenTelemetry

Estado: **Aceita** (humano, 2026-11-24)

## Decisão

- **Langfuse** self-hosted. Componentes exigidos pelo Langfuse: web, worker, PostgreSQL, ClickHouse, Redis/Valkey e armazenamento S3/blob.
- Spans do ADK via **`openinference-instrumentation-google-adk`** (`GoogleADKInstrumentor`) → OpenTelemetry → Langfuse; etapas determinísticas com spans manuais.
- Um trace por mensagem do chat (`chat_turn`), com um span por etapa E1–E12; `user_id` e `session_id` preenchidos.
- Prompts (classificador, pedagógico, geração, juízes, rubrica) ficam na gestão de prompts do Langfuse, com labels; cada span registra a versão do prompt usada.
- Scores no trace: vereditos dos juízes, taxas de aprovação, RAGAS por amostragem e feedback do usuário.

## Consequências

- A exportação OTel é assíncrona: o Langfuse fora do ar não derruba o chat.
- A linhagem de negócio (item → chunk → documento) **não** depende do Langfuse; ela é gravada pela aplicação (arquitetura §7), e o trace só a referencia pelo `trace_id`.
- O mascaramento de conteúdo nos traces em produção está em aberto.

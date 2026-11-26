# T-002 — inspeção da ADR-0008 contra OBS-INF-002

Data: 2026-11-24 · Papel: arquiteto (anthropic)

| Verificação | Onde na ADR-0008 | Status |
| --- | --- | --- |
| Linhagem num Postgres da aplicação | Decisão, item 1 | Passou |
| Separado do Postgres do Langfuse | Decisão, item 1; Alternativas, linha 1 | Passou |
| Bancos e credenciais diferentes (cenário de OBS-INF-002) | Decisão, item 2 | Passou |
| Estado "Aceita (humano, 2026-11-24)" | cabeçalho | Passou |
| Seções Contexto, Decisão, Alternativas consideradas, Consequências | corpo | Passou |
| §11 item 1 marcado como resolvido, apontando para a ADR-0008 | `docs/arquitetura/pipeline-rag.md` §11 | Passou |
| Linha no índice | `docs/README.md` | Passou |
| Cenário de OBS-INF-002 em infraestrutura no ar (inspeção das conexões) | — | Não se aplica: a T-002 só documenta; a prova em execução depende do `infra/` (outra tarefa) |
| Validador | `validador.txt` | Passou (exit 0) |
| Casos de teste (G3) | — | Não se aplica: tarefa só de documentação |

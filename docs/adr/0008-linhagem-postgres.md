# ADR-0008 — Linhagem num Postgres da aplicação, separado do Langfuse

Estado: **Aceita** (humano, 2026-11-24). Resolve o item 1 da arquitetura §11.

## Contexto

A arquitetura §7 define a linhagem de negócio: `document_version`, `chunk`, `chat_turn`, `search_plan`, `retrieval_run`, `item`, `citation` e `verdict`, navegável nos dois sentidos (item → citação → chunk → página do documento, e item → `trace_id` → trace no Langfuse). Faltava decidir onde gravá-la; sem isso, a ingestão não tem onde registrar documento e chunk e o E12 não tem onde persistir o turno do chat.

Restrições já decididas:

- A linhagem **não** depende do Langfuse; o trace só a referencia pelo `trace_id` (ADR-0005, Consequências). O Langfuse pode cair sem derrubar o chat, e a linhagem não pode cair junto com ele.
- O Qdrant guarda vetor e payload de filtro; a linhagem completa fica fora dele (ADR-0002, Consequências).
- Os registros são relacionais por natureza: chaves estrangeiras entre item, citação, chunk e documento, relações N:N entre `retrieval_run` e `chunk`, e o `chunk` é imutável.
- O Langfuse self-hosted já exige um PostgreSQL próprio (ADR-0005), então Postgres já faz parte da operação local.

## Decisão

- A linhagem da §7 fica num **Postgres da aplicação**, um serviço próprio, separado do Postgres que o Langfuse usa.
- A aplicação e o Langfuse usam **bancos e credenciais diferentes**; nenhum dos dois acessa o banco do outro (OBS-INF-002).
- A ligação entre linhagem e trace é só por identificador: `chat_turn` guarda o `trace_id`, e o trace leva o `chat_turn_id` nos metadados (§7).
- O esquema das tabelas não é decidido aqui: documento e chunk entram na mudança da ingestão; os registros do chat, na mudança do E12.

## Alternativas consideradas

| Opção | Por que não |
| --- | --- |
| Reusar o Postgres do Langfuse (mesma instância ou mesmo banco) | acopla a linhagem ao ciclo de vida do Langfuse (atualizações, migrações e quedas dele); as credenciais seriam compartilhadas; contraria a ADR-0005, que separa a linhagem do Langfuse |
| Gravar a linhagem no próprio Langfuse (metadados de trace e spans) | traces não são registro de negócio: sem integridade referencial, sujeitos a amostragem e retenção, e a exportação é assíncrona e pode perder dados quando o Langfuse cai |
| Payload do Qdrant | o Qdrant não faz junções nem garante integridade entre registros; só o chunk tem ponto no Qdrant, os demais registros (turno, plano, item, veredito) não |
| SQLite em arquivo | atende localmente, mas a API e o worker (`apps/api`, `apps/worker`) escrevem em paralelo, e o SQLite serializa escritas; trocar de banco depois exigiria migrar a linhagem |

## Consequências

- Mais um serviço na infraestrutura local: o `docker compose` de `infra/` sobe o Postgres da aplicação ao lado do Qdrant e do Langfuse (OBS-INF-001), com credenciais próprias no `.env`.
- O driver Postgres e a ferramenta de migração são dependências novas e passam pela aprovação do humano e pela carência de 7 dias (ADR-0007) quando o `packages/lineage` for implementado.
- Backup e retenção da linhagem são independentes dos do Langfuse; apagar ou recriar o Langfuse não perde linhagem.
- A ADR-0002 menciona a linhagem como "decisão em aberto"; ela continua aceita e sem edição, e esta ADR é a resposta.

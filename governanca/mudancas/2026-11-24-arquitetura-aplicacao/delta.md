# Delta — Arquitetura de aplicação e stacks de back e front

Spec afetada: `governanca/specs/aplicacao/spec.md` (criada por esta mudança).

## ADICIONA

### ARQ-APP-001 — Dependência só de app para pacote
O sistema DEVE manter a regra de negócio nos pacotes de domínio (`packages/*`), com `apps/*` dependendo dos pacotes e nenhum pacote importando uma app. (ADR-0010)

- Cenário: Dado o código do workspace, Quando os imports são analisados, Então nenhum módulo de `packages/*` importa `apps/*`.

### ARQ-API-001 — Progresso do chat por SSE
QUANDO a API processar uma mensagem do chat, ela DEVE enviar por SSE um evento de progresso por etapa executada e um evento final com a resposta completa (`ChatResponse`). (ADR-0011; arquitetura §10)

- Cenário: Dada uma mensagem que termina em E12, Quando o cliente acompanha o stream, Então recebe eventos de progresso em ordem e um único evento final com os itens e as fontes.

### ARQ-CTR-001 — Contratos no OpenAPI
A API DEVE publicar no seu OpenAPI os modelos de `packages/contracts` que cruzam a fronteira HTTP, gerados dos próprios modelos Pydantic. (ADR-0014)

- Cenário: Dado o OpenAPI da API, Quando comparado aos modelos de `packages/contracts` usados nos endpoints, Então cada um aparece com o mesmo schema de `model_json_schema()`.

### ARQ-JOB-001 — Jobs duráveis no Postgres
O sistema DEVE executar a ingestão assíncrona e a avaliação online como jobs Procrastinate persistidos no Postgres da aplicação. (ADR-0012)

- Cenário: Dado um job de ingestão enfileirado, Quando o worker é reiniciado antes de processá-lo, Então o job continua na fila e é processado após o reinício.

## MODIFICA

## REMOVE

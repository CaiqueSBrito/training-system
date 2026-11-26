# T-009 — inspeção das ADRs 0010–0014 contra os requisitos ARQ

Data: 2026-11-24 · Papel: arquiteto (anthropic)

## Por ADR

| Verificação | Onde | Status |
| --- | --- | --- |
| ADR-0010: monólito modular, apps como adaptadores finos, um deploy e um banco | 0010, Decisão, itens 1 e 3 | Passou |
| ADR-0010 × **ARQ-APP-001**: dependência só de app para pacote, nenhum `packages/*` importa `apps/*` | 0010, Decisão, item 2; Consequências (verificação por análise de imports) | Passou |
| ADR-0011: FastAPI assíncrono + Uvicorn, validação por `packages/contracts`, ADK com `run_async` | 0011, Decisão, itens 1 e 2 | Passou |
| ADR-0011 × **ARQ-API-001**: um evento de progresso por etapa, em ordem, e um único evento final com a `ChatResponse` completa | 0011, Decisão, item 3 | Passou |
| ADR-0011: buffering de proxy desligado (`X-Accel-Buffering: no`, proxy sem buffering, timeout) | 0011, Decisão, item 4; Consequências | Passou |
| ADR-0011: itens só no evento final, depois do E10 (sem streaming de conteúdo) | 0011, Decisão, itens 3 e 5 | Passou |
| ADR-0012 × **ARQ-JOB-001**: Procrastinate no Postgres da aplicação, ingestão e avaliação online como jobs duráveis | 0012, Decisão, itens 1 e 2 | Passou |
| ADR-0012: limite de vazão registrado | 0012, Decisão, item 5 | Passou |
| ADR-0012: critério para revisar a decisão | 0012, Decisão, item 6 (três gatilhos medidos) | Passou |
| ADR-0013: Next.js + React + TypeScript, tipos gerados do OpenAPI | 0013, Decisão, itens 1 e 2 | Passou |
| ADR-0013 × RAG-SAI-001: conteúdo gerado sempre renderizado escapado | 0013, Decisão, item 3 | Passou |
| ADR-0014 × **ARQ-CTR-001**: OpenAPI publica os modelos que cruzam a fronteira HTTP com o mesmo schema de `model_json_schema()` | 0014, Decisão, itens 1 e 4 | Passou |
| ADR-0014: substitui o JSON Schema exportado e a decisão do G1 de `2026-11-24-spec-contratos-chat` | 0014, cabeçalho; Decisão, item 2 | Passou |
| ADR-0014: remoção dos arquivos fica para a mudança da `apps/api`; até lá, continuam | 0014, Decisão, item 5; Consequências | Passou |
| Nenhuma dependência nova adotada: as ADRs só nomeiam bibliotecas, que entram na mudança de cada app | Consequências de 0011, 0012 e 0013 | Passou |

## Formato (todas)

| Verificação | Status |
| --- | --- |
| Estado "Aceita (humano, 2026-11-24)" nas cinco | Passou |
| Seções Contexto, Decisão, Alternativas consideradas, Consequências nas cinco | Passou |
| Números 0010–0014 livres e sequenciais após a 0009 | Passou |

## Arquitetura e índice

| Verificação | Onde | Status |
| --- | --- | --- |
| §10 com as três apps e as stacks (tabela e diagrama), latência mantida em §10.2 | `docs/arquitetura/pipeline-rag.md` §10.1 e §10.2 | Passou |
| §11: decisões resolvidas (item 10) e autenticação pendente para a `apps/api` (item 11) | `docs/arquitetura/pipeline-rag.md` §11 | Passou |
| Linhas 0010–0014 no índice | `docs/README.md` | Passou |
| Cenários de ARQ-APP-001, ARQ-API-001, ARQ-CTR-001, ARQ-JOB-001 em execução | — | Não se aplica: a T-009 só documenta; a prova em execução é das mudanças de cada app |
| Casos de teste (G3) | — | Não se aplica: tarefa só de documentação |
| Validador | `validador.txt` | Passou (exit 0) |

## Observações fora do escopo

- O cabeçalho de `docs/arquitetura/pipeline-rag.md` (linha "Decisões:") lista só as ADRs 0001–0007; faltam 0008–0014. A T-009 só pode editar §10 e §11; fica para o orquestrador decidir.
- O título da §10 passou de "Latência no chat" para "Aplicações e latência no chat"; referências a "§10" na spec (`RAG-JUI-003`, ARQ-API-001) continuam corretas.
- Spec viva `governanca/specs/aplicacao/spec.md` ainda não existe; é criada no arquivamento da mudança.

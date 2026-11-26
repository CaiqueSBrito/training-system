# ADR-0012 — Fila de jobs com Procrastinate sobre o Postgres da aplicação

Estado: **Aceita** (humano, 2026-11-24)

## Contexto

Dois fluxos não cabem numa requisição HTTP:

- a **ingestão assíncrona** (parse com Docling, chunking, verificação de injeção, embeddings, Qdrant; arquitetura §3), que leva de segundos a minutos por documento e carrega modelos pesados;
- a **avaliação online por amostragem** (RAGAS e juízes sobre turnos já entregues; arquitetura §9), que não pode atrasar a resposta do chat.

Eles rodam na `apps/worker`, adaptador fino sobre os pacotes de domínio (ADR-0010). Falta decidir a fila.

Restrições:

- Os jobs precisam ser duráveis (sobreviver a reinício do worker) e auditáveis ao lado da linhagem que eles produzem (ADR-0008).
- O Postgres da aplicação já existe (ADR-0008, OBS-INF-001) e o `packages/lineage` já usa `psycopg` 3. Um broker novo seria mais um serviço para operar, monitorar e proteger.
- O volume previsto é baixo: documentos enviados por um usuário e uma fração amostrada dos turnos.

## Decisão

1. **Procrastinate** como fila de jobs, com as tabelas da fila no **Postgres da aplicação**, no mesmo banco da linhagem e sem serviço novo (ARQ-JOB-001).
2. A `apps/worker` executa os jobs de **ingestão** e de **avaliação online**; a `apps/api` só enfileira. Cada job chama uma função de pacote (ADR-0010).
3. Fila e linhagem no mesmo banco permitem conferir um contra o outro: o registro de negócio nasce em estado pendente (ex.: `document_version` aguardando ingestão) e um registro pendente sem job correspondente é detectável e reenfileirável. Se o enfileiramento entra na mesma transação do registro é detalhe de implementação da mudança da `apps/worker`.
4. Jobs com tentativas limitadas e reprocessamento idempotente: reprocessar um documento não duplica chunk nem ponto no Qdrant (a idempotência é da ingestão, não da fila).
5. **Limite de vazão aceito**: uma fila sobre Postgres tem vazão menor que a de um broker dedicado e disputa o mesmo banco que atende a API e a linhagem (cada worker ocupa conexões e cada job gera escrita). Para o volume previsto — documentos de um usuário e uma fração amostrada dos turnos — o limite fica longe; não há número medido ainda.
6. **Critério para revisar a decisão** (gera uma ADR nova que substitui esta): qualquer um destes, medido e não estimado:
   - o atraso típico entre enfileirar e iniciar um job passa de 1 minuto com o worker ocioso de CPU;
   - a carga da fila (conexões, bloqueios, tabelas de job) degrada a latência da API ou da gravação da linhagem;
   - surge um fluxo de alto volume (ex.: avaliação de todos os turnos, ingestão em lote de muitos usuários) que exija vazão sustentada acima do que o Postgres entrega sem afetar a aplicação.

## Alternativas consideradas

| Opção | Por que não |
| --- | --- |
| Celery + Redis/RabbitMQ | serviço novo de broker na operação; jobs fora do banco da linhagem, sem transação conjunta; durabilidade depende da configuração do broker |
| RQ / arq / Dramatiq com Redis | o mesmo custo de um serviço novo; o Redis do Langfuse não pode ser reaproveitado, pela separação de credenciais (OBS-INF-002) |
| Fila própria com `SELECT … FOR UPDATE SKIP LOCKED` | reimplementar o que a Procrastinate já resolve (tentativas, agendamento, bloqueios, worker assíncrono) e manter esse código |
| Tarefas em segundo plano do próprio FastAPI | não são duráveis: reiniciar a API perde o job; disputa CPU com o chat |
| Fila gerenciada de nuvem (SQS, Pub/Sub) | prende a um provedor antes de decidir deploy e hospedagem (fora do escopo); não roda local sem emulador |

## Consequências

- Procrastinate é dependência nova e entra com a mudança da `apps/worker` (ou da primeira mudança que enfileirar), com aprovação do humano e a carência de 7 dias (ADR-0007). As migrações da fila convivem com as da linhagem no mesmo banco.
- Os jobs aparecem como registros consultáveis no banco: o estado de uma ingestão é visível sem ferramenta extra.
- O pool de conexões do Postgres passa a ser compartilhado entre API, worker e fila; o tamanho do pool entra na configuração das apps.
- Backup do banco da aplicação cobre linhagem e fila juntos.

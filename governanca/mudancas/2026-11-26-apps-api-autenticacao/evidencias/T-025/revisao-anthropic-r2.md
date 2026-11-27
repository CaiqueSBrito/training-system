# T-025 — revisão anthropic, rodada 2 (somente leitura)

- Revisor: anthropic (Claude Code, worker Orca `task_a3b930191cd1`, dispatch `ctx_06b26c5566b2`).
- Objeto: commit `512c159`, que traz as rodadas R3 (`f34b4d8`) e R4 (decisões humanas A4/I2/A11). A base é o que revisei em `47270ee` (`revisao-anthropic.md`).
- Lido: `relatorio-r3.txt`, `relatorio-r4.txt`, `alteracoes-casos-r4.json`, `delta.md` (MODIFICA RAG-SAI-001), o diff `47270ee..512c159` de `apps/api/src/training_api/{app,auth,chat_body,chat_execution,chat_routes,chat_runtime,config}.py`, `packages/contracts/src/training_contracts/rendering.py` e `packages/generation/src/training_generation/chat.py`, e o diff `f34b4d8..512c159` dos quatro arquivos de teste antigos.
- Execução (uma vez, chaves LLM vazias no subprocesso): `uv run pytest apps/api/tests/test_api_chat.py apps/api/tests/test_api_chat_hardening.py apps/api/tests/test_api_plaintext.py packages/generation/tests/test_geracao_validacao.py packages/generation/tests/test_chat_integration.py::test_escape_original_source_and_reject_altered_response -q` → **Passou**, 69 passed em 38.05s. Suíte inteira e APIs reais: **Não executado**.
- `sha256sum -c evidencias/T-025/casos-intactos-r4.txt` → **Passou** nos 9 arquivos.

## Veredito

**Aprovado.** M1 e M2 estão resolvidos. E1–E12 continuam iguais, exceto o E11, e a mudança do E11 segue a decisão humana A4. As seis linhas de teste alteradas são exatamente as declaradas e não afrouxam nada além da decisão. A execução fora do event loop está correta para os cenários cobertos. Nenhum achado bloqueia. Ficam um ponto de documentação para o orquestrador (D1) e notas.

## M1 — Resolvido

- O `count` passa a ser limitado à configuração com `min(5, max_items)` (`chat_execution.py:148`). O `session_id` só com espaços ou longo demais recebe 422 antes do stream (`chat_routes.py:47-48`).
- Qualquer exceção do domínio no worker vira `error_response()`: o log registra só a categoria, sem a mensagem (`chat_execution.py:154-156`). Se o worker falhar no início, sai um `final` sanitizado (`chat_routes.py:79-83`). Se falhar ao aguardar, também sai `final` (`:126-130`). Todo caminho que entra no stream termina com exatamente um `frame('final', …)` (`:142`).
- O ID só é anunciado depois de confirmar o commit por conexão independente (`chat_execution.py:71-77,124-126,149-150`). Sem confirmação, o `final` sai com `chat_turn_id`/`trace_id` nulos, sem itens (`:127-130`). Assim nenhum ID inexistente é anunciado.

## M2 — Resolvido

- Há agora um canal separado, `stage_progress` (`chat.py` `__init__`, e `run` com `_EXECUTION_PROGRESS.set(self.stage_progress)`). O `progress` da CLI volta a receber só o agregado `E5_E7`, como antes da T-025. A CLI não passa `stage_progress` (`cli.py:73-74`).
- A API liga os dois canais na factory (`chat_runtime.py:50`) e de novo na rota (`chat_routes.py:63`), o que é redundante mas inofensivo.

## E1–E12 — iguais, exceto E11 conforme A4

- A comparação AST de todas as funções e métodos de `chat.py` entre `47270ee` e `512c159` mostra diferença só em `ChatPipeline.__init__` (novo atributo `stage_progress`), `ChatPipeline.run` (a `ContextVar` usa `stage_progress`) e `ChatPipeline._generate`. Em `_generate`, a variável `safe` virou `sanitized` e ganhou um comentário (`chat.py:593-595`), sem mudança de comportamento. `_run`, que é o corpo de E1–E12, é idêntico.
- O efeito real do E11 está em `packages/contracts/src/training_contracts/rendering.py:10-22`: sai só o `html.escape`, e o filtro de Cf, Cs e Cc (exceto `\n` e `\t`) continua byte a byte igual, com a mesma revalidação `validar_item`. Não há `unescape` nem normalização nova. Isso bate com a MODIFICA RAG-SAI-001 do `delta.md`: texto puro sem escape, controles removidos, schema validado e cliente com `textContent`.
- A conferência de entrega no E12, `ChatLineageRepository.save_turn` (`packages/lineage/src/training_lineage/chat.py:93-95`), usa o mesmo `render_item`, então resposta e item persistido continuam iguais por construção.

## Seis linhas de teste — só as autorizadas

O diff `f34b4d8..512c159` confere exatamente com `alteracoes-casos-r4.json`:

| Arquivo:linha | Antes → depois | Avaliação |
| --- | --- | --- |
| `apps/api/tests/api_auth_support.py:12` | `training_session` → `__Host-training_session` | Decisão A11; é o único arquivo G3 alterado e foi autorizado. |
| `apps/api/tests/test_api_auth_hardening.py:31` | cookie literal renomeado | Mantém o mesmo cenário concorrente, sem mudar asserção. |
| `packages/generation/tests/test_chat_integration.py:260` | `'&lt;script&gt;'` → `'<script>'` | Decisão A4; continua `in` com marcador. |
| `packages/generation/tests/test_geracao_validacao.py:172-173,182` | igualdades escapadas → igualdades literais | Decisão A4; continuam igualdades exatas, nenhuma asserção removida. |

Nenhuma asserção foi retirada nem trocada por outra mais fraca. Os outros quatro arquivos G3 estão idênticos (hashes conferidos). O controle que o escape deixava de garantir passa a ser coberto por casos novos em `test_api_plaintext.py`: `<script>` e `<img onerror>` literais iguais no SSE, nos GETs e no banco, `&amp;` sem dupla conversão, e remoção de Cf e Cc.

## Execução fora do event loop (`chat_execution.py`) — correta

- Cada turno roda em `ChatWorkers.executor` (threads `training-chat-turn`, `max_workers = chat_max_active_global`) com um event loop próprio. O executor padrão desse loop é privado (`training-chat-io`, 2 workers), então o `asyncio.to_thread` do domínio não usa o executor do ASGI (`chat_execution.py:132-137`). O preflight de taxa e o escopo também saem do loop ASGI (`chat_routes.py:64-67`).
- Cancelamento: `cancel()` e `execute()` trocam `loop`, `task` e `cancel_requested` sob o mesmo `Lock` (`:89-95,140-145`), sem janela entre cancelar e começar. `call_soon_threadsafe(task.cancel)` cruza de loop corretamente. Num timeout, `wait_for` cancela e aguarda a pipeline, que grava o erro, e `error_response(cancelled=True)` reaproveita esse turno (`:146-153,101-109`).
- Drenagem: `shutdown_asyncgens` e `shutdown_default_executor` rodam antes de fechar o loop (`:159-165`). A vaga (`Lease`) é idempotente e só é liberada no `finally` do gerador ou no `aclose` do `BackgroundTask`, ambos sob `CancelScope(shield=True)` (`chat_routes.py:143-153,156-172`).
- Os callbacks de progresso continuam indo para o loop ASGI por `call_soon_threadsafe` (`chat_routes.py:57-58`). O marcador de término vem do `add_done_callback` do future (`:85`), e a fila é drenada antes do `final` (`:133-139`), o que mantém o E12 ordenado mesmo depois de um timeout.
- Limite de corpo: `ChatBodyLimit` recusa `Content-Length` acima de 256 KiB e conta os bytes de corpo chunked antes do FastAPI (`chat_body.py:16-33`).

## Achados

| ID | Severidade | Local | Achado | Recomendação |
| --- | --- | --- | --- | --- |
| D1 | Documentação (não bloqueante; fora do papel `rag-backend`) | `docs/arquitetura/pipeline-rag.md:110`, `docs/arquitetura/pipeline-rag.md:194`, `docs/adr/0004-guardrails-e-juizes.md:21` | A arquitetura e a ADR-0004 ainda descrevem o E11 como "sanitização e escape para renderização web", e o `apps/web` como "sempre escapados". Isso agora contradiz a MODIFICA RAG-SAI-001 (decisão A4). A ADR-0013:19 já é compatível (texto do React, sem `dangerouslySetInnerHTML`). A spec viva `governanca/specs/chat/spec.md:96` vai ser atualizada pelo arquivamento do delta. | O orquestrador deve despachar ao `arquiteto` o ajuste da arquitetura e uma nota ou emenda à ADR-0004 antes do G4, ou registrar a pendência. |
| N1 | Nota | `chat_execution.py:101-107` | Para reaproveitar o turno cancelado, a busca é feita por dono + `session_id` + `created_at >= started` (comparação de strings ISO no JSONB) + status erro, pegando o mais recente. Com o padrão de 1 turno ativo por dono (`config.py:35`), não há ambiguidade. Se o limite subir acima de 1, um turno cancelado pode pegar o erro de outro turno concorrente da mesma sessão. A comparação textual de datas funciona com o formato atual do Pydantic, mas é frágil. | Num endurecimento futuro, propagar o `chat_turn_id` do domínio ao `ChatJob`, ou documentar que `chat_max_active_per_owner` deve ficar em 1. |
| N2 | Nota | `chat_runtime.py:28-29`; `config.py:36,41-42` | O pool do chat tem `pool_size=4`, `max_overflow=0` e `pool_timeout=2`, para até 4 turnos com 3 threads cada (turno + 2 de I/O). O domínio não segura conexão durante a LLM, então a disputa é curta. Ninguém mediu isso sob carga, e esgotar o pool vira `final` de erro, não travamento. | Nenhuma ação agora; medir na carga real. |
| N3 | Nota | `packages/generation/tests/test_chat_integration.py:261` | `'&amp;lt;' not in back` ficou quase vazia sem escape. Não houve afrouxamento: a linha não foi alterada e a cobertura de `&` literal está em `test_api_plaintext.py`. O nome dos casos ainda diz "escape", o que foi mantido de propósito para preservar IDs. | Nenhuma ação nesta tarefa. |
| N4 | Nota | `packages/lineage/src/training_lineage/chat.py:93` e dados já persistidos | Turnos gravados antes da A4 guardam `response` com HTML escapado. Os GETs de linhagem devolvem esse conteúdo como está, e um cliente com `textContent` mostraria `&lt;` literal. Só ambientes de desenvolvimento têm esses dados. | Se houver dados a preservar, registrar que turnos anteriores à A4 têm apresentação legada. Não migrar sem o humano. |
| N5 | Nota | `chat_execution.py:137` | `asyncio.set_event_loop(loop)` não é desfeito depois de `loop.close()`, então a thread do executor fica com um loop fechado como corrente até o próximo turno. O próximo turno define outro loop, logo não há efeito hoje. | Opcional: `asyncio.set_event_loop(None)` no `finally`. |

As notas N1–N3 anteriores (`revisao-anthropic.md`) foram tratadas: o log da composição agora registra a categoria (`app.py:62-64`), e a duração de etapa já notificada continua como estava, o que é aceitável.

## O que esta revisão não é

Não substitui a revisão de segurança (`seguranca-llm`) desta rodada nem o aceite final do humano (G4). Não fiz commit.

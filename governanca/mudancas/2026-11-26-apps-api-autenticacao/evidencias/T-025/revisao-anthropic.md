# T-025 — revisão anthropic (somente leitura)

- Revisor: anthropic (Claude Code, worker Orca `task_db00c34fe6d3`, dispatch `ctx_65f5a6068a15`)
- Implementação revisada: openai (`ctx_50488ff15770`), commit `47270ee`; diff de `packages/generation` desde `b3b2969`.
- Lido: `AGENTS.md`, `delta.md` (API-CHT-001, API-LIN-001, AUT-OWN-001), `tarefas/T-025.md`, `evidencias/T-025/{interface.md, relatorio.txt, diagnostico-gerar-mais.txt, pytest-r2.txt, real-http/manifest.json}`, `docs/adr/0011`, `docs/adr/0017`, `apps/api/src/training_api/{app,chat_routes,chat_runtime,auth,csrf}.py`, `packages/generation/src/training_generation/{chat,cli}.py`, os casos `apps/api/tests/test_api_chat.py` e `api_chat_support.py`.
- Execução: `uv run pytest apps/api/tests/test_api_chat.py -q` (uma vez, chaves LLM vazias no subprocesso) → **Passou**, 18 passed em 18.48s. Suíte inteira e APIs reais: **Não executado** (fora do escopo da revisão).
- Casos congelados: sha256 de `test_api_chat.py` (`3df89ae3…d133`) e `api_chat_support.py` (`3a716b89…a30e1`) conferem com o frontmatter da T-025.

## Veredito

**Aprovado com ressalvas.** Nenhum achado bloqueante. Os requisitos API-CHT-001, API-LIN-001 e AUT-OWN-001 estão atendidos no caminho normal, recusa, erro de domínio e desconexão; não há mock em código de produto; E1–E12 não mudaram. Há uma ressalva de robustez (M1) que recomendo corrigir numa tarefa de acompanhamento, e um efeito colateral aditivo na CLI (M2) que precisa ser aceito ou documentado pelo humano.

## Conferência por requisito

### API-CHT-001 — Passou (com ressalva M1)

- **Ordem**: `chat_routes.py:84-99` libera E1–E7 só quando todas as predecessoras executadas chegaram (o `break` em `:91-92` segura E3 até E2 chegar) e retém E8–E12 até o fim, porque reparos voltam a E8/E9/E10. `LABELS` (`:22-23`) é construído em ordem E1..E12. O agregado `E5_E7` emitido por `_span` (`chat.py:186-188`) é descartado pelo filtro de `LABELS` (`chat_routes.py:81`), e E5/E6/E7 reais vêm dos spans por `_StageLatencies.on_end` (`chat.py:125-129`). Etapa não executada não é inventada (`:93-94`). Repetição não duplica a notificação (`emitted`).
- **Final único após o commit**: `ChatPipeline._run` grava a linhagem em `_persist` (`chat.py:286`, transação `engine.begin()` em `:232`) antes de retornar; a rota só emite `final` depois de `await task` (`chat_routes.py:113-116`), exatamente uma vez, com `ChatResponse` validada e sem envelope paralelo. Recusa (E1→E12) e erro de domínio (`chat.py:292-303`) retornam `ChatResponse` e portanto também terminam com `final`.
- **Cabeçalhos**: `text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no` (`chat_routes.py:126-127`). Keepalive é comentário SSE (`:106`), não evento de domínio.
- **Cancelamento**: a task é criada dentro do gerador (`:74`), então desconexão antes da primeira iteração não deixa produtor órfão. No `finally` (`:117-124`) a task é cancelada e aguardada sob `CancelScope(shield=True)`; a pipeline grava `erro` sem itens e relança (`chat.py:287-291`); o `CancelledError` é suprimido só nesse await. Coberto pelo caso ASGI real `test_api_cht_001_disconnect_cancels_and_persists_error`.
- **Sessão de auth confirmada antes do trabalho longo (N2)**: `current_user` faz `commit()` logo após ler o token (`auth.py:180`) e `require_csrf` também (`csrf.py:21`), ambos antes do corpo da rota. O escopo (`versions`, `chat_routes.py:30-40,57`) roda numa conexão curta do engine síncrono de linhagem, antes do stream. O produtor só recebe o `owner` textual. Coberto por `test_api_cht_001_auth_transaction_released_before_models`.
- **Progresso a partir da thread**: `progress` usa `loop.call_soon_threadsafe` (`:51-53`); o marcador de término usa `call_soon` (`:72`) e entra depois dos callbacks já agendados pela thread de recuperação, que termina antes de `to_thread` retornar.

### API-LIN-001 — Passou

- `GET /lineage/items/{id}` serializa `ChatLineageRepository.get_item_lineage(owner, id)` (`chat_routes.py:130-132,147-152`), cujo `select` junta `chat_item` a `chat_turn` filtrando `owner_id` (`packages/lineage/src/training_lineage/chat.py:286-288`) e só devolve item `entregue`.
- `GET /lineage/turns/{id}` filtra o turno por `owner_id` **antes** de carregar os itens (`chat_routes.py:137-140`) e devolve os itens na ordem da resposta (`:143-144`).
- Outro dono e inexistente: mesmo `HTTPException(404, 'Not Found')` (`:150-151,158-159`). As colunas `item_id`/`chat_turn_id` são `Text` (`tables.py:199,229`), então um id arbitrário não vira 500 e não diferencia existência.

### AUT-OWN-001 — Passou

- `owner` vem só de `current_owner_id` (`auth.py:186-187`); `ChatRequest` herda `extra="forbid"` (`training_contracts/comum.py:28`), então `owner_id` no corpo é 422; nenhuma rota lê `owner_id` da query.
- POST e os dois GET dependem de `current_owner_id` → 401 sem sessão válida; POST exige CSRF (`chat_routes.py:43`).

### Sem mock em produto — Passou

- `ChatRuntime` (`chat_runtime.py`) compõe só componentes reais (engine, `IndexingPipeline.from_env`, `GenerationService`, `LocalReranker` com calibração obrigatória, tracing OTel local), no mesmo padrão da CLI (`cli.py:60-74`), inclusive `RequestAdmission(6, 12)` igual aos defaults da CLI (`cli.py:26-27`).
- Configuração ausente: o lifespan registra aviso e não define a factory (`app.py:58-64`); `POST /chat` responde 503 explícito (`chat_routes.py:45-47`). Não há resposta fixa ou implementação alternativa.
- Dublês só em `apps/api/tests/api_chat_support.py`. `run_chat_http_real.py` não é coletado pelo pytest (nome não começa com `test_`).

### E1–E12 inalterados — Passou

- O diff de `packages/generation/src/training_generation/chat.py` desde `b3b2969` tem só três partes: a `ContextVar` (`:105`), o callback em `_StageLatencies.on_end` (`:125-129`, protegido por `try/except` e restrito aos spans E5/E6/E7) e o envoltório `run` → `_run` (`:259-267`), que define e restaura a `ContextVar` no `finally`. O corpo de `_run` é o antigo `run` (conferido no diff; o diagnóstico registra igualdade por AST). A `ContextVar` é copiada por `asyncio.to_thread`, então cada execução vê só o próprio callback, mesmo com `_StageLatencies` compartilhado por provider.

### Diagnóstico de `gerar_mais` — se sustenta

- No commit revisado, o dublê de T-021 gera o enunciado `f'O que é descrito por {chunk.chunk_id[:8]}?'` com alternativas fixas (`git show 47270ee:packages/generation/tests/test_chat_integration.py`, linha 89). Dois enunciados que diferem só em 8 caracteres hexadecimais ficam perto de `DUPLICATE_RATIO = 0.9` (`validation.py:36`), então o E9 real rejeita uma parte dos pares como duplicata conforme o UUID da versão. O experimento fechado com 256 UUIDs fixados a priori (244 aprovados, 12 duplicatas, cerca de 4,7%) e a receita de hash conferida contra os chunks gravados reproduzem o mecanismo sem mexer no produto.
- O caminho do teste tem `progress=None`, e a única mudança que esse caminho executa é o envoltório da `ContextVar` mais uma subtração no `on_end` antes de ler `callback is None`. Não existe caminho plausível pelo qual isso mude o E9. Concordo que não é regressão.
- Limite honesto, já declarado no relatório: o motivo exato da omissão na execução histórica não foi gravado; o que se demonstra é um mecanismo equivalente.

## Achados

| ID | Severidade | Local | Achado | Recomendação |
| --- | --- | --- | --- | --- |
| M1 | Menor (não bloqueante) | `apps/api/src/training_api/chat_routes.py:113` e `packages/generation/src/training_generation/chat.py:270-274,283,303-309` | Uma exceção que escape de `ChatPipeline.run` sai do gerador **depois** do HTTP 200 e corta o stream sem `final`, contra ADR-0011 §3 ("nunca com o stream cortado sem explicação"). Isso pode acontecer porque a validação de argumentos (`ValueError`), `ChatFailure('Tracing local indisponível')` e o código depois do `try` (`telemetry.take`, `_turn`, `last_execution`) ficam fora do tratamento que converte erro em `ChatResponse(status='erro')`. Exemplo concreto: com `CHAT_MAX_ITEMS` menor que 5, todo `POST /chat` falharia assim, porque a rota usa o `count=5` padrão (`chat.py:273-274`). Caso de domínio normal (erro em etapa, commit) está coberto. | Numa tarefa de acompanhamento: (a) validar no lifespan que `max_items >= 5`, ou passar `count` limitado à configuração; (b) no gerador, capturar `Exception` de `await task`, registrar log sanitizado e encerrar com um `final` de erro sem itens. Para não emitir resposta sem turno gravado, o `final` pode trazer só `status='erro'` quando não houver `chat_turn_id`; isso exige decidir o contrato, portanto passa pelo humano. |
| M2 | Menor (aceite do humano) | `packages/generation/src/training_generation/chat.py:125-129`; `cli.py:71-72` | O callback de E5/E6/E7 também chega ao `progress` da CLI, que agora imprime no stderr os registros `E5`, `E6` e `E7` **além** do agregado `E5_E7` de `_span`. A mudança é só aditiva e não altera a lógica de E1–E12. Mesmo assim, `interface.md` pedia "preservar esse comportamento para a CLI", e quem lê esse stderr passa a ver tempos sobrepostos. | Aceitar e registrar em `Decisões e conflitos`, ou suprimir o agregado só no consumidor que não o quer. Não mexer no domínio sem autorização. |
| N1 | Nota | `apps/api/src/training_api/chat_routes.py:82,88-89` | Uma etapa de E1 a E7 que se repita depois de já notificada acumula tempo em `pending`, mas esse tempo não é reenviado; o `seconds` notificado fica abaixo do total. A interface aceita isso porque não exige duplicar a notificação, e hoje nenhum fluxo conhecido repete E1–E7 depois de E8. | Nenhuma ação, ou documentar que `seconds` é o tempo até a notificação. |
| N2 | Nota | `apps/api/src/training_api/app.py:62-64` | A falha de composição do `ChatRuntime` é engolida com um aviso genérico, sem o tipo da exceção, o que dificulta diagnosticar por que o chat ficou em 503. | Registrar `type(exc).__name__`, sem a mensagem, para não vazar segredo. |
| N3 | Nota | `apps/api/src/training_api/chat_runtime.py:169,190-192` | `env_file='.env'` depende do diretório de trabalho do processo, e a troca do cliente `genai` mexe em atributo interno (`processor.model_client.client`). Os dois seguem o padrão já existente da CLI (`cli.py:65-67`). | Nenhuma ação nesta tarefa. Considerar expor a opção de tentativas em `IndexingPipeline` numa evolução futura. |

## Observação de ambiente (fora do escopo)

No momento da revisão, a árvore de trabalho tinha alterações **não commitadas** de outro trabalho: `packages/generation/tests/test_chat_integration.py` (enunciado do dublê passa a usar o `chunk_id` inteiro), `packages/generation/tests/analisar_duble_questoes.py` e `evidencias/T-025/{analise-duble-questoes.*, pytest-dublê.txt}`. Não avaliei esse material; esta revisão cobre só o commit `47270ee`. Essa alteração está num caso de T-021 já aprovado e precisa de autorização própria do humano.

## O que esta revisão não é

Não substitui a revisão de segurança obrigatória (`security-reviewer`, papel `seguranca-llm`) nem o aceite final do humano (G4). Não fiz commit.

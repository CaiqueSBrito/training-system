# T-021: correção da revisão Anthropic (autoria mista)

- Autoria efetiva: **mista**. O produto original é da OpenAI (`ctx_c55621c67ed1`). As correções desta rodada são da Anthropic (Claude Opus 5.5, worker Orca `task_3654b00024c5`), por ordem do coordenador `msg_5e1ad4a2e659`, depois que os executores OpenAI esgotaram a quota. Os testes novos 436–531 de `test_chat_integration.py` já estavam no arquivo, escritos antes do produto pela rodada parcial anterior, e foram preservados.
- Este worker **não** é revisor independente. A sessão principal OpenAI revisa os diffs desta rodada. A revisão Anthropic do código OpenAI original (`revisao-anthropic.md`, `revisao-seguranca-anthropic.md`) fica preservada como histórico.
- Data: 2026-11-25. Não houve chamada de API real nem smoke real, nenhum peso foi baixado, nada foi commitado e o Langfuse continua desligado. Não toquei em `evals/`, no lock nem em manifests. `protocol.py`, os contratos e as evidências históricas ficaram inalterados.

## Achados e correções

| Achado | Correção | Teste |
| --- | --- | --- |
| 1 (bloqueante): cursor herdado por todo turno | Só `gerar_mais` passa o cursor persistido. O binding fica compatível porque o modo vem do turno anterior. Um pedido novo vai sem cursor, sem excluir fontes anteriores e sem o anti-repetição da sessão, e abre uma progressão nova (`save_continuation(new_progression=True)`, com quantidade da progressão). O padrão continua sem reset. Estado inválido nunca é herdado: `gerar_mais` falha antes do E4, e um pedido novo o substitui. O fence usa `get_continuation_head`, que não depende de cursor válido. | `test_new_request_resets_explicitly_then_more_uses_new_mode_type`, `test_fresh_request_reuses_sources_and_recovers_bad_old_cursor`, `test_malformed_continuation_fails_before_models_and_does_not_reset`, `test_generate_more_*`, `test_concurrent_session_advance_*` |
| 2 / S1: reparo E8 e E10 sem filtro | O reparo roda por grupo (alocação, ou o grupo único da consulta) com o mesmo `NumberedContext` filtrado e as refs locais da geração original. O E10 recebe só os contextos filtrados dos grupos dos itens julgados, renumerados, e as citações são remapeadas. Continua uma única chamada E10 por rodada. Citação fora do contexto filtrado não vai ao juiz. | `test_repair_and_judge_only_receive_filtered_section` (chunk misto real conferido no próprio teste) |
| 3: ref local desconhecida cai na global | É mapeada para `C{n+1}`, que é válida no schema e ausente do contexto global, e o E9 rejeita com `citacao_inexistente`. | `test_unknown_local_ref_is_rejected_as_local_not_global` |
| 4: bruto do reparo não persistido | `ItemRecord.raw_content` passa a ser o bruto da última tentativa. Nova tabela `chat_generation_attempt` (migração aditiva 0005) guarda cada tentativa E8 por item: bruto, `call_id` (agora sempre gerado), índice e resumo do contexto (ref, chunk_id, sha256 e tamanho do texto entregue, sem duplicar a fonte). A leitura é por `get_generation_attempts(owner, item_id)`, e owner alheio recebe `[]`. | `test_every_generation_attempt_is_durably_audited` |
| S2: prompt bruto sem limite | O turno guarda no máximo `max_input_chars` caracteres. `chat_input_audit` (0005) guarda o tamanho bruto, o sha256 e o tamanho guardado, validados em `save_turn` no mesmo savepoint. | `test_oversized_prompt_is_bounded_and_hashed_in_audit` |
| S3: captura segue symlink | Caminho existente ou link é recusado antes de qualquer chamada (retorno 2, JSON sem traceback). A escrita usa `O_CREAT\|O_EXCL\|O_NOFOLLOW` com 0600. | `test_cli_capture_rejects_symlink_without_modifying_target` |
| Nit: processors acumulam | Um `_StageLatencies` por `TracerProvider` (`attach`) e no máximo 256 traces não coletados. | indireto: `test_real_stage_spans_*` |
| Nit: `log.warning` por construtor | Passou a ser `info`, uma vez por processo. | — |
| Nit: CLI com cancelamento e shutdown | `KeyboardInterrupt`/`CancelledError` retornam 130 com JSON fixo. `provider.shutdown` e o fechamento de clientes e engine rodam no `finally`, também em erro. | **Não executado** (sem teste de sinal) |
| Nit: imports não usados | `unicodedata` e `render_item` removidos de `chat.py`. | — |
| 5: evidência anterior ao snapshot | Suíte focal completa nomeada no snapshot desta rodada (abaixo). | — |
| Nit: `_evidence_range` O(n²) | **Não alterado.** O revisor o considerou aceitável no tamanho atual, e trocar a normalização arriscaria a correspondência canônica. | — |
| Ressalva `protocol`: `_verify_batch` privado | **Não alterado**, por ordem de preservar o protocolo. | — |
| Operacional: recursos retidos da execução real | Não verificado nesta rodada. Os únicos containers ativos observados são `app-postgres` e `qdrant` da infra. | — |

Ampliação de escopo, que exige ciência da sessão principal: `packages/lineage/src/training_lineage/chat.py` (T-020) recebeu só acréscimos compatíveis: `save_turn(..., input_audit=None, attempts=())`, `get_generation_attempts`, `get_continuation_head` e `save_continuation(..., new_progression=False)`. `tables.py` recebeu duas tabelas, e há a migração nova `0005_chat_audit.py` (append-only com os triggers de 0004 e downgrade que recusa histórico). Os chamadores existentes continuam iguais.

Mudança de expectativa em teste anterior desta tarefa: `test_malformed_continuation_fails_before_models_and_does_not_reset` deixava de chamar qualquer modelo. Agora o estado inválido só é rejeitado quando o E3 identifica `gerar_mais`, porque um pedido novo precisa recuperá-lo (teste 450). A asserção passou a exigir zero E4/E8, erro e quantidade preservada. O E2 e o E3 rodam.

Fixture ajustado: `test_repair_and_judge_only_receive_filtered_section`, escrito antes do produto, não tinha chunk com caminho `['Manual','Permitida']` (o chunker não cruza H1 e herda o caminho do primeiro span). Por isso a T-018 levantava `ScopeError` legítimo. O texto e o `ChunkConfig(18/38/0)` foram ajustados, e o teste agora confere que existe um chunk misto real antes de exercitar o filtro.

## Provas (snapshot sha256)

| Arquivo | sha256 |
| --- | --- |
| `packages/generation/src/training_generation/chat.py` | `486589c3c79851e7b57ffc0d729808c8570f293a2c13b174ac45d89199d7fdde` |
| `packages/generation/src/training_generation/cli.py` | `aae88f63afd354fff1b79b717dfe637513070befda5f6825856b43b7b7169ac3` |
| `packages/generation/tests/test_chat_integration.py` (final) | `31fcc28db241b65686d6eaddc072a1f9f851c506ba92cb263efc033bb150cade` |
| `packages/generation/README.md` | `7c41fe6249c8629008c84ad24e5416de8ff7f9d0d54b61c6e46bc1397ab8db53` |
| `packages/lineage/src/training_lineage/chat.py` | `2aaf45b7540751456b37819dad1cb987731f7a180950602de9081dce2ab9fd03` |
| `packages/lineage/src/training_lineage/tables.py` | `841f222fa9c6de90c1d13f37beda358ddaa7ca000784f7631d37d17c4e3edb8e` |
| `packages/lineage/src/training_lineage/migrations/versions/0005_chat_audit.py` | `d0474c7c7297de34b33eea12c2500d96a63e64e4e887c20c95e244804e55f369` |

| Execução | Status | Resultado |
| --- | --- | --- |
| Focal única `test_chat_integration.py -rA` (teste em `19cf7fbd…`, produto acima) | **Falhou** | 36 passaram, 1 falhou, 32.38s. `focal-correcao-revisao-chat.txt`. A falha foi `test_repair_and_judge_only_receive_filtered_section`: `ScopeError` em E5_E7 por fixture sem chunk no prefixo. |
| Diagnóstico estreito do caso que falhou | Passou (diagnóstico) | Plugin só em scratchpad, sem mudar o produto. Etapa E5_E7, `ScopeError('Seção ausente ou ambígua entre versões')`. |
| Complemento após o ajuste de fixture (r1) | Falhou | O guarda novo detectou que ainda não havia chunk misto. `focal-correcao-revisao-fixture-secao.txt` |
| Complemento após o ajuste de fixture (r2, teste `31fcc28d…`) | Passou | 1 passou, 36 desmarcados, 3.83s. `focal-correcao-revisao-fixture-secao-r2.txt` |
| Linhagem estreita: `test_chat.py` inteiro, metadata e C02 | **Falhou** | 38 passaram, 1 falhou. `focal-correcao-revisao-linhagem.txt`. Os 20 de `test_chat.py`, `compare_metadata` (inclui a 0005) e downgrade/append-only passaram. |
| Falha `test_C02_history_refuses_downgrade_before_any_drop` | Falhou, **pré-existente** | O teste fixa a cabeça em `'0003'`. Com a 0005 retirada temporariamente, o mesmo caso falha com `'0004' == '0003'`, ou seja, já falhava desde a 0004 da T-020. O teste é de outra tarefa e não foi alterado. |

Não há declaração de suíte integral verde: os 36 casos que passaram não foram repetidos depois do ajuste do fixture, que só altera aquele teste. Não rodei build, lint nem formatter.

## Lacunas reais

- Revisão independente dos diffs desta rodada: **Não executado**, pertence à sessão principal OpenAI. O papel `seguranca-llm` deve reavaliar S1–S3 no snapshot acima.
- CLI real e APIs reais depois da correção: **Não executado**, por ordem do coordenador. A execução real anterior (`real-cli/`) é do produto original e não prova esta rodada.
- O caminho de cancelamento da CLI (130) não tem teste.
- A migração 0005 precisa ser aplicada pelo operador em bancos já migrados para 0004 antes de usar a CLI.
- RAGAS, calibração do juiz e G4: fora desta rodada.

## Rodada 2 (dispatch `ctx_2edefa516adc`, mensagens `msg_30e84685d171`, `msg_d0d129947d80`, `msg_45d87fad648b`)

Autoria Anthropic, mesma condição: não é revisão independente. O histórico da rodada 1, acima, fica preservado; os hashes da rodada 1 foram superados pelos desta rodada.

| Item | Correção | Teste |
| --- | --- | --- |
| C02 fixava a cabeça `'0003'` | O contrato agora é: recusar o downgrade antes de qualquer DROP, preservando a revisão atual. O teste compara o estado observado antes da tentativa (revisão igual à cabeça do Alembic, tabelas e contagens das tabelas de histórico) com o estado depois, e os dados lidos. A falha anterior não foi reexecutada para confirmação. | `test_C02_history_refuses_downgrade_before_any_drop` |
| Reparo de envelope gravado como attempt 1 (incoerente com `call=reparo` da T-022) | Semântica única: `attempt=2` se e só se a chamada E8 que produziu o item é de reparo. A auditoria tem dois níveis (0005, ainda não aplicada em banco real e por isso reescrita): `chat_generation_call` (inicial/reparo, completed/failed, `failure_kind`, `repairs_call_id` → chamada inicial falha, resumo do contexto) e `chat_generation_attempt` (bruto por item → chamada concluída; único por `call_id`+índice). Item sem tentativa 1 só é aceito se veio do reparo de um envelope inicial falho, e nenhum bruto inicial é inventado. Os vereditos E9/E10 desses itens são `tentativa=2`. A leitura é por `get_generation_calls(owner, turn)` e `get_generation_attempts`, este com `call` e `repairs_call_id`. | `test_invalid_initial_envelope_repair_is_attempt_two_persisted_and_t022_consumable`: valida a captura com o contrato real `evals.runner_geracao.E8Capture` (só importado, sem editar `evals`) e confere a persistência. |
| Limite global de reparo | Decisão conservadora, sem ampliar: **uma única chamada E8 de reparo por turno** (RAG-JUI-003 e o brief "orçamento total 1 reparo incluindo envelope"), substituindo a rodada de 1 chamada por grupo da rodada 1. O contexto do reparo é a união filtrada só dos grupos reprovados, com um chunk por entrada (mapeamento injetivo exigido pela T-022); as refs voltam ao grupo dono do item, e ref de outro grupo vira `citacao_inexistente`. O E10 usa o mesmo construtor de contexto. Custo: na cobertura, o modelo vê várias alocações no reparo, e uma evidência que cruza alocações em um chunk compartilhado é recusada por `_resolve` (fail-closed). | `test_coverage_repair_is_one_call_with_filtered_union_and_refs_mapped_back` |
| CLI: cancelamento e erro | Provas offline do consumidor com PG/Qdrant descartáveis reais e `ChatPipeline` real. Só são dublês o SDK genai, o serviço de modelos e o reranker, todos no arquivo de teste. Um SIGINT real durante o E3 dá exit 130 e um JSON fixo no stderr, sem traceback nem segredo, com o turno persistido como `erro`/"Turno cancelado." e sem E4. Um erro de infraestrutura com segredo na mensagem dá exit 2 com JSON fixo. Nos dois casos fecham o provider, os dois clientes genai (original e substituto), o cliente Qdrant e a engine. | `test_cli_sigint_cancels_turn_exit_130_*`, `test_cli_infrastructure_error_exit_2_*` |
| R1 da T-022: `max_retries` do SDK | `agents._completion` (E2 e E10 pelo mesmo caminho LiteLLM) passa a enviar `max_retries=0`, além de `num_retries=0`. Modelos e rubrica ficam iguais. | `test_geracao_transporte.py`: caminho real `_completion` → `litellm.acompletion` → cliente OpenAI/httpx contra um servidor HTTP local que responde 429/503 com `retry-after: 0`. Exatamente 1 requisição por chamada, em E2 e E10. Nenhuma API externa. |

Snapshot da focal r2:

| Arquivo | sha256 |
| --- | --- |
| `packages/generation/src/training_generation/chat.py` | `15a6c1136c8bb63b2e1a665e6bf1e955897e54a7623371db77868b66615e8888` |
| `packages/generation/src/training_generation/cli.py` | `aae88f63afd354fff1b79b717dfe637513070befda5f6825856b43b7b7169ac3` |
| `packages/generation/src/training_generation/agents.py` | `283d437800d6bfa547a50b64984cf8ea367ad94922f748b0482323d9e8dab302` |
| `packages/generation/tests/test_geracao_transporte.py` | `3fecb617a2ccfc209c3a009892408cb81d17441aa5690da2e34ce816352db1c4` |
| `packages/lineage/src/training_lineage/chat.py` | `5e355cd88a90ec969a7a3002a8ac641fb53ce6dfd2a1ac788387c8ce4cbb0df8` |
| `packages/lineage/src/training_lineage/tables.py` | `47176f54499255a4d255fd5a3cee1ede496cf5bcc8a19263e5720179ff2d710d` |
| `packages/lineage/src/training_lineage/migrations/versions/0005_chat_audit.py` | `01f10a539b6d9e0146e8dbfdd92036631af78ad7d753663c80e85d809da34bd7` |
| `packages/generation/tests/test_chat_integration.py` (focal) | `f688ad75ebeda900f04f6a5bd33794b4877c1e2c9ccfad4c415bca0652f1a5ac` |
| `packages/lineage/tests/test_ressalvas_persistencia.py` (focal) | `456ef2a3a3f9a14d2f7f6e7c63b219a0532cbd162fd3b8ccd2a23b3a0350c5e6` |
| `packages/generation/tests/test_chat_integration.py` (final) | `0efc7d4c6b5917e722e15f4d2654e58b0443092f17c5ab26c994f010e8938ac7` |
| `packages/lineage/tests/test_ressalvas_persistencia.py` (final) | `fcbf3468b31157cd3c05dcbdccfe47693cc5c84c50c59015ac1619f7a56061df` |

| Execução | Status | Resultado |
| --- | --- | --- |
| Focal única após todas as mudanças: `test_chat_integration.py` inteiro, `test_geracao_transporte.py`, C02, catálogo/metadata e `test_chat.py` | **Falhou** | 64 passaram, 4 falharam, 47.09s. `focal-correcao-r2.txt`, snapshot em `focal-correcao-r2-snapshot.txt`. As 4 falhas são asserções de teste novas e erradas, não do produto: dois `genai` fechados (a CLI fecha o original na troca e o substituto); o `bundle()` do C02 não popula todas as tabelas de histórico; o dublê do teste de cobertura reparava citando o texto inteiro de um chunk compartilhado entre alocações, recusado com razão por `_resolve`. |
| Complemento só dos 4 corrigidos | Passou | 4 passaram, 7.78s. `focal-correcao-r2-complemento.txt`. O teste de cobertura foi reescrito para reparar preservando a citação de cada item e reprovar tudo na 1ª rodada do juiz. |

Os 64 que passaram não foram repetidos, e não há declaração de suíte integral verde. Não houve smoke nem API real e não houve commit; não toquei em `evals/` nem no lock.

Lacunas: revisão OpenAI desta rodada; a 0005 precisa ser aplicada pelo operador (e foi reescrita depois da rodada 1, então qualquer banco de teste migrado com a 0005 anterior deve ser recriado; nenhum banco real a recebeu); a T-022 deve qualificar `protocol.retries` para o produto com `max_retries=0`.

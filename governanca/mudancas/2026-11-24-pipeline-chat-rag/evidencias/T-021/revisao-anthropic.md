# T-021: revisão técnica independente da Anthropic

- Revisor: anthropic (Claude Opus 5.5), worker Orca `task_165cab01e82f` / `ctx_c109fa06b6ea`.
- Produto revisado: OpenAI (`rag-backend`), dispatch `ctx_c55621c67ed1`. Autor e revisor são de provedores diferentes.
- Data: 2026-11-25.
- Modo: **só leitura**. Não rodei suíte, API, pesos nem git. Não li `.env` nem artefato de chave. Não fiz commit e não editei produto nem progresso. O Langfuse continua desligado.
- Escopo: só os diffs da T-021 e as integrações dela. Não é uma auditoria global. As aprovações das T-018 a T-020 ficam preservadas. A T-022 (avaliação aditiva, outro contexto Anthropic) **não** entra nesta revisão.
- Parecer de segurança LLM em separado: `revisao-seguranca-anthropic.md`.

## Snapshot revisado (sha256)

| Arquivo | sha256 | mtime |
| --- | --- | --- |
| `packages/generation/src/training_generation/chat.py` | `6d4dec24ed67be1a0d79761b7e1c9e198530d297365b8580c983a8110d1891a6` | 00:24:24 |
| `packages/generation/src/training_generation/cli.py` | `737cced2fe22b6424e6f9cc112b242f537bf603a7ae089b972e4a3f193754ad2` | 00:24:24 |
| `packages/generation/tests/test_chat_integration.py` | `8aa958668e9ca56d0c82261ab37172ac03d31c9aef0b44155c01ec1f20b60f3f` | 00:31:55 |
| `packages/lineage/src/training_lineage/chat.py` | `8c9ffcff2c79eac0cc46085a5138256b2706106522373d41b8d90e0f86b00d00` | 00:10:07 |
| `packages/contracts/src/training_contracts/rendering.py` | `3412ca3375f856ea685c0979d5b2719882b50be34059b02757ea8492e9e04cd7` | 00:10:07 |
| `packages/ingestion/src/training_ingestion/protocol.py` | `0536fb312d1cf1e2ee997c8ebfcd507967ab02d8f210610934d6998598385d7a` | 00:10:07 |
| `packages/ingestion/src/training_ingestion/indexing.py` | `d5ef08f7b42fefdc60ce1c70cc7ad972a7fbf7cf1a83e465f92f708521c35b27` | — |
| `packages/retrieval/src/training_retrieval/service.py` | `8962adc7b2c0a0096512f0859cb9924df42b19ad9257157b682df39588662e1c` | 00:20:22 |

O arquivo de teste mudou durante a revisão (00:31:55), e `focal-reparo-e9-e10.txt` (3 passed) apareceu às 00:32. O produto (`chat.py`/`cli.py`) não mudou depois de 00:24:24.

## Veredito

**Não aprovado nesta rodada**: há 1 bloqueante funcional (achado 1). Não há bloqueante de segurança; o parecer de segurança é **aprovado com ressalvas**. A correção de `protocol` está **aprovada**. Esta revisão não aprova G4 nem produção.

## Evidência conferida

| Item | Status | Base |
| --- | --- | --- |
| CLI real, uma execução | Passou | `real-cli-execucao.txt`: `status ok`, `exit 0`, 1 flashcard com fonte. Os hashes do `manifest.json` conferem com `sha256sum` dos 5 artefatos. `run_chat_real.py:107-111` relê `chat_turn.response` por outra conexão e confere `==` resposta e o modo 0600 da captura, ou seja, o commit foi confirmado. |
| Determinísticos iniciais 69 passed / 11 failed | Preservado | `focal-inicial.txt` está intacto. |
| 11 corrigidos | Passou, **mas antes do snapshot final** | `focal-correcao-plan.txt` (00:23:00) é anterior à última edição de `chat.py`/`cli.py` (00:24:24). |
| Captura (1), invariantes (8), reparo E9/E10 (3) | Passou | Executados depois do produto final. Os arquivos só têm pontos, sem nomes de teste. Não há suíte completa de `test_chat_integration.py` (30 casos hoje) no snapshot final. |
| Captura opt-in | Passou | Modo `-rw-------`. Há um contexto (`inicial`, `completed`) e `original_items[0].call_id` igual ao `call_id` desse contexto, com `generation_item_index=0` e `attempt=1`. Li só as chaves de vínculo, não o conteúdo. |
| `diagnostics.json` | Passou | Só durações, modelos, prompt@sha256 offline, `errors=[]`, `embedding_key` e manifesto do reranker com revision e sha. Sem texto de conteúdo nem segredo. |
| Validador | Não executado | Pela restrição desta tarefa. Fica para o orquestrador antes de qualquer commit. |

## Conferências que passaram

| Requisito / ponto | Onde | Resultado |
| --- | --- | --- |
| E1 sem LLM | `chat.py:253-267` | Normalização, limite, slot de taxa e escopo canônico vêm antes de qualquer `_call`. A recusa por tamanho usa o texto bruto e o normalizado. Continuação malformada falha em E1 (`lineage/chat.py:337` → `_canonical_cursor`). |
| E2/E3 em paralelo, cancelamento seguro | `chat.py:268-280` | Duas tasks. Falha do E2 dá recusa `falha_do_juiz_de_entrada` (fail-closed). `finally` cancela as pendentes e faz `gather(return_exceptions=True)`. `CancelledError` não é engolido (`chat.py:143-145,224-228`). |
| E4 só depois de juiz seguro | `chat.py:271-300` | `plan` só roda depois de `await judge` não malicioso e da classificação. `gerar_mais` reaproveita o plano persistido (`chat.py:293-299`). |
| Orçamento global de 1 reparo | `chat.py:382-388,434-443` | `repair_used` é compartilhado entre reparo de envelope JSON e reparo E9/E10. O reparo pega só os índices reprovados (`failed`). Não há terceira geração. |
| HTML escapado de forma compartilhada, original intacto | `rendering.py:11-24`, `validation.py:240-246`, `lineage/chat.py:86-88` | O E11 e a checagem de entrega do E12 usam a mesma `render_item`. `ItemRecord.content` guarda o original canônico (`chat.py:454-456`). |
| E12 grava tudo e faz commit antes da entrega | `chat.py:189-209,223` | Uma transação `engine.begin()`, e a resposta só sai depois do commit. Erro de commit esvazia os itens (`chat.py:237`). Em `save_turn`, `authorize_candidates`, a reconferência de owner, o E9/E10 aprovado e as fontes ficam no mesmo savepoint (`lineage/chat.py:100-147`). |
| Fence de sessão com lock | `chat.py:193-197`, `lineage/chat.py:307-328` | `get_continuation` pega `pg_advisory_xact_lock` até o commit. Gravação de continuação concorrente gera `ChatFailure` e não entrega nada. |
| Continuação de `gerar_mais` e alocações de cobertura | `chat.py:290-303,396,411`, `service.py:193-199,272-296` | `used_chunk_ids` só cresce. `quantity` é cumulativa e checada com lock (`lineage/chat.py:309-311`). Cada item herda a sua `Allocation`, e `_resolve` rejeita citação fora dela. `prior` da sessão entra no anti-duplicata (`chat.py:402`). |
| Latências e spans reais, E6 sem dado falso | `chat.py:96-110,241`, `service.py:241-257` | E5/E6/E7 vêm dos spans reais. Na cobertura não existe span E6, só o atributo `training.E6.status='Não se aplica'`. Modelos e prompts são filtrados pelas etapas que de fato rodaram (`chat.py:177-181`). |
| Sem segredo nem log bruto | `chat.py:139,147,235`, `agents.py:62-80`, `cli.py:77-80` | Os logs têm só categoria e HTTP status. `provider_code` é categórico. A CLI troca exceção de SDK/SQL por mensagem fixa. |
| Correção de `protocol` | ver seção abaixo | Aprovada. |

### Correção de `protocol` (ingestão pública consultável sem reconciliação)

- `protocol.py:221-223` grava `decision_event_id`, `decision_revision` e `input_sha256` no payload (`indexing.py:160-165`, que valida revisão ≥ 1 e hash).
- `protocol.py:225` relê o ponto e o compara, por igualdade estrita de payload e forma de vetor, com a mesma verificação da reconciliação (`reconciliation.py:177-200`), **antes** do ack.
- `record_index_ack` (`repository.py:535-540`) trava o chunk, exige reserva ativa e geração, e exige `decision.revision == decision_revision` atual. A decisão `unavailable` é recusada. Um ack obsoleto, portanto, nunca autoriza nada.
- `eligibility.py:78-84` exige que payload, decisão efetiva e ack coincidam. Se um ponto antigo for sobrescrito por um worker obsoleto depois do ack novo, a leitura falha fechada (fica indisponível) e não autoriza o ponto.
- Testes de consumidor: `test_initial_public_ingestion_is_searchable_without_reconciliation` e `test_initial_ingestion_failure_or_stale_worker_never_acknowledges_or_publishes[upsert|stale_worker]`.
- Ressalva menor: `protocol.py:192-193` instancia `Reconciler` para chamar o método privado `_verify_batch`. O acoplamento é aceitável, mas o certo é expor uma função pública compartilhada.

## Achados

### 1. Bloqueante: trocar de modo, ou fazer pedido novo, na mesma sessão e escopo quebra o turno

- Onde: `chat.py:302` junto com `service.py:186-192`.
- O problema: o cursor persistido é passado ao `RetrievalService` em **todo** turno com o mesmo escopo, e não só em `gerar_mais`. O `binding` do cursor inclui o `modo`.
- Regressão no consumidor (mesma `session_id` e mesmo `document_version_ids`):
  1. Peça "flashcards sobre X" (consulta) e depois "questionário cobrindo todo o capítulo" (cobertura). Resultado: `ContinuationError` → `ChatFailure` em E5_E7 → `status=erro`. Como a continuação só é substituída por um turno bem-sucedido, **todo** pedido no outro modo falha para sempre nessa sessão e escopo.
  2. No mesmo modo, um pedido novo (não `gerar_mais`) exclui todos os chunks já usados (`service.py:199`). Repetir o tema ou mudar o tipo de item (flashcard → questão) cai em `conteudo_insuficiente` num documento pequeno.
- Não há teste que cubra esses casos. `test_generate_more_*` só cobre cobertura → `gerar_mais`.
- Correção sugerida: separar a continuação por (escopo canônico, modo). Por exemplo, chave `(owner, session, scope_hash, modo)` ou um mapa `modo → cursor` dentro do estado, mantendo o "sem reset" por modo. Pedido novo que não é `gerar_mais` deve abrir o seu próprio cursor sem apagar o progresso do outro modo. Adicionar um teste de consumidor consulta → cobertura → `gerar_mais` na mesma sessão. Isso mexe em `lineage/chat.py` (T-020); a coordenação fica com o principal.

### 2. Médio: o reparo E8 e o juiz E10 recebem o contexto sem o filtro de seção e alocação

- Onde: `chat.py:438` (reparo) e `chat.py:422` (juiz). Os dois usam `state.retrieval.context`, cujo `chunk.text` é o texto integral do chunk. A geração inicial usa `_context_for` (`chat.py:311-331`), que corta por `section_prefix` e pela `Allocation`.
- Impacto:
  - Com `--section`, ou na cobertura com chunk sobreposto (`service.py:143-146`), o modelo vê texto fora da seção pedida.
  - Na cobertura com várias alocações, o reparo recebe o contexto de todas. O item reparado tende a citar outra alocação e é rejeitado por `_resolve` (`chat.py:352-353`). O reparo vira desperdício.
  - A defesa em profundidade (`_resolve`) impede a entrega fora do escopo. O problema é minimização de dados e eficácia do reparo, não vazamento entre owners.
- Correção sugerida: reparar por grupo, com `local=self._context_for(state, allocation.chunk_ids, allocation)`, remapeando as refs como na chamada inicial. Passar ao E10 o mesmo contexto filtrado usado na geração do item.

### 3. Baixo: ref local desconhecida cai na numeração global

- Onde: `chat.py:394`.
- `local_refs.get(ref, ref)` mantém uma ref que não existe no contexto local, e ela passa a ser lida contra o contexto **global**, onde `C3` pode ser outro chunk.
- Hoje o E9 e o `_resolve` rejeitam por evidência ou por escopo, então não chega a ser entregue. O correto é rejeitar já com `citacao_inexistente`, mapeando para um sentinela inválido.

### 4. Baixo: a saída bruta do reparo não é persistida

- Onde: `chat.py:441` e `chat.py:455`.
- `raw` não é atualizado com `fixed.itens`, então `ItemRecord.raw_content` fica com a 1ª tentativa e `content` com a reparada. O docstring de `ItemRecord` tolera isso ("content após reparo"), mas o bruto do reparo só sobrevive na captura opt-in.
- Para auditoria de RAG-JUI-003, vale gravar o bruto da tentativa 2, ou registrar no `VerdictRecord` que houve reparo.

### 5. Baixo: evidência dos 11 corrigidos é anterior ao produto final, sem suíte completa no snapshot final

- `focal-correcao-plan.txt` é de 00:23:00. `chat.py`/`cli.py` são de 00:24:24.
- Os arquivos focais não listam os nomes dos testes.
- Recomendo uma única execução completa de `packages/generation/tests/test_chat_integration.py` no snapshot final, com `-rA` ou `-v` para rastreabilidade, antes do aceite.

### 6. Nits

- `chat.py:9,20`: `unicodedata` e `render_item` são importados e não usados.
- `chat.py:121-123`: cada `ChatPipeline` adiciona um `_StageLatencies` ao provider. Se o provider for compartilhado (`generation._tracer_provider`), processors antigos acumulam traces que nunca saem por `take`.
- `chat.py:130`: `log.warning` em todo construtor é ruído. Melhor `info`, uma vez só.
- `cli.py:58,77`: `CancelledError` e `KeyboardInterrupt` passam por `except Exception` e imprimem traceback. `provider.shutdown()` não roda no caminho de erro.
- `chat.py:66-93`: `_evidence_range` é O(n²) por citação (normaliza cada prefixo). Aceitável no tamanho atual dos chunks; vale medir se `max_tokens` crescer.
- Operacional: a execução real ficou com `resources_retained: true` (banco `test_t021_real_*` e container Qdrant em `127.0.0.1:32814`). Limpar depois.

## O que falta

- Corrigir o achado 1, com teste de consumidor, e reavaliar.
- Os achados 2 a 4 podem entrar na mesma rodada ou ficar como ressalvas registradas.
- Uma suíte completa de `test_chat_integration.py` no snapshot final e o validador, rodados pelo autor.
- Aceite humano (G4) fora desta revisão.

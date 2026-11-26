# T-020 — Reavaliação independente Anthropic (rodada 2)

- Revisor: anthropic (Claude Opus 5.5), worker Orca `task_d689ca0c42af` / `ctx_21e338d7a967`.
- Correção revisada: OpenAI, worker `task_491f2d176b6a` / dispatch `ctx_2293e96364d2`. O produto foi feito por outro provedor.
- Data: 2026-11-25.
- Modo: **só leitura**. Nenhuma suíte, API, migração ou git; nenhum segredo lido; sem commit; produto não editado.
- Lido:
  - `evidencias/T-020/{correcao-revisao.md,interfaces.md,pytest-correcao-revisao.txt}`;
  - `packages/lineage/src/training_lineage/chat.py`;
  - `packages/lineage/src/training_lineage/repository.py` (`authorize_candidates`, `_active_reservation`, `record_index_ack`, `checkpoint_job`, `reserve`, `begin_reconciliation`);
  - `packages/lineage/tests/test_chat.py`.
- Para a ordem de locks, conferi as sequências de escrita em `training_ingestion/{protocol,reconciliation}.py`, a chamada em `retrieval/eligibility.py:57` e os chamadores com `dict` nos testes.
- Contexto: a T-018 foi concluída antes nesta sessão (`../T-018/revisao-anthropic.md`, **Aprovado parcialmente**; RAG-REC-002 não aprovado). Não interfiro na revisão da T-019.
- O parecer anterior (`revisao-anthropic.md`, **Não aprovado nesta rodada**), a focal inicial com 10 erros e a complementar com 10 passed ficam preservados.

## Veredito

**Aprovado** para a slice de persistência T-020 (RAG-SAI-002, OBS-LIN-001, RAG-ENT-001). Os dois bloqueantes anteriores estão resolvidos. Dos dois não bloqueantes, o 3 está documentado e o 4 está aceito como restrição operacional.

Esta aprovação **não** cobre a integração E1–E12 nem a CLI. As interfaces prontas são pré-requisito da T-021, que continua pendente. A prova focal (`19 passed, 1 warning in 5.21s`) e o validador foram executados pelo autor. Não os executei de novo.

## Reavaliação dos bloqueantes

| # | Bloqueante R1 | Estado | Base |
| --- | --- | --- | --- |
| 1 | `ready` lida sem lock; despublicação concorrente antes do commit | **Resolvido** | Ver detalhamento abaixo da tabela. |
| 2 | Escopo de continuação não canônico permitia reset | **Resolvido** | Ver detalhamento abaixo da tabela. |

**Bloqueante 1.**
- Ordem de locks em `repository.py:610-658`:
  - descobre as versões pelos chunks imutáveis, sem lock;
  - trava **todas** as reservas `FOR SHARE`, em ordem de `document_version_id` e filtradas pelo owner (`:629-637`);
  - **só depois** trava os chunks `FOR SHARE`, em ordem de `chunk_id` (`:638-643`), inclusive em lotes de 128, sem alternar reserva e chunk.
- A fase é conferida na linha de reserva já travada (`:653-655`). Os locks ficam na transação do chamador até o commit.
- O chat não pré-trava mais chunks (`chat.py:111-117`).
- Escritores conferidos:
  - fenced: `_active_reservation` (`:324-333`) usa `FOR UPDATE` na reserva antes de `_owned_chunk(lock=True)` em `append_assessment` (`:262-266`) e `record_index_ack` (`:535-537`);
  - `reserve` e `begin_reconciliation` trabalham na reserva, e o `document_version` que `begin_reconciliation` trava não é pedido pela barreira;
  - a reclassificação sem fencing só pede o chunk `FOR UPDATE`.
- Nenhum escritor de `protocol.py` ou `reconciliation.py` encadeia chunk e depois reserva na mesma transação (cada `_write` é curto). Por isso não há ciclo:
  - chat contra escritor fenced: os dois pegam a reserva primeiro;
  - chat contra escritor sem fencing: o chat só espera o chunk.
- As FKs de `chat_candidate`/`chat_citation` levam `KEY SHARE` em chunks, compatível com os `SHARE` já mantidos.
- Testes com duas conexões reais:
  - `test_chat.py:245-277`: `begin_reconciliation`, `reserve` e `append_assessment` recebem `55P03` enquanto o chat não confirma, e operam depois do commit, com a resposta íntegra;
  - `:280-318`, ordem inversa: o writer segura a reserva, o chat espera nela (provado com `pg_blocking_pids`), o writer reclassifica com fencing sem deadlock, e o chat rejeita o contexto revogado sem gravar o turno.

**Bloqueante 2.**
- `canonicalize_scope` (`chat.py:219-241`):
  - aceita só `document_version_ids` e `section_prefix`, e chaves extras são erro;
  - ordena e deduplica as versões, sem `strip` que confunda identidades;
  - deixa `section_prefix` como lista explícita, mantendo a ordem hierárquica;
  - recusa versões de outro owner antes de comparar ou gravar.
- `save_continuation` valida, compara o progresso e persiste no formato canônico (`:275-312`).
- `get_continuation` canonicaliza também o estado legado (`:320-335`), então a comparação `previous['scope']==scope` passa a ser semântica.
- Cursor v1:
  - exige o conjunto exato de chaves;
  - confere o binding contra owner, versões, seção e o modo do turno **persistido nessa owner/session** (`:243-273`);
  - reescreve o binding só para a forma canônica equivalente;
  - recusa o binding de outro owner.
- O binding é calculado igual ao de `training_retrieval/service.py:153` (tupla e lista serializam igual em JSON).
- Testes:
  - `test_chat.py:321-336`: três codificações equivalentes não permitem 8→0;
  - `:339-356`: estado legado sem seção não reseta, chave extra e versão estrangeira são recusadas, e o estado fica intacto;
  - `:359-380`: o cursor é canonicalizado, o binding de outro owner é recusado e o estado não muda.

## Não bloqueantes anteriores

- **3 (rate limit e READ COMMITTED).** Documentado em `interfaces.md:70`, sem alegação de suporte a REPEATABLE READ. **Aceito.**
- **4 (corrida no downgrade).** Mantida e documentada como exigência de aplicação quiescente (`interfaces.md:70`, `correcao-revisao.md`). A 0004 não mudou. **Aceito como restrição operacional**, não como correção.

## `observed_revision=None`

- `None` só é usado pelo E12 (`chat.py:115`) e revalida a revisão atual sob os mesmos locks.
- Todos os outros consumidores continuam passando `dict` explícito e mantêm a exigência da revisão observada:
  - `eligibility.py:57`;
  - `protocol.py:465`;
  - os testes de `lineage` e `ingestion`.
- O tipo de retorno não mudou, e não há commit implícito.
- Limite já registrado na R1, sem mudança: o E12 não compara com a revisão vista no E5, porque `RetrievalCandidate` não a carrega. Um ciclo revogar e depois liberar de novo seria aceito. O texto é imutável e a decisão atual é `allowed`, então o impacto é baixo. A T-021 pode passar o `dict` observado se a recuperação expuser as revisões.

## Observações (sem ação obrigatória)

- **Efeito em todos os consumidores.** A barreira agora toma `FOR SHARE` nas reservas para todos eles, inclusive as leituras curtas de `authorize_points`. Isso gera contenção breve com `begin_reconciliation` e `reserve` durante a recuperação. É aceitável porque essas conexões são curtas.
- **Cursor legado incompatível.** Um cursor v1 persistido com binding que não corresponde a nenhuma forma equivalente faz `get_continuation` levantar `ValueError` (falha fechado). Para a T-021, isso significa que esse estado precisa ser tratado explicitamente, sem apagá-lo.
- **Ordem no RetrievalService.** A T-021 precisa montar o `RetrievalScope` a partir de `canonicalize_scope`. Com ordem não canônica, o `RetrievalService` recusa o cursor (`ContinuationError`), o que é fail-closed.
- **Warning de Pydantic.** O warning da fixture de erro continua. Não é falha, porque `save_turn` revalida antes de gravar.

## Não executado

- Suítes, focal e testes concorrentes: **Não executado** por esta revisão (proibido pelo brief). O resultado considerado é o `pytest-correcao-revisao.txt` do autor.
- Integração E1–E12/CLI (T-021): **Não se aplica**; não aprovada aqui.

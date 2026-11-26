# T-020 — Revisão independente Anthropic

- Revisor: anthropic (Claude Opus 5.5), worker Orca `task_0a99b166dd3a` / `ctx_f6263a7b0090`.
- Produto revisado: OpenAI, dispatch `ctx_8af50d85ac08`. O provedor do produto é diferente do revisor, como exige a regra 3.
- Autonomia humana registrada em `tarefas/T-020.md` (`permissao.palavras`). Não exijo gates extras.
- Data: 2026-11-24.
- Modo: **só leitura**. Nenhuma suíte, API, migração compartilhada ou git executados; nenhum segredo lido; Langfuse desligado; sem commit.
- Lido: `tarefas/T-020.md`, `design.md` (§T-020 e regras transversais), `evidencias/T-020/{resultado,interfaces}.md`, `pytest-complemento.txt` (10 passaram, 1 warning, 2.90s), `packages/lineage/src/training_lineage/chat.py`, `tables.py` (trecho de chat), `migrations/versions/0004_chat.py`, `packages/lineage/tests/test_chat.py`. Li também, só como referência para a barreira, `repository.py` (`_owned_chunk`, `append_assessment`, `reserve`, `begin_reconciliation`, `record_index_ack`, `authorize_candidates`) e os contratos `ChatTurnRecord`, `RetrievalRunRecord` e `ResolvedSpan`. Não reaudito as tarefas antigas.

## Veredito

**Não aprovado nesta rodada**, por dois achados pequenos e corrigíveis dentro do escopo da T-020.

- **Achado 1:** a fase `ready` não fica travada até o commit.
- **Achado 2:** a continuação pode ser reiniciada usando um escopo equivalente com outra codificação.

As demais propriedades pedidas estão **aprovadas** dentro dos limites abaixo. Não avalio a integração E1–E12/CLI da T-021, que ainda não está integrada. Os testes antigos que fixam `head == 0003` podem ser migrados normalmente. Não exijo alias nem shim de produção.

## Propriedades aprovadas

| Propriedade | Base |
| --- | --- |
| Gravação atômica de turno, plano, recuperação, candidatos, itens, citações, vereditos e resposta | `chat.py:111-170`: o grafo inteiro é gravado num único `begin_nested()`, depois de toda a validação em Python (`:58-108`). `test_last_insert_failure_rolls_back_only_savepoint` (`test_chat.py:185-202`) falha na última inserção, por `verdict_id` duplicado, e mantém só o turno anterior. |
| Sem commit implícito; transação do chamador | `save_turn`, `save_continuation` e `acquire_chat_rate_slot` não chamam commit nem rollback. O savepoint só é liberado, e a transação externa continua do chamador. Coberto por `test_chat.py:94-108` (rollback externo apaga tudo) e `:120-125` (falha depois de `save_turn` faz rollback). |
| E1 sem intenção ou modo inventados | Contrato `ChatTurnRecord` (`linhagem.py:59-104`), guarda em `chat.py:76-77` e CHECK `chat_turn_e1` no banco (`0004_chat.py:23`). Testado em `test_chat.py:111-117`. Status `erro` é persistido sem classificação (`:118-119`). |
| Isolamento por owner e sessão | `_owned_chunks` (`chat.py:42-50`), CHECK `chat_turn_envelope` (`0004_chat.py:21`) e FKs compostas `(chat_turn_id, owner_id)` em candidatos e citações (`:62,94`). Trigger `chat_check_owner` (`:152-161`). Continuação com PK `(owner_id, session_id)` e FK `(chat_turn_id, owner_id, session_id)` (`:122-123`). `get_item_lineage` filtra por owner no turno e na citação (`chat.py:173-184`). Testes `:98,128-138,168-169,205-217`. |
| Fonte canônica exata | Cada citação exige `CitationSpanSelection` (`chat.py:83-84,122-127`) resolvida pela proveniência T010. A página só é preenchida quando todos os spans concordam e são `exact` (`:131-134`); caso contrário fica `None`, sem cair para a página inicial. As fontes da resposta são comparadas exatamente (`:146-147`). No banco, o trigger `chat_check_citation` exige mapeamento `exact` dentro do intervalo do chunk selecionado (`0004_chat.py:162-176`). A releitura é conferida contra os spans persistidos (`chat.py:188-190`). Testado com spans 6:9 na página 3 (`test_chat.py:101`). |
| Revalidação da decisão com lock até o commit | Lock compartilhado nos chunks selecionados (`chat.py:115-116`, `repository.py:88-89`) e `authorize_candidates` com `FOR SHARE` (`repository.py:618-621`). A reclassificação toma `FOR UPDATE` no mesmo chunk (`repository.py:266`), então fica bloqueada até o commit do chat. Decisão revogada antes do commit é recusada (`test_chat.py:237-244`). |
| Rate limit com relógio do banco e concorrência | Advisory lock transacional por owner, `clock_timestamp()` lido depois do lock e contagem na janela (`chat.py:213-221`). Com duas conexões concorrentes e `limit=1`, o resultado é exatamente `[False, True]`. O limite persiste em nova conexão, é isolado por owner e expira (`test_chat.py:141-154`). |
| Continuação durável, sem replay e com owner correto | Lock por owner e sessão até o fim da transação (`chat.py:266-279`), chunks do owner dentro do escopo (`:238-254`), sem perda de progresso no mesmo escopo (`:256-259`) e rollback que não avança o estado (`test_chat.py:170-173`). Exceção: achado 2. |
| Downgrade não apaga histórico | `0004_chat.py:179-184` verifica que todas as tabelas, inclusive continuação e eventos de rate, estão vazias antes de qualquer DROP. Testado em `test_chat.py:176-182`. Histórico imutável por trigger (`:147-151`). Exceção: achado 4. |
| Schema igual ao metadata | `compare_metadata == []` (`test_chat.py:205-209`). |

## Achados

1. **[Médio, bloqueante] A fase `ready` não fica travada até o commit.** Arquivos: `chat.py:115-121`; `repository.py:630-635`, já existente e usado como barreira.
   - `save_turn` revalida que a versão está `ready` lendo `ingestion_reservation` **sem lock** (`repository.py:630-633`). Os locks compartilhados ficam só nas linhas de `chunk`.
   - `begin_reconciliation` (`repository.py:441-469`) e `reserve` (`:346-363`) passam a reserva de `ready` para `persisted` com `FOR UPDATE` na própria reserva e em `document_version`. Isso não conflita com o `FOR SHARE` em `chunk`, então podem confirmar entre a revalidação do chat e o commit dele.
   - Cenário: o chat valida a versão como `ready`, outra transação inicia a reconciliação (`persisted`) e confirma, e então o chat confirma. A resposta é entregue com contexto que a própria barreira (`authorize_candidates`) já negaria no momento do commit.
   - O design (`design.md:29`) e o brief pedem revalidação de decisão **e de `ready`** com lock até o commit. A decisão está coberta; `ready` não está.
   - Correção sugerida, só em `chat.py`, dentro da slice: dentro do savepoint e antes de `authorize_candidates`, ler com `FOR SHARE` as linhas de `ingestion_reservation` (owner e `document_version_id`) das versões dos chunks selecionados, em ordem determinística. As transições existentes usam `FOR UPDATE` e ficariam bloqueadas.
   - Teste de consumidor sugerido: duas conexões. A primeira chama `save_turn` sem confirmar; na segunda, `begin_reconciliation` precisa bloquear, ou expirar com `lock_timeout`, até o commit da primeira. Fazer o mesmo para `append_assessment`, que hoje só tem teste sequencial (`test_chat.py:237-244`).

2. **[Baixo/Médio, bloqueante para "sem replay"] A comparação de escopo não é canônica.** Arquivo: `chat.py:239-264`.
   - A proteção contra perda de progresso só vale quando `previous['scope'] == scope` (`:257`), e o escopo é comparado e persistido exatamente como o chamador mandou.
   - Escopos equivalentes passam como "escopo novo" e permitem zerar `used_chunk_ids`/`quantity`, o que leva a repetir os primeiros cards em `gerar_mais`. Exemplos:
     - `{'document_version_ids': ['v1']}` contra o mesmo com `'section_prefix': []`;
     - `['v1','v2']` contra `['v2','v1']`;
     - tupla contra lista, aceitas em `:241,243`, mas o JSON lido volta como lista;
     - chaves extras.
   - Correção sugerida: normalizar antes de validar, comparar e persistir. Usar versões únicas e ordenadas, `section_prefix` explícito como lista (vazia por padrão) e rejeitar chaves desconhecidas. Acrescentar um teste com escopo equivalente reordenado ou sem `section_prefix`, que precisa ser recusado com a mensagem `progresso`.

3. **[Baixo, não bloqueante] O rate limit depende de READ COMMITTED.** Arquivo: `chat.py:213-217`.
   - A contagem depois do advisory lock só enxerga o slot confirmado pela outra conexão porque, em READ COMMITTED, cada comando tira um snapshot novo.
   - Em REPEATABLE READ ou SERIALIZABLE, o snapshot é tirado no primeiro comando, que aqui é o próprio `pg_advisory_xact_lock`. Assim a segunda transação não veria o slot da primeira, e com `limit=1` as duas poderiam receber `True` (em SERIALIZABLE, possivelmente erro de serialização).
   - O engine padrão usa READ COMMITTED (`config.py:48`), então o comportamento atual está correto.
   - Sugestão: documentar o requisito ou recusar explicitamente outro nível (`SHOW transaction_isolation`).

4. **[Baixo, não bloqueante] Corrida no downgrade entre a verificação e o DROP.** Arquivo: `0004_chat.py:181-184`.
   - A checagem `EXISTS` não trava as tabelas. Uma inserção concorrente confirmada depois da checagem e antes do `DROP TABLE` seria descartada.
   - O risco é operacional (downgrade com a aplicação ativa).
   - Sugestão: `LOCK TABLE <todas> IN ACCESS EXCLUSIVE MODE` antes das checagens.

## Observações (sem ação obrigatória)

- **A revalidação usa a revisão lida na hora, não a da recuperação.** `observed_revision` é calculado no próprio `save_turn` (`chat.py:117-119`) porque `RetrievalCandidate` não carrega revisão. Um ciclo revogar e depois liberar de novo entre E5 e E12 não seria detectado. O texto do chunk é imutável, então o impacto é pequeno. Se T-018 expuser a revisão vista, `save_turn` pode passá-la adiante.
- **`chat_rate_event` cresce sem limpeza** e, por desenho, impede qualquer downgrade depois do primeiro uso. Isso é coerente com "não apagar", mas vale registrar como decisão operacional.
- **`get_item_lineage` depois de revogação.** Continua devolvendo a linhagem de um item já entregue. Os `ResolvedSpan` só trazem offsets e metadados, sem texto canônico novo, então não reexpõem conteúdo revogado.
- **Warning de Pydantic.** O warning em `test_chat.py:118` (`model_copy` com `status` em string) é da fixture. `save_turn` revalida antes de gravar (`chat.py:25,58`).
- **Testes antigos com `head == 0003`.** Os de `test_ressalvas_persistencia.py` precisam ser migrados para a nova head 0004, como `resultado.md` já registra.

## Não executado

- Suítes, focal, testes concorrentes sugeridos e migrações: **Não executado** (o brief proíbe rerodar; leitura apenas).
- Integração E1–E12, CLI e entrega depois do commit: **Não se aplica** a esta revisão; pertence à T-021.

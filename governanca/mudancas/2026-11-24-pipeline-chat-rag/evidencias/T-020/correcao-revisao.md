# T020 — correção dos achados da revisão Anthropic

Data: 2026-11-25. Executor efetivo OpenAI, worker `task_491f2d176b6a`, dispatch `ctx_2293e96364d2`. Escopo extra autorizado: somente método/barreira/lock necessário em `repository.py`. A revisão anterior **Não aprovado nesta rodada**, a focal inicial com 10 erros e a focal complementar com 10 passaram permanecem intactas nos respectivos artefatos. Este documento não substitui o parecer de outro provedor.

## Achado 1 — publicação e ordem de locks

Correção implementada na barreira canônica `LineageRepository.authorize_candidates(owner_id, candidate_ids, observed_revision=None)`; sem critérios paralelos no chat. A barreira descobre versões por chunks imutáveis, trava todas as reservas por document_version_id em ordem determinística com FOR SHARE e só depois trava chunks por chunk_id com FOR SHARE. A ordem vale para o conjunto inteiro, inclusive quando precisa dividir consultas em lotes de 128; não alterna reserva/chunk por lote. O filtro de owner existe em cada consulta.

A fase é conferida na linha de reserva já travada, e os locks sobrevivem ao savepoint até o commit/rollback externo. `reserve`, `publish_ready`, `record_index_ack` e `append_assessment` fenced já usam reserva antes de chunk; seus métodos não foram alterados. `begin_reconciliation` trava document_version antes da reserva, mas a barreira não pede lock conflitante em document_version, portanto não cria a volta desse ciclo. Reclassificação sem fencing precisa de lock exclusivo no chunk e também espera o chat confirmar.

O chat remove os prelocks de chunk e pede a revisão atual à mesma barreira. O novo valor opcional None de observed_revision é usado somente para essa revalidação E12; passar dict mantém o comportamento anterior de rejeitar revisão divergente. O retorno continua dict[chunk_id, ChunkRecord], sem commit implícito.

Regressões de consumidor em duas conexões reais: `test_chat_holds_publication_and_decision_until_caller_commit` cobre begin_reconciliation/reserve/append_assessment. A segunda conexão encontra SQLSTATE 55P03 por lock_timeout enquanto o chat não confirma, não vê o turno pendente, e consegue operar e ler a resposta completa após commit. `test_chat_waits_before_chunk_lock_when_reconciliation_owns_reservation` cobre a ordem inversa: writer detém reserva, chat espera nela (confirmado por pg_blocking_pids), writer ainda consegue reclassificar chunk com fencing e confirmar sem deadlock; chat então rejeita o contexto e não grava turno.

## Achado 2 — escopo e cursor canônicos

`ChatLineageRepository.canonicalize_scope(owner_id, scope)` valida owner e versões, rejeita chaves extras e retorna exatamente `{document_version_ids: versões únicas ordenadas, section_prefix: lista explícita}`. Ordem hierárquica da seção é preservada, e IDs são tratados literalmente, sem trim que pudesse confundir identidades. Versão de outro owner é rejeitada antes de comparar ou gravar estado.

save_continuation usa esse escopo para validação, comparação de progresso e persistência. get_continuation também normaliza o formato anterior já persistido, impedindo reset por omissão de seção, tupla/lista, permutação ou duplicação de versões. used_chunk_ids é ordenado e continua sem duplicados; quantidade cumulativa e conjunto de chunks usados não podem diminuir no mesmo escopo semântico. O teste anterior de representação do escopo foi fortalecido para exigir section_prefix explícito; nenhum assert de perda de progresso foi relaxado.

O cursor JSON é copiado, não altera o objeto do chamador, e rejeita valores não finitos. Quando contém protocolo de recuperação v1, exige version/binding/used_chunk_ids/remaining, valida o binding contra owner + versões + seção + modo do turno realmente persistido nessa sessão e converte somente bindings semanticamente equivalentes para a ordem canônica. Um binding de outro owner não é aceito. Cursors opacos anteriores sem protocolo continuam preservados; a barreira de escopo/owner/progresso é a mesma. T021 deve chamar canonicalize_scope antes de montar RetrievalScope e passar state['cursor'] ao continuar.

Regressões: três codificações equivalentes não permitem quantity8 -> 0 nem apagar c-v1; estado legado sem section_prefix também não permite reset; chaves desconhecidas e versão de outro owner são rejeitadas; cursor gerado com ordem equivalente é canonicalizado e binding de outro owner é rejeitado sem alterar o estado salvo.

## Evidência e limites

- Focal afetada: **Passou**, `19 passed, 1 warning in 5.21s`, em `pytest-correcao-revisao.txt`; uma execução após as correções. Inclui todos os 10 casos anteriores e nove regressões/parametrizações novas.
- Warning Pydantic anterior da fixture de erro permanece; não é falha e save_turn revalida o modelo antes do banco.
- PostgreSQL descartável por teste (`test_t020_*`); nenhuma migração na aplicação compartilhada e nenhum serviço/modelo externo chamado.
- AST dos três arquivos alterados: **Passou**. LSP Python: **Bloqueado**, pyright/pylsp ausentes; referências locais dos consumidores/escritores inspecionadas. Sem nova dependência.
- Achado não bloqueante 3: rate limit requer READ COMMITTED, nível do engine padrão; requisito documentado em interfaces.md. Nenhuma alegação de suporte a outro isolamento foi adicionada.
- Achado não bloqueante 4: permanece o risco operacional de downgrade concorrente com aplicação ativa; 0004 não foi alterada neste dispatch. Downgrade deve ocorrer com consumidores quiescentes; não há autorização para apagar história.
- Revisão independente desta correção: **Não executado**. G4: **Não executado**. A passagem dos testes não é autoaprovação.
- Sem alterações em contratos, ingestion, dependências/uv.lock, migrações 0001–0004, T017 ou casos G3 T001; sem API externa, commit, push ou deploy.

- Validador desta correção: Passou, governança OK (6 mudanças, 21 tarefas, 28 requisitos), em `evidencias/T-020/validador-correcao-revisao.txt`.

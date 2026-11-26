# T-020 — interfaces concretas E12

Executor OpenAI, Orca `task_01914620619c`, dispatch `ctx_8af50d85ac08`; revisão Anthropic pendente.

```python
from training_lineage.chat import ChatLineageRepository
repo = ChatLineageRepository(connection)
repo.save_turn(turn, plan, run, items, citations, verdicts, response, provenance=None)
repo.get_item_lineage(owner_id, item_id)
repo.acquire_chat_rate_slot(owner_id, limit, window_seconds)
repo.save_continuation(owner_id, session_id, *, chat_turn_id, scope, cursor,
                       used_chunk_ids, quantity)
repo.get_continuation(owner_id, session_id)
repo.canonicalize_scope(owner_id, scope)
```

`turn`: ChatTurnRecord; `plan`: SearchPlanRecord | None (SearchPlan sem IDs não serve); `run`: RetrievalRunRecord | None; `items`: list[ItemRecord]; `citations`: list[CitationRecord]; `verdicts`: list[VerdictRecord]; `response`: ChatResponse. Todos contratos existentes; validação reexecutada mesmo para model_copy. Nenhum contrato paralelo. IDs do turno e trace_id na resposta são obrigatórios e idênticos. Plano obrigatório quando há recuperação. IDs e conteúdo entregue/omitido devem coincidir entre os registros e resposta. Itens entregues exigem últimas aprovações E9/E10. Candidates e citações têm FKs/ownership, IDs e status no JSON são conferidos. Fontes são exatamente as dos itens entregues.

`provenance` é dict[citation_id, CitationSpanSelection] obrigatório para toda citação, com offsets canônicos pré-escape. Não escolhe primeira ocorrência de evidência repetida. Resolver usa a proveniência persistida T010. Source.page é None quando spans não concordam ou provenance é unknown; section_path vazio se ambígua, nunca página inicial do chunk como substituto. Antes da gravação a barreira autoritativa `authorize_candidates` revalida publicação/decisão atual dos selecionados e mantém share locks nas reservas de publicação antes dos chunks, até commit. Reclassificações tomam lock exclusivo do mesmo chunk. Candidatos não selecionados podem documentar rejeição; todos precisam pertencer ao owner.

`get_item_lineage` retorna None para item ausente, omitido ou outro owner. Para entregue retorna `{'lineage': ItemLineage, 'spans': {citation_id: tuple[ResolvedSpan, ...]}}`. Os spans incluem start/end/span_id/canonical_id/page/section_path/provenance e são reconferidos contra fonte canônica imutável.

`save_continuation` recebe scope explícito JSON com document_version_ids e section_prefix opcional; cursor é dict | None, podendo ser exatamente RetrievalResult.continuation. used_chunk_ids é lista cumulativa e deve coincidir com cursor.used_chunk_ids quando este existir; quantity é quantidade cumulativa não negativa. Chunks devem pertencer ao owner e escopo. Mesmo scope não pode perder chunks/quantidade. `get_continuation` retorna None ou dict(scope,cursor,used_chunk_ids,quantity,chat_turn_id); bloqueia por owner/session até fim da transação, inclusive quando ainda não existe estado. Passe state['cursor'] em RetrievalScope.continuation, sem reconstruir cursor no início. Escopo novo pode iniciar novo progresso.

`acquire_chat_rate_slot` retorna bool. Limite e janela positivos. Janela deslizante com clock_timestamp após lock transacional de owner; duas conexões não excedem o limite. Confirmar a transação curta do slot antes de LLM, inclusive quando depois houver recusa/erro; rollback libera reserva. Não manter transação de rate aberta durante chamadas externas.

save_turn usa savepoint que remove fragmentos em erro de inserção, sem commit/rollback da transação externa. save_turn e save_continuation devem participar da mesma transação do chamador; só entregar depois do commit. Se commit falhar não entregar. Migração 0004 só adiciona tabelas/triggers; downgrade com qualquer história/estado/slot persistido falha antes de DROP. 0001–0003 não são alteradas. Nenhuma migração no PG compartilhado.

## Exemplo de objetos canônicos

Exemplo de construção com valores da fixture descartável de `packages/lineage/tests/test_chat.py`; não representa chamada de juiz real. A aplicação recebe IDs/modelos/prompt versions e decisões reais de etapas anteriores, sem substituir estes valores pelos de teste.

```python
turn = ChatTurnRecord(chat_turn_id='turn', session_id='session', user_id='owner',
    prompt='input', status='ok', intencao='gerar_flashcards', modo='consulta',
    input_verdict={'malicioso':False, 'motivo':'seguro'}, trace_id='trace', created_at=now)
plan = SearchPlanRecord(search_plan_id='plan', chat_turn_id='turn', objetivos=['Eco'],
    bloom_level='lembrar', consultas=['Eco'])
run = RetrievalRunRecord(retrieval_run_id='run', chat_turn_id='turn', modo='consulta',
    candidates=[{'chunk_id':'c-v1','selected':True}])
verdicts = [
    VerdictRecord(verdict_id='e9',item_id='item',origem='E9',aprovado=True,created_at=now),
    VerdictRecord(verdict_id='e10',item_id='item',origem='E10',aprovado=True,
        fundamentado=True,conforme_rubrica=True,judge_model=judge_model_used,created_at=now),
]
provenance = {'cite':CitationSpanSelection(chunk_id='c-v1',canonical_id='can-v1',
                                         start=6,end=9,evidence='Eco')}
# ItemRecord, CitationRecord e ChatResponse completos: função success() no teste.
with engine.begin() as connection:
    repo=ChatLineageRepository(connection)
    repo.save_turn(turn,plan,run,items,citations,verdicts,response,provenance)
    repo.save_continuation('owner','session',chat_turn_id='turn',
        scope={'document_version_ids':['v1'],'section_prefix':[]},
        cursor=retrieval_result.continuation,
        used_chunk_ids=retrieval_result.continuation['used_chunk_ids'],quantity=8)
# Somente aqui response pode ser entregue.
```

E1 não exige objetos de E2/E3: recusa ChatTurnRecord(status='recusa', motivo_recusa='limite_de_taxa', intencao=None, modo=None, input_verdict=None) com response equivalente, plan=None, run=None, itens/citações/vereditos vazios. Status erro também é persistido sem fabricar classificação. Modelos e prompts usados permanecem no registro canônico do turno.


## Atualização após revisão — ctx_2293e96364d2

Assinaturas anteriores de save_turn/save_continuation/get_continuation preservadas. `canonicalize_scope(owner_id, scope) -> dict` é a entrada pública para T021 obter versões únicas/ordenadas e section_prefix lista explícita antes de construir RetrievalScope. Só aceita document_version_ids e section_prefix; cada versão precisa pertencer ao owner. save/get_continuation também canonicalizam escopos equivalentes e formatos anteriores, de modo que permutação/duplicação/tupla/seção omitida não reiniciam progresso. Prefixos de seção mantêm a ordem hierárquica. Chaves extras são erro.

Cursor de recuperação v1 é validado contra owner/escopo/modo do turno salvo, e binding equivalente é convertido para a ordem canônica. used_chunk_ids é ordenado; use state['cursor'] integralmente na retomada. Binding de outro owner é erro. Cursor opaco sem protocolo permanece disponível para formatos sem binding, sempre sujeito à validação de owner/escopo e progresso cumulativo; não é convertido em cursor de recuperação artificial.

A barreira compartilhada agora é `LineageRepository.authorize_candidates(owner_id, candidate_ids, observed_revision=None) -> dict[chunk_id, ChunkRecord]`. Dict explícito exige revisão observada como antes; None pede revisão atual sob locks para E12. Primeiro trava todas as reservas por versão e depois chunks por ID, conservando ambos até fim da transação. Não adquirir locks de chunks antes dessa barreira. Nenhum método escritor mudou. Interface comunicada ao principal em msg_803f4ef5d009.

Rate slot: executar em transação READ COMMITTED (padrão de create_lineage_engine); snapshots de REPEATABLE READ não são suportados pela contagem após advisory lock. Downgrade requer aplicação quiescente; corrida operacional do parecer anterior não foi alterada nesta correção. Revisão independente da correção permanece pendente, conforme correcao-revisao.md.

# Revisão independente (Google) — T-014 e Reavaliação de T-013 (`2026-11-24-correcao-integral-ressalvas`)

- **Tarefas avaliadas**:
  1. **T-014** (`2026-11-24-correcao-integral-ressalvas`) — Reconciliar índice e fechar leitura segura (`ING-REP-001`, `ING-LEG-001`, `ING-OPS-001`, `ING-AUD-001` / Casos `C13`, `C14`, `C16`)
  2. **Reavaliação explícita dos achados de T-013** (Achado 1: cutover de caller residual de `_PartialIngestor` em `test_ingestion.py`; Achado 2: cobertura de casos de `ING-LEG-001` / `C14`)
- **Provedor efetivo do autor (mantido)**: `openai` (`rag-backend`, dispatch `ctx_df5af613a5b9` para T-014 e `ctx_d18611b3f44b` para T-013)
- **Provedor efetivo da revisão (técnica + `seguranca-llm`)**: `google` (Antigravity / Gemini 4 Argon; dispatch `ctx_0501a76d93dc`, task `task_c8b07844f4e5` — fallback efetivo pois `anthropic`/Opus segue indisponível e `openai` é o autor)
- **Relatório separado `seguranca-llm`**: `evidencias/T-014/revisao-seguranca-llm-google.md`
- **Relatório de reavaliação em T-013**: `evidencias/T-013/reavaliacao-r2-google.md` (preservando intacto o relatório original da rodada 1 em `evidencias/T-013/revisao-google.md`)
- **Snapshot/revisão exata de leitura**:
  - `packages/ingestion/src/training_ingestion/reconciliation.py` (282 linhas, `sha256: d1f5f627fc6b106a9a75f1dcd02d85e1f627f8e0d24c5bd9ae76db16b0399dbe`)
  - `packages/ingestion/src/training_ingestion/indexing.py` (273 linhas, `sha256: d5ef08f7b42fefdc60ce1c70cc7ad972a7fbf7cf1a83e465f92f708521c35b27`)
  - `packages/ingestion/src/training_ingestion/retrieval/eligibility.py` (72 linhas, `sha256: 39cd1767994b52d7f14de7f5128edebee9ba6f32fd0f4c4fb9d008c93ead024e`)
  - `packages/lineage/src/training_lineage/repository.py` (638 linhas, `sha256: 9c804d5f9a1fbd34a37d3eb8d51e10ad60ed5ef2d4db62854bd88c0009aefca1`)
  - `packages/ingestion/tests/test_ressalvas_reconciliacao.py` (317 linhas, `sha256: 58805f6b8f3b395d6e5936e49203c8645a422114490fb19a1abdca1eb9361582`)
  - `packages/ingestion/tests/test_ingestion.py` (313 linhas, `sha256: 4f779f336881090b896f371dc5cdf08b155d7d5aeb9597539d042333524d6d99`)
- **Veredito para T-014**: **aprovado** (no escopo de reconciliação retomável, payload derivado com revisão/evento/hash, remoção escopada de pontos bloqueados, barreira `SafeRetriever.search` com autorização autoritativa no Postgres, inventário dry-run e remoção restrita a fixtures comprovadas; 0 achados bloqueantes).
- **Veredito da reavaliação de T-013 (Rodada 2)**: **aprovado** (ambos os achados da Rodada 1 foram integralmente sanados sem reintroduzir shim `_PartialIngestor`; reprovação histórica da Rodada 1 preservada).

---

## 1. Reavaliação explícita dos achados de T-013 (`arquivo:linha`)

### 1.1. Achado 1 de T-013 — Cutover completo de callers de `_PartialIngestor` para `Ingestor` público sem shim (**Resolvido**)

- **Verificação no código (`packages/ingestion/tests/test_ingestion.py:14-18,24-72,115-124,126-143,195-219,227-250,302-306`)**:
  - `grep -rn "_PartialIngestor" packages/` confirma que não resta **nenhum** import ou uso em código Python (`packages/*/src` e `packages/*/tests`).
  - `test_ingestion.py:14-17` importa diretamente a entrada pública `Ingestor` de `training_ingestion` (que exige `processor` e delega a `DurableIngestionProtocol` em `core.py:314-336`). Nenhum shim ou classe paralela foi reintroduzido em `core.py`.
  - Uma fixture `processor` (`test_ingestion.py:61-72`) conecta um container Qdrant real descartável (`test-ingestion-qdrant-<uuid>`, `:39-58`) e um dublê de fronteira `_ExternalModels` restrito ao arquivo de teste (`:24-37`).
  - Todos os testes de ingestão (`test_ING_FMT_001_refuses_before_write` `:121`, `test_ING_REC_001_reuse_without_parsing_and_owner_isolation` `:127`, `test_ING_FMT_001_parser_failure_writes_nothing` `:199`, `test_ING_REC_001_concurrent_duplicate` `:207`, `test_ING_FMT_001_ING_CHK_001_persist_real_formats` `:229`, `test_ING_REC_001_limits_and_chunk_failure_are_atomic` `:243-244`) passam pela API pública `Ingestor(engine, processor=processor)` e todos os 26 itens de `test_ingestion.py` passaram na execução focal (`evidencias/T-014/pytest.txt:9`).
  - *(Nota editorial menor, não bloqueante)*: `packages/ingestion/README.md:102` ainda contém uma frase descritiva histórica mencionando `core._PartialIngestor`; não afeta código, contratos ou testes.

### 1.2. Achado 2 de T-013 — Cobertura completa de `ING-LEG-001` / `C14` (**Resolvido**)

- **Verificação no código e testes (`packages/ingestion/src/training_ingestion/reconciliation.py:103-116,203-223`, `packages/ingestion/tests/test_ressalvas_reconciliacao.py:193-250`)**:
  - **Sentido `blocked → allowed` com preservação byte a byte do histórico (`test_C14_blocked_to_allowed_reconciles_before_public_read`, `:193-213`)**:
    - Ingere chunk inicialmente bloqueado pelo juiz (`pipeline.model_client.block=True`), reconcilia (confirmando `count == 0` no Qdrant, `:200`), reclassifica via `Ingestor(engine, processor=pipeline).reclassify(...)` com `block=False` (`:201-203`), verifica que `SafeRetriever(engine, pipeline).search('owner', 'membranas')` continua retornando `[]` **antes** da reconciliação vetorial (`:204-205`), executa `Reconciler(engine, pipeline).reconcile_version(...)` (`:206`), comprova que `SafeRetriever.search` passa a devolver o `ChunkRecord` (`:207-208`) e comprova que `DocumentVersionRecord` e `ChunkRecord` no Postgres permanecem byte a byte idênticos aos originais (`:209-212`).
  - **Erro/timeout do juiz na reclassificação (`test_C14_reclassification_error_and_legacy_missing_input_fail_closed`, `:215-226`)**:
    - Parte de versão já ingerida, reconciliada e `ready`; força falha do juiz (`fail_judge=True`) durante `Ingestor.reclassify(...)`, que lança `IngestionStageError` (`:220-221`), grava evento final `stage='decision', outcome='unavailable'` sem persistir o texto sensível da exceção (`'sensitive fixture diagnostic' not in repr(history)`, `:224-226`) e faz `SafeRetriever.search('owner', 'membranas')` retornar `[]` (`:223`).
  - **Origem legada sem `CanonicalTextRecord` / `ChunkProvenanceRecord` / `EmbeddingInputRecord` (`test_C14_reclassification_error_and_legacy_missing_input_fail_closed`, `:228-250`)**:
    - Insere diretamente no Postgres apenas `DocumentVersionRecord` e `ChunkRecord` legados sem canônico nem entrada de embedding (`:229-240`).
    - Ao chamar `Reconciler(engine, pipeline).reconcile_version(owner, version_id)` (`:241`), `reconcile_version` (`reconciliation.py:105-115`) não inventa título vazio nem reconstrói fonte por concatenação: grava `mark_legacy_insufficient` (`mapping_status='insufficient'`), anexa `[triage, decision]` com `outcome='unavailable', reason_code='legacy_insufficient'` (`_append_legacy_unavailable`, `:203-222`), transiciona o job para `state='blocked', error_code='legacy_insufficient'` e a reserva para `phase='failed', error_code='legacy_insufficient'` (`_fail`, `:165-175`), retorna `{'state': 'blocked', 'error_code': 'legacy_insufficient', ...}` e mantém `authorize_candidates` retornando `{}` (`test_ressalvas_reconciliacao.py:242-249`).

---

## 2. Análise técnica de T-014 por invariante (`arquivo:linha` / símbolo)

### 2.1. Reconciliação retomável, idempotência, cursor, fencing, ack e reprodução exata de embedding (`ING-REP-001` / `C13`)

- **Fencing e job monotônico no banco (`packages/lineage/src/training_lineage/repository.py:441-479`, `packages/ingestion/src/training_ingestion/reconciliation.py:52-65`)**:
  - `LineageRepository.begin_reconciliation` (`repository.py:441-478`) trava a versão (`_owned_version(..., lock=True)`) e a reserva (`with_for_update()`) em savepoint curto: se houver reserva ativa não terminal (`phase not in ('ready', 'failed') and lease_until > now`), retorna `'busy'`; caso contrário, cria ou incrementa `generation = reservation.generation + 1` em `phase='persisted'` e cria um `ReconciliationJob` com `desired_revision = (latest.desired_revision + 1) if latest else 1`.
  - `Reconciler.reconcile_version` (`reconciliation.py:52-150`) transiciona o job para `'running'` e a reserva para `'indexing'`, lê todos os chunks da versão em páginas ordenadas por `chunk_id` via `list_reconciliation_chunks(..., cursor=cursor, limit=128)` (`:67-73`) e renova a lease (`_renew`, `:45-50`) antes e depois de cada chamada externa.
  - Se a lease expirar ou outra geração assumir, `_renew` ou as escritas fencadas levantam `ReconciliationLeaseLost` (`:49-50,151-152,161-162`), impedindo que o worker antigo grave `record_index_ack`, avance `checkpoint_job` ou chame `publish_ready` (`test_C13_expired_worker_cannot_ack_or_publish`, `test_ressalvas_reconciliacao.py:175-190`).
- **Reprodução exata da entrada de embedding e payload com revisão/evento/hash (`reconciliation.py:117-128`, `indexing.py:124-169`)**:
  - Para cada chunk com `decision.allowed is True`, `reconcile_version` reconstrói `formatted = document_input(entry.title, list(entry.section_context), chunk.text)`, chama `entry.validate_input(chunk.text, formatted)` (conferindo `text_sha256` e `input_sha256` contra o registro imutável persistido na linhagem, `:118-119`), gera o vetor via `self.model.embed_document(...)` e invoca `self.index.upsert(chunk, vector, owner_id=owner_id, decision_event_id=decision.decision_event_id, decision_revision=decision.revision, input_sha256=entry.input_sha256)` (`:120-125`).
  - `QdrantIndex.ensure_collection` (`indexing.py:124-142`) cria e valida índices de payload para `document_version_id` (`KEYWORD`), `owner_id` (`KEYWORD`), `chunk_id` (`KEYWORD`), `injection_flagged` (`BOOL`), `decision_revision` (`INTEGER`) e `input_sha256` (`KEYWORD`).
  - `QdrantIndex.upsert` (`indexing.py:149-168`) valida `type(decision_revision) is int and decision_revision >= 1 and bool(input_sha256)` quando `decision_event_id is not None` e grava `decision_event_id`, `decision_revision` e `input_sha256` no payload do ponto UUIDv5 determinístico (`point_id(chunk_id)`).
- **Remoção escopada de bloqueados, verificação em lote, `record_index_ack` e cursor (`reconciliation.py:126-150,177-201`, `indexing.py:170-210`)**:
  - Para chunks bloqueados (`not decision.allowed`), `reconcile_version` acumula `blocked_ids` e chama `self.index.delete_owned_points(owner_id=owner_id, version_id=version_id, chunk_ids=blocked_ids)` (`reconciliation.py:129`).
  - `QdrantIndex.delete_owned_points` (`indexing.py:189-210`) e `delete_owned_point` (`:170-187`) primeiro recuperam os pontos existentes por ID, conferem que `owner_id`, `document_version_id`, `chunk_id` e `embedding_key` no payload pertencem exatamente ao escopo autorizado (levantando `ValueError('Ponto do índice fora do escopo autorizado')` se divergir) e executam `client.delete` com `FilterSelector` conjuntivo (`owner_id` + `document_version_id` + `embedding_key` + `chunk_id`).
  - Em seguida, `_verify_batch` (`reconciliation.py:131,177-200`) faz uma única chamada `client.retrieve` para o lote (até 128 IDs) com `with_payload=True, with_vectors=True`, confirmando ausência de chunks bloqueados e igualdade exata de payload (incluindo `decision_event_id`, `decision_revision`, `input_sha256`, `owner_id`, `embedding_key`) e presença de vetores `dense` (3072 floats não nulos) e `bm25` (`SparseVector`) para chunks permitidos.
  - Só após `_verify_batch` passar é que `record_index_ack` é gravado para cada chunk do lote e `checkpoint_job` avança `cursor = chunk.chunk_id` (`reconciliation.py:132-142`), publicando `publish_ready` apenas ao final de todos os lotes (`:143-150`).
  - Provado em `test_C13_repairs_projection_twice_and_reuses_point_ids` (`test_ressalvas_reconciliacao.py:127-145`) e `test_C13_upsert_without_ack_is_inaccessible_then_resumes` (`:147-173`).

### 2.2. Barreira de leitura segura consumer-visible e falha fechada com Postgres indisponível (`ING-AUD-001`, `ING-REP-001` / `C13`, `C16`)

- **Implementação de `SafeRetriever.search` (`packages/ingestion/src/training_ingestion/retrieval/eligibility.py:11-72`)**:
  - Não é um helper desacoplado: `SafeRetriever(engine, processor).search(owner_id, query, *, limit=10)` executa o fluxo completo de consulta:
    1. Valida `owner_id`, `query` e `1 <= limit <= 1000` (`:19-22`);
    2. Gera o vetor de busca via `self.model.embed_query(query)` (`:23-25`);
    3. Consulta o Qdrant (`client.query_points`, `:26-32`) com filtro obrigatório `must=[owner_id == owner_id, embedding_key == settings.embedding_key, injection_flagged == False]`;
    4. Descarta qualquer ponto cujo payload no Qdrant não traga `chunk_id` (`str`), `decision_revision` (`int` estrito), `decision_event_id` não vazio e `input_sha256` (`str`) (`:34-43`);
    5. Abre conexão com o Postgres (`:46-54`) e consulta `repo.authorize_candidates(owner_id, candidates.keys(), candidates)`, `repo.get_effective_decisions(owner_id, candidates.keys())` e `repo.load_embedding_inputs(owner_id, candidates.keys(), embedding_key=...)`. Qualquer falha de conexão ou erro do Postgres é capturada e relançada como `raise EligibilityUnavailable('Linhagem indisponível para autorizar candidatos') from None` (`:53-54`), sem fallback permissivo;
    6. Para cada candidato autorizado pelo Postgres (`:56-71`), confere ainda a consistência cruzada entre o payload retornado pelo Qdrant e os registros autoritativos do Postgres (`document_version_id`, `text`, `section_path`, `owner_id`, `decision.allowed`, `decision_event_id`, `decision_revision` e `input_sha256`), devolvendo o `ChunkRecord` da linhagem.
  - Provado em `test_C14_blocked_to_allowed_reconciles_before_public_read` (`test_ressalvas_reconciliacao.py:204-208`), `test_C16_projection_verifies_all_three_batches_and_scopes_owner` (`:267-269`) e `test_safe_retrieval_fails_closed_when_authoritative_store_fails` (`:312-317`).

### 2.3. Coleção estrita, verificação em lotes 128/128/1, inventário dry-run e remoção restrita a fixtures comprovadas (`ING-OPS-001` / `C16`)

- **Sem `allow_test_collection` e leitura em lotes limitados a 128 (`indexing.py:96-102`, `reconciliation.py:22,96-131`, `test_ressalvas_reconciliacao.py:252-271`)**:
  - `QdrantIndex.__init__` (`indexing.py:96-101`) não possui parâmetro `allow_test_collection` e recusa qualquer `collection_name != settings.embedding_key`.
  - Em `test_C16_projection_verifies_all_three_batches_and_scopes_owner` (`test_ressalvas_reconciliacao.py:252-271`), um documento com 257 chunks (título + 256 parágrafos) é reconciliado enquanto `client.retrieve` é instrumentado, comprovando `calls == [128, 128, 1]` (3 chamadas em lote para 257 chunks, nunca N chamadas por N chunks) e isolamento total contra chunks de `owner-b`.
- **Inventário dry-run e remoção protegida por manifesto/digest/run/quiescência/autorização (`reconciliation.py:224-282`, `test_ressalvas_reconciliacao.py:273-310`)**:
  - `Reconciler.inventory_orphans` (`reconciliation.py:233-254`) apenas lê a coleção via `scroll` paginado em lotes de 128 (`with_payload=True, with_vectors=False`), calcula `payload_digest` SHA-256 sem copiar texto ou vetores para o relatório e só classifica um ponto como `'fixture_proven'` se o `fixture_manifest` tiver `run_id`, `collection`, `source_db_identity` e `digest` válidos **e** `points_map.get(point_id) == digest` (`:235-245`); todos os demais pontos recebem `classification='unknown'` com `fixture_manifest_ref=None`.
  - `Reconciler.execute_orphan_deletion` (`reconciliation.py:256-282`) exige:
    1. Callbacks `quiescence_check` e `authorization` explícitos e `callable` (`:257-258`);
    2. Idade do inventário `0 <= age <= INVENTORY_MAX_AGE_SECONDS` (300 s, `:260,264`);
    3. Integridade do `fixture_manifest` (`fixture_manifest_digest`) e igualdade de `run_id`, `collection` e `source_db_identity` entre inventário, manifesto e índice (`:261-268`);
    4. Retorno verdadeiro de `quiescence_check(scope)` e `authorization(scope)` (`:269`);
    5. Reexecução imediata de `inventory_orphans` confirmando `current['digest'] == inventory['digest']` (abortando se qualquer ponto mudou desde a observação, `:271-273`);
    6. Que **todos** os pontos do inventário submetido sejam `classification == 'fixture_proven'` (`len(deletable) == len(inventory['entries'])`, `:274-276`), e revalidação individual do `payload_digest` antes de cada `client.delete` (`:277-281`).
  - Nenhum dos 15 pontos observados na coleção compartilhada da aplicação foi tocado; o teste `test_orphan_inventory_is_dry_and_delete_requires_fresh_exact_fixture_scope` (`test_ressalvas_reconciliacao.py:273-310`) roda exclusivamente contra o container Qdrant descartável `test-t014-qdrant-<uuid>`.

---

## 3. Achados e limites

- **Achados bloqueantes ou de retrabalho em T-014 (e na reavaliação de T-013)**: **Nenhum (0)**.
- **Observação de escopo / arquitetura registrada**:
  - Como o pacote `packages/retrieval/` ainda não existe no workspace (a etapa E5 da pipeline de chat pertence a mudança futura), o consumidor mínimo autorizado pelo despacho de T-014 foi implementado em `packages/ingestion/src/training_ingestion/retrieval/eligibility.py` (`SafeRetriever` e `EligibilityUnavailable`), sem criar nova distribuição Python nem adicionar dependências ao `uv.lock`. Quando `packages/retrieval/` for criado, deverá consumir ou reexportar essa mesma barreira autoritativa.
- **Limites explícitos desta aprovação**:
  1. Esta revisão aprova **T-014** e a **reavaliação (Rodada 2) de T-013** nos snapshots identificados por SHA-256 acima, preservando o registro histórico da Rodada 1 de T-013.
  2. Não aprova antecipadamente a avaliação dos 9 ataques com juiz LLM real (`T-015`, suspensa até confirmação de rotação da `GEMINI_API_KEY`), o fechamento documental de `T-016`, nem a prova final integrada e diff RAGAS (`T-017`, `Bloqueado` por pré-requisitos).
  3. Não declara encerradas as 16 ressalvas da mudança e não concede nem substitui o Gate G4 humano.

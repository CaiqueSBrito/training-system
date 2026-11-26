# Revisão independente (Google) — Slice de ressalvas `ctx_3017e7ea9815` (T-006 / T-008)

- **Escopo avaliado**: Slice do backend paralelo (`ctx_3017e7ea9815`, implementado por `openai` / `rag-backend`), cobrindo os itens **1, 2, 15 e 16** da matriz de 16 ressalvas (`ING-MIG-001`, `ING-MIG-002`, `SEC-LOG-001`, `ING-OPS-001`) e a parte local de diagnóstico sanitizado (`OBS-DIA-001`, item 10), aplicados a `packages/lineage/src/training_lineage/migration.py`, `packages/llm/src/training_llm/__init__.py`, `packages/ingestion/src/training_ingestion/indexing.py` e seus respectivos testes (`packages/lineage/tests/test_lineage.py`, `packages/llm/tests/test_models.py`, `packages/ingestion/tests/test_indexing.py`).
- **Provedor efetivo da revisão**: `google` (Antigravity / Gemini 4 Argon, dispatch `ctx_d072ae09d0c3`, task `task_705a0f068a0e`)
- **Autor da implementação (mantido)**: `openai` (`rag-backend`, dispatch `ctx_3017e7ea9815`)
- **Motivo do despacho para Google**: Independência de provedor efetivo (`governanca/papeis/README.md`: implementação `openai` e fallback `GPT-6.1 Sol` também `openai` exigem revisão independente por `google`).
- **Veredito da slice local (itens 1, 2, 15, 16 e diagnóstico local)**: **aprovado na slice local, com bloqueios externos registrados** (sem declarar o lote completo das 16 ressalvas aceito e sem conceder Gate G4).

---

## 1. Histórico de provas preservado (sem reexecução desnecessária)

1. **Execução focal inicial (`pytest-slice-ressalvas.txt`, sanitizado)**: **Falhou** (`60 passed, 22 failed, 8 errors, 1 deselected` em 10,13 s). Nessa execução:
   - Todos os **50 testes de `packages/lineage/tests/test_lineage.py` passaram** (`pytest-lineage-ressalvas.txt`).
   - As falhas fora de `lineage` decorreram da nova guarda de logging que recusou `litellm.input_callback = ['cache']` instalado pelo próprio LiteLLM ao importar o contexto Gemini, além de um incidente de exposição de credencial no traceback de argumentos do pytest.
2. **Diagnósticos offline (`diagnostico-offline-ressalvas.txt` e `diagnostico-provider-offline-ressalvas.txt`)**: identificaram sem uso de rede que o provider nativo do Gemini no LiteLLM instancia `Cache(LOCAL)` apenas para `get_cache_key`, populando `input_callback = ['cache']` mesmo com `litellm.cache is None`.
3. **Complemento local offline/Qdrant (`pytest-complemento-local-ressalvas.txt`)**: **Passou** (`41 passed, 2 deselected` em 6,33 s com `LITELLM_LOG=ERROR`), validando todos os testes locais de `packages/llm/tests/test_models.py` e os 8 nós de Qdrant em `packages/ingestion/tests/test_indexing.py` sem repetir `lineage` e sem chamar APIs externas.
4. **Conformidade desta revisão**: Nenhum teste, suíte ou chamada real foi executado; o artefato bruto anterior à sanitização não foi aberto e nenhuma variável de ambiente/credencial foi impressa; Langfuse permaneceu desligado; o trabalho em andamento da `T-010` não foi auditado nem tocado.

---

## 2. Avaliação técnica por item da slice (com `arquivo:linha`)

### 2.1. Item 1 (`ING-MIG-001`) e Item 2 (`ING-MIG-002`): Suporte a upgrade/downgrade intermediário sem dano, CHECKs e triggers (`packages/lineage`)
- **Resolução estrita de revisão e direção por ancestralidade**:
  - `packages/lineage/src/training_lineage/migration.py:23-32`: `migrate(engine, target="head")` valida que `target` é `str` não vazia sem espaços laterais (`:24`), resolve `destination` via `script.get_revision(target)` quando `target != 'base'` (`:26`), e exige correspondência exata `destination.revision == target` quando `target != 'head'` (`:27-29`). Prefixos parciais (`'000'`), deslocamentos relativos (`'-1'`, `'+1'`), intervalos (`'0001:0002'`), payloads SQL (`'head;DROP TABLE chunk'`), string vazia e `None` são rejeitados com `ValueError('Revisão de migração inválida') from None` antes de qualquer DDL.
  - `packages/lineage/src/training_lineage/migration.py:33-54`: dentro de `with engine.begin() as connection:`, obtém `heads = MigrationContext.configure(connection).get_current_heads()`, recusa múltiplos heads (`:35-36`), retorna sem mutação se `current == target_id` (`:41-42`), calcula `ancestors_target` e `ancestors_current` via `script.walk_revisions` (`:43-44`) e seleciona `command.upgrade` ou `command.downgrade` (`:45-48`) atomicamente na mesma conexão (`config.attributes["connection"] = connection`, `:53-54`).
- **Ciclo de vida de triggers, funções e preservação de dados**:
  - `packages/lineage/tests/test_lineage.py:245-275` (`test_ING_LIN_001_intermediate_roundtrip_and_trigger_lifecycle`): exercita em Postgres real descartável a sequência `base → 0001 → 0002 → 0001 → head (0002) → 0002 (no-op) → base`.
  - Comprova que: (a) dados inseridos em `0001` sobrevivem ao upgrade para `0002` e bloqueiam `UPDATE`/`DELETE`/`TRUNCATE` nas duas tabelas (`:247-256`); (b) o downgrade intermediário `0002 → 0001` remove `reject_document_version_mutation` e os triggers `document_version_immutable_%` enquanto preserva os 2 triggers `chunk_immutable_%` e os registros existentes (`:257-266`); (c) o re-upgrade a `head` e chamada idempotente a `'0002'` preservam os dados (`:267-270`); e (d) o downgrade a `'base'` remove ambas as funções (`reject_chunk_mutation`, `reject_document_version_mutation`) e todos os triggers (`:271-274`).
  - `packages/lineage/tests/test_lineage.py:277-288` (`test_ING_LIN_001_invalid_target_preserves_data`): prova que 8 alvos inválidos falham com `ValueError` mantendo `alembic_version == '0002'` e os registros intactos.
- **Exercício individual de todos os CHECK constraints migrados**:
  - `packages/lineage/tests/test_lineage.py:290-314` (`test_ING_LIN_001_each_migrated_check`): 14 casos parametrizados validam por `INSERT` direto que cada violação nas tabelas `document_version` e `chunk` levanta `IntegrityError` com `caught.value.orig.diag.constraint_name == constraint` (`document_version_identifiers`, `document_version_sha256`, `chunk_identifiers`, `chunk_page`, `chunk_offsets`, `chunk_text`, `chunk_injection`, `chunk_section_path`).
- **Observação para integração futura com T-011/T-016**: esta slice valida a mecânica de `migrate()` sobre as revisões `0001` e `0002` existentes; quando a T-011 introduzir a nova revisão aditiva (tabelas de texto canônico/spans/auditoria), os casos `C01`/`C02` de T-011/T-016 deverão estender o roundtrip para a revisão `0003`.

### 2.2. Item 15 (`SEC-LOG-001`) e Diagnóstico Local (`OBS-DIA-001`, Item 10): Guarda DEBUG, callbacks/cache sem falso positivo e sanitização (`packages/llm`)
- **Eliminação da superfície de vazamento via argumentos de frame no traceback**:
  - `packages/llm/src/training_llm/__init__.py:47-50,107-115`: `_guard_sdk_logging()` não recebe `env`, `settings` nem dicionários como parâmetro; lê `os.environ.get(key)` internamente, enquanto `ModelSettings.from_env` (`:47-49`) inspeciona apenas `env.get(key)` para `'LITELLM_LOG'` e `'OPENAI_LOG'` e levanta `InjectionJudgeError('unsafe_logging', setting=key) from None`. Caso uma falha ocorra, o pytest não tem como imprimir o dicionário de variáveis do `.env` nos argumentos do frame.
- **Guarda técnica antes de import/chamada sensível (juiz e embeddings)**:
  - `packages/llm/src/training_llm/__init__.py:107-163`: `_guard_sdk_logging()` é chamada em `ModelSettings.from_env` (`:50`), `GeminiModels.__init__` (`:174`), `GeminiModels._embed` (`:186`) e duas vezes em `GeminiModels.judge_injection` — antes e depois do `import litellm` (`:207-212`), fixando `os.environ.setdefault('LITELLM_LOG', 'WARNING')` antes do import (`:209`).
  - Recusa com `InjectionJudgeError('unsafe_logging', setting=...)`:
    - `LITELLM_LOG` ou `OPENAI_LOG` fora de `{'', 'INFO', 'WARNING', 'ERROR', 'CRITICAL', '20', '30', '40', '50'}` (`:102-104,113-115`);
    - logger root, qualquer logger de `_SDK_LOGGERS` (`LiteLLM`, `LiteLLM Proxy`, `LiteLLM Router`, `litellm`, `openai`, `httpx`, `httpcore`, `google.genai`, `google.auth`) ou filhos registrados em `loggerDict` com `getEffectiveLevel() <= logging.DEBUG` (`:86-91,116-119`);
    - flags do LiteLLM (`set_verbose`, `log_raw_request_response`, `log_client_error_tracebacks`, `log_auth_failure_key_identity`) e `litellm._logging.set_verbose` (`:122-123,130,132-133,147-148`);
    - hooks de `litellm.utils` (`add_breadcrumb`, `capture_exception`, `sentry_sdk_instance`, `user_logger_fn`, `:129,131,149-150`).
  - **Endurecimento ativo e filtro de handlers**: ativa `suppress_debug_info = True`, `turn_off_message_logging = True`, `redact_messages_in_exceptions = True`, `redact_user_api_key_info = True` (`:151-154`), passa `num_retries=0, **{'no-log': True}` em `litellm.completion` (`:232`) e instala `_SDK_FILTER` (`_SuppressSDKRecords`) nos loggers SDK e nos handlers existentes, incluindo do root logger (`:94-99,156-162`), impedindo que até mesmo registros `ERROR` emitidos internamente pelo LiteLLM alcancem handlers da aplicação.
- **Tratamento do callback interno `'cache'` sem falso positivo e sem abrir brecha**:
  - `packages/llm/src/training_llm/__init__.py:124-126,134-146`: verifica todas as 10 listas de callbacks do LiteLLM. Permite **exclusivamente** a string literal `'cache'` (`type(cb) is str and cb == 'cache'`) nas listas `'input_callback'`, `'success_callback'` e `'_async_success_callback'`, e **somente quando `litellm.cache is None`** (`:142-145`). Se `litellm.cache` não for `None` (cache ativo) ou se houver qualquer outro callback (função, objeto ou string), levanta `InjectionJudgeError('unsafe_logging', setting='litellm.' + name)`.
  - `packages/llm/tests/test_models.py:279-305` (`test_ING_INJ_001_offline_native_provider_lifecycle`): comprova com rede bloqueada (`socket.socket.connect`) e `HTTPHandler.post` dublê que duas chamadas consecutivas pelo caminho nativo do provider Gemini deixam `litellm.cache is None` e `litellm.input_callback == ['cache']` na primeira chamada e sucedem na segunda sem falso positivo.
  - `packages/llm/tests/test_models.py:149-202` (`test_ING_INJ_001_unsafe_sdk_logging_fails_before_sensitive_call`): 13 cenários parametrizados (incluindo `active_cache` nas linhas 188–190) provam que a chamada sensível não é executada (`called == []`) e nenhuma sentinela vaza em `traceback`, `caplog`, `stdout` ou `stderr`.
- **Sanitização de diagnósticos e exceções (`InjectionJudgeError` e `_provider_error`)**:
  - `packages/llm/src/training_llm/__init__.py:75-84,165-169,234-242`: `InjectionJudgeError` expõe apenas `kind` (`'provider'`, `'invalid_output'`, `'unsafe_logging'`), `status_code` (somente `int` estrito entre `100` e `599`, `:167`), `retryable` (`status in {408, 425, 429, 500, 502, 503, 504}` quando `status` não é `None`, caso contrário `None`, `:168`) e `setting` (nome constante do controle violado), sempre com mensagem fixa `'Falha na verificação de injeção'` e `from None`.
  - Limite honestamente documentado (`__init__.py:110`): a guarda protege o pipeline antes de entregar dados ao SDK e filtra handlers existentes, mas não controla código externo do chamador que reconfigure handlers concorrentemente ou imprima variáveis locais por conta própria.

### 2.3. Item 16 (`ING-OPS-001`): Remoção do gancho de teste, índices de payload e verificação em lotes no Qdrant (`packages/ingestion`)
- **Remoção de `allow_test_collection` sem bypass substituto no produto**:
  - `packages/ingestion/src/training_ingestion/indexing.py:96-101`: `QdrantIndex.__init__(self, client, settings, *, collection_name=None)` removeu o parâmetro `allow_test_collection` e exige invariavelmente `if self.collection_name != settings.embedding_key: raise ValueError('Nome da coleção diverge da chave de embedding')`.
  - `packages/ingestion/tests/test_indexing.py:55-95`: a fixture `isolated_qdrant` sobe um container Docker descartável (`qdrant/qdrant:v1.20.2`) em porta efêmera de loopback (`127.0.0.1::6333`), sem volumes e com remoção forçada em `finally` (`docker rm -f`), permitindo que todos os testes usem o nome exato da coleção de produção (`gemini-embedding-2@3072@fmt-v1`) sem tocar no container Qdrant compartilhado do ambiente local.
- **Índices de payload criados e verificados**:
  - `packages/ingestion/src/training_ingestion/indexing.py:124-140`: `ensure_collection()` define `expected_indexes` para `document_version_id` (`KEYWORD`), `owner_id` (`KEYWORD`), `chunk_id` (`KEYWORD`) e `injection_flagged` (`BOOL`), recusa índice existente com `data_type` divergente (`:132-133`), cria os ausentes com `wait=True` (`:134-136`) e revalida `get_collection(...).payload_schema` ao final (`:137-140`).
  - `packages/ingestion/tests/test_indexing.py:470-483` (`test_ING_IDX_002_payload_indexes_and_no_test_bypass`): verifica os 4 tipos no schema do Qdrant, testa a recusa de nome de coleção com sufixo `-test-forbidden` e testa a recusa quando `owner_id` é recriado com tipo `INTEGER`.
- **Verificação de versões existentes em lotes de 128 com checagem de duplicatas, ordem, ownership, payload e vetores**:
  - `packages/ingestion/src/training_ingestion/indexing.py:188-219`: `IndexingPipeline.verify_existing(chunks, owner_id)`:
    - recusa `chunks` com `chunk_id` duplicado na entrada (`:190-191`);
    - itera em lotes `range(0, len(chunks), 128)` (`:192-194`) e faz uma única chamada `client.retrieve(..., ids, with_payload=True, with_vectors=True)` por lote (`:195-196`);
    - indexa o retorno por `found = {str(p.id): p for p in points}` (independente da ordem devolvida pelo Qdrant) e valida cardinalidade e subconjunto (`:197-199`);
    - para cada chunk do lote: se `chunk.injection_flagged`, exige `point is None` (`:202-205`); se não marcado, exige que `point.payload` seja exatamente igual a `{**chunk.model_dump(), 'owner_id': owner_id, 'embedding_key': self.index.settings.embedding_key}` (`:206-209`);
    - valida os vetores recuperados (`:210-218`): `dense` deve ser `list` de 3072 floats finitos não todos zero, e `bm25` deve ser `models.SparseVector` com `len(indices) == len(values)` e valores finitos.
  - `packages/ingestion/tests/test_indexing.py:485-527`: `test_ING_IDX_001_existing_batches_validate_all_records` testa 129 chunks (128 normais + 1 marcado), confirma exatamente 2 chamadas a `retrieve` (`[(128, True), (1, True)]`) mesmo com retorno em ordem reversa, e testa recusas para duplicatas, `wrong-owner` e ponto deletado; `test_ING_IDX_001_existing_marked_payload_and_vectors` testa recusas para chunk marcado presente no índice, payload adulterado e vetor `dense` removido.

---

## 3. Impedimentos reais e fronteira de escopo (Sem G4 / Sem aprovação do lote completo)

1. **Bloqueio de credencial para o corpus real 9 ataques + 11 negativos (`ING-INJ-001` / `ING-INJ-002` / `SEC-ATQ-001`)**:
   - Status: **Bloqueado** (`juiz-real-ressalvas.json` com `attempts_per_case: 0` e `reason: "rotacao_credencial_pendente"`; `incidente-credencial-ressalvas.md`).
   - O bloqueio é legítimo e obrigatório: após a exposição da chave no traceback inicial (já sanitizado), o coordenador suspendeu chamadas à API real até que o humano confirme a rotação da `GEMINI_API_KEY` no `.env`. Não se deve reexecutar testes reais antes dessa confirmação.
2. **Causa histórica da falha `run3` na rodada 2 (`OBS-DIA-001`)**:
   - Status: **Bloqueado**. O artefato original `pytest-r2-run3-incompleto.txt` não possui traceback. O achado de `litellm.input_callback = ['cache']` explica a falha da guarda nova na focal desta slice, mas não é extrapolado como causa retroativa da `run3`.
3. **Integração com a mudança `2026-11-24-correcao-integral-ressalvas` (T-010 a T-017)**:
   - Esta revisão aprova tecnicamente a implementação local entregue por `ctx_3017e7ea9815` para os itens **1, 2, 15, 16 e diagnóstico sanitizado local (item 10)** nos pacotes `lineage`, `llm` e `ingestion`.
   - Os demais itens da lista de 16 ressalvas (itens 3 a 9, 11 a 14: contratos novos `T-010` em execução independente, spans/texto canônico, chunking H2/H3 e Unicode, protocolo durável sem transação longa, lock do Docling, trilha append-only de auditoria, reconciliação Qdrant ← linhagem, reclassificação e prova final integrada + RAGAS) pertencem às tarefas `T-010`–`T-017` e **não** estão concluídos nesta slice.
   - Nenhuma reexecução da suíte local desta slice é exigida agora; a execução integrada única ficará para a etapa final (`T-017`) após a rotação da credencial e a conclusão das demais slices. Gate **G4** permanece pendente do humano.

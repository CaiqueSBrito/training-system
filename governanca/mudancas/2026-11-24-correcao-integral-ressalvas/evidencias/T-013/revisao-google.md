# Revisão independente (Google) — T-013 (`2026-11-24-correcao-integral-ressalvas`)

- **Tarefa**: T-013 (`2026-11-24-correcao-integral-ressalvas`) — Construir reserva durável e trilha de avaliação
- **Requisitos avaliados**: `ING-TXN-001`, `ING-AUD-001`, `ING-LEG-001` (Casos `C07`, `C12`, `C14`)
- **Provedor efetivo do autor (mantido)**: `openai` (`rag-backend`, dispatch `ctx_d18611b3f44b`)
- **Provedor efetivo da revisão (técnica + `seguranca-llm`)**: `google` (Antigravity / Gemini 4 Argon; dispatch `ctx_bb88a686b3d5`, task `task_b5a1e194bcbf` — fallback efetivo pois `anthropic`/Opus está sem créditos e `openai` é o autor)
- **Relatório separado `seguranca-llm`**: `evidencias/T-013/revisao-seguranca-llm-google.md`
- **Snapshot/revisão exata de leitura (delimitando a entrega T-013 antes de ampliações posteriores por T-014)**:
  - `packages/ingestion/src/training_ingestion/core.py` (336 linhas, `sha256: 830f4242d4f6c7830702cbd21c8d83ec9971c5c23622b27bc104513bccfb72f5`)
  - `packages/ingestion/src/training_ingestion/protocol.py` (498 linhas, `sha256: b04f5a61c8d205d13217513eb117acd5f155adfd43da2503e848ede74c2f245a`)
  - `packages/llm/src/training_llm/__init__.py` (292 linhas, `sha256: 02a17338c717b48f6df63b70338c4e521b09da147c6782bba1ef203097c9134d`)
  - `packages/lineage/src/training_lineage/repository.py` (584 linhas, `sha256: 0c585c06bdef92d50db9177149678f79c528930858c5046e8c2412d26f48c921`)
  - `packages/ingestion/tests/test_ressalvas_protocolo.py` (301 linhas, `sha256: c1d7f0958386609d661fdb61929ae1699d142586737227fda27d81cc027e5411`)
- **Veredito para T-013**: **reprovado** (com 2 achados reais: 1 achado de migração incompleta de caller de teste não congelado após a remoção de `_PartialIngestor`, e 1 lacuna de cobertura/prova de `ING-LEG-001` já registrada pelo próprio executor como `Bloqueado`; os protocolos e invariantes de `ING-TXN-001` e `ING-AUD-001` em `protocol.py`, `llm/__init__.py` e `repository.py` estão corretos e aprovados na parte técnica e de segurança).

---

## 1. Análise técnica por invariante (`arquivo:linha` / símbolo)

### 1.1. Ausência de `BEGIN` durante parse/juiz/embedding/Qdrant, deduplicação, isolamento por dono e fencing (`ING-TXN-001` / `C07`)

- **Transações curtas e trabalho pesado/remoto fora de transação (`packages/ingestion/src/training_ingestion/protocol.py:59-66,74-239`)**:
  - `DurableIngestionProtocol._read` (`:59-61`) e `_write` (`:63-65`) abrem e fecham `self.engine.connect()` / `self.engine.begin()` apenas para a chamada imediata ao `LineageRepository`.
  - Em `ingest` (`:74-111`) e `_run` (`:113-239`), a leitura do arquivo e cálculo do SHA-256 (`:80-86`), o parse Docling e chunking (`:122-123`), a chamada ao juiz de injeção (`:270,412`), a geração de embedding (`:209`), o `index.upsert` no Qdrant (`:219`) e a verificação em lotes `processor.verify_existing` (`:232`) ocorrem **todos fora** de qualquer bloco `self.engine.begin()`.
  - Comprovado em `test_C07_no_begin_at_parse_judge_embed_upsert_and_dedup` (`test_ressalvas_protocolo.py:103-106,121-136`) via consulta a `pg_stat_activity` (`xact_start IS NOT NULL`) durante `parse`, `judge`, `embedding` e `upsert`.
- **Reserva durável, deduplicação por `(owner_id, source_sha256)`, isolamento por dono e fencing (`protocol.py:67-73,88-94,117-120,143-147,237-238`)**:
  - `repo.reserve(owner_id, digest, attempt_id, lease_seconds=LEASE_SECONDS)` (`:88-89`) retorna `'busy'` (convertido em `IngestionBusy`, `:90-91`) quando outro worker detém lease ativa para o mesmo `(owner_id, digest)`, retorna `'ready'` apenas quando a versão está integralmente publicada e íntegra (`:92-93`), e isola outro `owner_id` com `document_version_id` próprio (`test_ressalvas_protocolo.py:134-138,153-175`).
  - Renovação de lease (`_renew`, `:67-73`) converte expiração ou incremento de `generation` em `IngestionLeaseLost('Reserva obsoleta ou expirada') from None`, impedindo que um parser lento cuja lease expirou grave ou publique sobre a nova geração (`test_C07_stale_slow_parser_cannot_publish`, `:177-195`).
- **Recuperação após queda (antes e depois do upsert) e publicação `ready` somente com `ack` e decisão atual (`protocol.py:115-190,193-239`, `repository.py:425-434`)**:
  - Se o processo cair após `commit_parsed` (`phase == 'persisted'`), a retomada após expiração da lease faz takeover com `newly_parsed = False` (`:115`), carrega `canonical`, `chunks`, `embedding_inputs` e `get_latest_reconciliation_job` (`repository.py:425-433`, adicionado em T-013) sem reexecutar o parser Docling, cria novo `ReconciliationJob` com `desired_revision = latest_job.desired_revision + 1` (`protocol.py:161-170`) e converge mantendo os JSONs históricos dos chunks byte a byte idênticos (`test_C07_crash_after_parse_commit_recovers_without_reparse`, `test_ressalvas_protocolo.py:197-218`).
  - Se o processo cair após `self.index.upsert` mas antes de `record_index_ack` / `publish_ready`, o ponto presente no Qdrant permanece inacessível em `authorize_candidates` (`== {}`) e a reexecução converge sem duplicar pontos (`test_C07_crash_after_upsert_is_unacknowledged_then_converges`, `:220-238`).
  - `_ready_result` (`protocol.py:461-470`) devolve ao chamador apenas os chunks autorizados por `repo.authorize_candidates(owner_id, [c.chunk_id for c in chunks], revisions)`.

### 1.2. Auditoria `triage` antes do juiz, liberações/erros, fingerprint real da chamada LiteLLM e sanitização (`ING-AUD-001` / `C12`)

- **Triagem persistida antes da chamada ao juiz e preservação de `heuristic_rule_ids` (`protocol.py:240-352`)**:
  - Em `_assess`, o evento `stage='triage'` (`sequence = revision + 1`) é gravado e comitado em transação curta (`self._write(lambda repo: repo.append_assessment([triage], ...))`, `:257-259`) **antes** de qualquer chamada a `self.model.judge_injection(value)` (`:270`).
  - As regras detectadas (`rules = (suspect.split(':', 1)[-1],) if suspect else ()`, `:248`) são propagadas de forma idêntica para os eventos `judge` (`:292,303`) e `decision` (`:294,306,310,321`), satisfazendo a invariante de escopo do trigger `t011_audit_insert`.
  - Quando o chunk é suspeito (mas não `high_precision_reason`) e o juiz retorna `injection=False`, a trilha persiste os 3 eventos (`triage` -> `judge` com `result='safe'` -> `decision` com `outcome='allowed', reason_code='judge_release'`, `:302-307`).
  - Quando o juiz lança exceção/timeout (`:277-300`), a trilha persiste `stage='judge'` com `result='error', error_kind=kind` e `stage='decision'` com `outcome='unavailable', reason_code='judge_unavailable'`, transiciona a reserva para `phase='failed'` e o job para `state='retry'` (`_record_failure`, `:487-497`) e levanta `IngestionStageError(kind, ...) from None`, sem persistir o texto do erro nem o `reason` bruto (`test_C12_judge_error_persists_error_without_raw_reason_then_retries`, `test_ressalvas_protocolo.py:240-255`).
- **Hashes de `JudgeMetadata` correspondem exatamente ao prompt e configuração reais (`packages/llm/src/training_llm/__init__.py:174-279`)**:
  - `GeminiModels._judge_system_prompt()` (`:212-222`) e `GeminiModels._judge_request_configuration()` (`:224-234`) são a fonte única usada tanto por `injection_judge_spec()` (`:236-252`, que calcula `config_sha256` sobre o JSON canônico ordenado e `prompt_sha256` sobre o UTF-8 do system prompt) quanto por `judge_injection()` (`:263-269`, que passa `**configuration` e `self._judge_system_prompt()` diretamente a `litellm.completion`).
  - Comprovado em `test_T013_judge_metadata_matches_exact_request_and_changes_with_settings` (`test_ressalvas_protocolo.py:275-301`).

### 1.3. Reclassificação conservadora e legado insuficiente (`ING-LEG-001` / `C14`)

- **Implementação no protocolo (`protocol.py:353-460`)**:
  - `DurableIngestionProtocol.reclassify(owner_id, version_id)` verifica ownership (`:355`), carrega chunks e `embedding_inputs` (`:356-358`), e:
    - Para chunks sem `EmbeddingInputRecord` persistido (`:361-379`), marca `mark_legacy_insufficient` se ainda não houver proveniência e anexa `[triage, decision]` com `outcome='unavailable', reason_code='legacy_insufficient'` (`_append_legacy_unavailable`, `:438-460`), sem fabricar título, seção ou fonte canônica;
    - Para chunks com `EmbeddingInputRecord` (`:380-385`), executa `_assess_unfenced` (`:387-436`), anexando nova cadeia de eventos com `supersedes_decision_id = previous_id` sem tocar nas linhas de `document_version` ou `chunk`.
  - Como `authorize_candidates` exige que o `index_ack` da versão `ready` tenha `decision_revision == decision.revision` e `decision.allowed is True`, qualquer reclassificação bloqueante ou `unavailable` revoga imediatamente a autorização de leitura (`test_C14_reclassification_preserves_history_and_revokes_ready`, `test_ressalvas_protocolo.py:257-273`).

---

## 2. Achados reais (`arquivo:linha`)

1. **[Médio — Cutover incompleto de caller de teste não congelado] `packages/ingestion/tests/test_ingestion.py:14,67,73,145,152,167,181-182` e `packages/ingestion/README.md:102`**:
   - **Evidência**: `core.py:314-336` removeu completamente `_PartialIngestor` (fazendo o cutover limpo para `Ingestor` que exige `processor` e delega a `DurableIngestionProtocol`). Entretanto, `packages/ingestion/tests/test_ingestion.py:14` ainda contém `from training_ingestion.core import _PartialIngestor as Ingestor` e chama `Ingestor(engine)` sem `processor` nas linhas `67, 73, 145, 152, 167, 181, 182` (além da menção residual a `core._PartialIngestor` em `packages/ingestion/README.md:102`).
   - **Impacto**: Qualquer coleta de `packages/ingestion/tests/test_ingestion.py` pelo `pytest` falhará imediatamente na linha 14 com `ImportError: cannot import name '_PartialIngestor' from 'training_ingestion.core'`. Como `test_ingestion.py` não é um arquivo G3 congelado (G3 foi dispensado em `T-003`–`T-008` e `T-010`–`T-017`), seus testes que instanciam `Ingestor` precisam usar a entrada pública `Ingestor(engine, processor=...)` (sem reintroduzir shim `_PartialIngestor`).

2. **[Médio — Cobertura de casos de `ING-LEG-001` / `C14` incompleta, já reportada como `Bloqueado` na tarefa] `governanca/mudancas/2026-11-24-correcao-integral-ressalvas/tarefas/T-013.md:18,56` e `packages/ingestion/tests/test_ressalvas_protocolo.py:257-273`**:
   - **Evidência**: `test_C14_reclassification_preserves_history_and_revokes_ready` (`test_ressalvas_protocolo.py:257-273`) exercita apenas o sentido `allowed → blocked`. Embora `protocol.py:361-460` implemente os ramos de `blocked → allowed`, falha do juiz durante `reclassify` (`:413-426`) e ausência de `embedding_input` (`_append_legacy_unavailable`, `:438-460`), esses três caminhos ainda não possuem caso de teste executável em `test_ressalvas_protocolo.py` (razão pela qual o próprio executor manteve `ING-LEG-001` como `Bloqueado` em `tarefas/T-013.md:18`).
   - **Impacto**: Conforme instrução do despacho, esses casos faltantes de `ING-LEG-001` (junto com a reconciliação da projeção vetorial após reclassificação e o payload com revisão/hash no Qdrant) devem ser completados e provados em `T-014` / antes do fechamento da mudança, não podendo `ING-LEG-001` ser declarado concluído em `T-013`.

---

## 3. Resumo da revisão `seguranca-llm` (detalhes em `evidencias/T-013/revisao-seguranca-llm-google.md`)

- **Veredito `seguranca-llm` para o código de T-013**: **aprovado** (0 vulnerabilidades nos mecanismos de guardrail, montagem de prompt do juiz, isolamento de credenciais e sanitização de logs/eventos/exceções).
- **Destaques verificados (`arquivo:linha`)**:
  - **LLM01 (Injeção indireta / falha fechada)**: triagem normaliza NFKD/casefold/Cf/fillers/soletração espaçada apenas para detecção, passando o texto original intacto ao juiz dentro de envelope JSON `{'untrusted_chunk': text}` (`indexing.py:27-67`, `llm/__init__.py:212-222,266-267`); delimitadores de modelo/tags Unicode/bidi bloqueiam por `rule_block` sem depender do juiz (`protocol.py:263-265`); qualquer erro ou timeout do juiz gera `outcome='unavailable'` e `IngestionStageError` fail-closed (`protocol.py:277-300`); `ChunkRecord.injection_reason` nunca recebe a string `verdict.reason` não confiável do LLM no protocolo V2 (`protocol.py:214-216` usa literal fixo `'bloqueado_por_decisao_auditavel'` apenas na cópia transitória para deleção no Qdrant, enquanto o banco guarda apenas `ReasonCode` enumerado).
  - **LLM07 e Dados/Segredos**: `_guard_sdk_logging()` é chamado antes e depois do import do LiteLLM e antes de cada chamada de juiz/embedding (`llm/__init__.py:109-165,179,191,255,259`), acoplando `_SuppressSDKRecords` e forçando `no-log: True`, `turn_off_message_logging = True`, `redact_messages_in_exceptions = True` e `redact_user_api_key_info = True`; `judge_api_key()` isola estritamente `GEMINI_API_KEY` (`gemini/`) e `OPENROUTER_API_KEY` (`openrouter/`) (`llm/__init__.py:280-292`); exceções `IngestionStageError` e `InjectionJudgeError` usam `from None` com categorias constantes, sem vazar prompt, chunk, resposta bruta ou chave (`protocol.py:38-43,100-111,298-300`, `llm/__init__.py:77-86,167-171,270-279`).

---

## 4. Limites explícitos desta revisão

1. Esta revisão avalia **exclusivamente o snapshot de T-013** listado no cabeçalho (`core.py`, `protocol.py`, `llm/__init__.py`, `repository.py:425-434` e `test_ressalvas_protocolo.py`).
2. Não avalia nem aprova antecipadamente a inclusão de `decision_event_id`/`revision`/`input_sha256` no payload do Qdrant, o reconciliador em lotes, o inventário/remoção de órfãos nem o consumidor de elegibilidade de `T-014`/`T-016`.
3. Não declara `ING-LEG-001` completo, não aprova o lote de 16 ressalvas, não substitui os testes com juiz real (`T-015`, suspensos aguardando confirmação de rotação da `GEMINI_API_KEY`) nem a prova integrada e diff RAGAS (`T-017`, `Bloqueado`), e não concede o Gate G4 humano.

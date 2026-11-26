# Revisão independente (Google) — T-011 (`2026-11-24-correcao-integral-ressalvas`)

- **Tarefa**: T-011 (`2026-11-24-correcao-integral-ressalvas`) — Persistir canônico e eventos com migração aditiva
- **Requisitos avaliados no escopo de persistência relacional**: `ING-MIG-001`, `ING-MIG-002`, `ING-CAN-001`, `ING-PRO-001`, `ING-AUD-001`, `ING-LEG-001` (Casos `C01`, `C02`, `C03`, `C04`, `C12`, `C14` e persistência auxiliar `C07`)
- **Provedor efetivo do autor (mantido)**: `openai` (`rag-backend`, dispatch `ctx_34fd30a2a133`)
- **Provedor efetivo da revisão**: `google` (Antigravity / Gemini 4 Argon; dispatch `ctx_ba16a9c5839c`, task `task_7540a9fcba47`)
- **Snapshot/revisão exata de leitura (delimitando a entrega T-011 antes de ampliações posteriores por T-013)**:
  - `packages/lineage/src/training_lineage/tables.py` (156 linhas, `sha256: 252af57e341a1ecbecc78ed405933820d232ba705d4d2f47211e86f48707f0a1`)
  - `packages/lineage/src/training_lineage/repository.py` (574 linhas, `sha256: e245a1a7b8269ac5b13fba5e6ed07ff18fb5d60ebbe8878cd29e0567b80cf8be`)
  - `packages/lineage/src/training_lineage/migrations/versions/0003_canonical_audit_protocol.py` (484 linhas, `sha256: 5141cebb409dba293ac9e89e04e5dd9a0fdf26b31664061b0ec84e7a7013574f`)
  - `packages/lineage/tests/test_ressalvas_persistencia.py` (393 linhas, `sha256: ba1ac990c5718e2966c20c6225fee23a7e3f096da9c7a4ff824d2a795347faf8`)
  - `packages/lineage/tests/test_lineage.py` (intocado por T-011, `sha256: 3c021b178cb9518c8c5f3dd6cd9687f915897c0bd8d98d517264e4b8476aa9b0`)
- **Veredito para T-011**: **aprovado** (no escopo estrito do schema relacional `0003`, triggers/constraints, `LineageRepository` e testes em Postgres real de `T-011`; **não** aprova o protocolo de ingestão fora de transação de `T-013`, a reconciliação/inventário Qdrant de `T-014`/`T-016`, os ataques em juiz real de `T-015`, a prova final integrada e adaptação de `test_lineage.py` em `T-017`, o encerramento das 16 ressalvas nem o Gate G4 humano).

---

## 1. Escopo e conformidade de governança

1. **Gates e rastreabilidade de evidências (`governanca/mudancas/2026-11-24-correcao-integral-ressalvas/tarefas/T-011.md:11-28,63-65`, `evidencias/T-011/resultado.md:5-11`, `evidencias/T-011/correcoes-pos-focal.md:1-7`)**:
   - G1 e G2 aprovados pelo humano (`aprovo_proposta_contratos_e_execucao_T010_T017`); G3 dispensado (`dispenso_G3_T010_T017`).
   - Casos G3 congelados de `T-001` (`test_classificacao.py`, `test_plano.py`, `test_itens.py`) e os testes anteriores de `packages/lineage/tests/test_lineage.py` permaneceram inalterados.
   - A primeira execução focal em bancos Postgres temporários descartáveis (`test_t011_<uuid>`) falhou com `35 passed, 6 failed in 6.35s` e foi integralmente preservada em `evidencias/T-011/pytest.txt`.
   - As 6 falhas iniciais tinham apenas duas causas no repositório (`repository.py`): (a) 5 falhas por `sqlalchemy.exc.InvalidRequestError` em joins implícitos de `load_chunk_provenance` e `load_embedding_inputs`, corrigidas com `.select_from(...join(..., onclause))` explícitos (`repository.py:175-178,211-214`); e (b) 1 falha em `append_assessment` onde a checagem de sequência `e.sequence != expected_revision + i + 1` estava agrupada na mensagem genérica `'Cadeia ou hash da avaliação divergente'`, separada para `'Revisão obsoleta ou sequência divergente'` (`repository.py:257-258`), sem alterar nenhum caso de teste em `test_ressalvas_persistencia.py`.
   - O complemento autorizado pelo coordenador (`msg_735056d6d974`) reexecutou apenas os 6 casos afetados (`evidencias/T-011/pytest-complemento.txt`: `6 passed, 35 deselected in 1.22s`), compondo 41/41 casos aprovados, e o validador final (`evidencias/T-011/validador.txt`) registrou `governança OK: 5 mudança(s), 17 tarefa(s), 28 requisito(s) na spec viva`.
   - Conforme instrução do despacho, nenhum teste, serviço ou API foi reexecutado nesta revisão.

---

## 2. Análise técnica por invariante (`arquivo:linha` / símbolo)

### 2.1. Migração aditiva `0003`, preservação de histórico e downgrade seguro (`ING-MIG-001`, `ING-MIG-002` / `C01`, `C02`)

- **Snapshot DDL literal e alinhamento com SQLAlchemy Metadata (`packages/lineage/src/training_lineage/migrations/versions/0003_canonical_audit_protocol.py:1-206`, `packages/lineage/src/training_lineage/tables.py:45-156`)**:
  - A revisão `0003` (`down_revision = '0002'`) usa DDL literal estático (`DDL` e `VALIDATIONS`), sem importar `tables.metadata` mutável em runtime e sem tocar `0001_ingestion.py` ou `0002_document_immutable.py`.
  - Cria 5 tabelas históricas (`canonical_text`, `canonical_span`, `chunk_provenance`, `embedding_input`, `ingestion_audit_event`) e 4 tabelas operacionais (`ingestion_reservation`, `effective_chunk_decision`, `reconciliation_job`, `index_ack`), todas sem defaults falsos ou backfill inventado sobre versões antigas.
  - `test_C02_database_catalog_and_specific_checks` (`test_ressalvas_persistencia.py:243-248`) comprova `compare_metadata(MigrationContext.configure(c), tables.metadata) == []`.
  - `test_C01_upgrade_preserves_old_records` (`test_ressalvas_persistencia.py:110-125`) comprova upgrade a partir de `base`, `0001` (com registros inseridos) e `0002` (com registros inseridos) até `0003` e reexecução idempotente de `migrate`, preservando `DocumentVersionRecord` e `ChunkRecord` intactos e mantendo `authorize_candidates` vazio (`{}`) para registros antigos sem trilha/reserva `ready`.
- **Imutabilidade append-only e triggers de integridade no Postgres (`0003_canonical_audit_protocol.py:208-212,463-471`)**:
  - Cada uma das 5 tabelas em `HISTORY` recebe dois triggers que executam `t011_immutable()` (`ERRCODE='23514'`): `{table}_history_rows` (`BEFORE UPDATE OR DELETE ... FOR EACH ROW`) e `{table}_history_truncate` (`BEFORE TRUNCATE ... FOR EACH STATEMENT`) (`:466-468`), exercitados nas 15 combinações `(tabela × UPDATE/DELETE/TRUNCATE)` em `test_C02_all_new_history_is_append_only` (`test_ressalvas_persistencia.py:299-311`).
- **Downgrade seguro fail-closed (`0003_canonical_audit_protocol.py:474-484`)**:
  - `downgrade()` itera primeiro sobre **todas** as 9 tabelas (`NEW_TABLES`, `:209,476-477`) e aborta com `RAISE EXCEPTION 'história nova impede downgrade destrutivo' USING ERRCODE='23514'` antes de executar qualquer `op.drop_table` ou `DROP FUNCTION`.
  - Provado em `test_C02_empty_downgrade_preserves_0002_and_protections` (`test_ressalvas_persistencia.py:128-135`, onde o downgrade vazio remove as 9 tabelas novas e preserva as tabelas e triggers de imutabilidade de `0002`) e em `test_C02_history_refuses_downgrade_before_any_drop` (`:138-146`, onde a presença de dados recusa o downgrade mantendo `alembic_version == '0003'` e os registros intactos).

### 2.2. Integridade canônica, hash UTF-8, partição de spans, mapas, citações por interseção e ownership (`ING-CAN-001`, `ING-PRO-001` / `C03`, `C04`)

- **Validação em duas camadas (repositório + triggers/constraints no banco)**:
  - **No banco (`tables.py:46-86`, `0003_canonical_audit_protocol.py:30-104,213-279,471`)**:
    - `canonical_text` exige `canonical_digest` (`text_sha256 = encode(sha256(convert_to(text,'UTF8')),'hex')`, `tables.py:54`), `UNIQUE(document_version_id, canonical_version)`, `t011_canonical_insert()` (conferindo `parser_version` igual ao de `document_version`, `0003:213-220`) e o constraint trigger deferrable `canonical_partition_complete` (`AFTER INSERT ON canonical_text DEFERRABLE INITIALLY DEFERRED`, `0003:221-229,471`), que exige no commit `n > 0`, `min(start) = 0`, `max("end") = char_length(NEW.text)` e `sum("end" - start) = char_length(NEW.text)`.
    - `canonical_span` exige `canonical_span_range` (`start >= 0 AND "end" > start`), `canonical_span_page` (`page IS NULL OR (page >= 1 AND provenance='exact' AND kind<>'separator')`, `tables.py:65`) e `t011_span_insert()` (`0003:230-239`, bloqueando `end > char_length(text)`, sobreposição entre spans do mesmo `canonical_id` e elementos brancos em `section_path`).
    - `chunk_provenance` exige `chunk_provenance_state` (separando `mapping_status='exact'` de `'insufficient'` com todos os campos de coordenada nulos/vazios, `tables.py:74`) e `t011_mapping_insert()` (`0003:240-268`), que valida no próprio Postgres: igualdade de `document_version_id`, igualdade de offsets `(NEW.start, NEW."end") == (c.start_offset, c.end_offset)`, igualdade exata de substring em codepoints `c.text = substr(source.text, NEW.start::int+1, (NEW."end"-NEW.start)::int)` (`0003:250`), igualdade exata da lista ordenada de spans intersectados `array_agg(span_id ORDER BY start)` (`0003:252-254`), `count(DISTINCT h1_id) <= 1` (`0003:252-254`) e `overlap_ranges` JSONB internos, ordenados e disjuntos (`0003:256-266`).
    - `embedding_input` exige `t011_embedding_insert()` (`0003:269-279`), validando `NEW.text_sha256 = encode(sha256(convert_to(chunk.text,'UTF8')),'hex')` e sufixo `@||NEW.format_version` em `embedding_key`.
  - **No repositório (`repository.py:74-232`)**:
    - `LineageRepository.persist_canonical` (`:103-134`) valida ownership via `_owned_version` (`:74-82`), igualdade de `parser_version`, `validate_canonical_spans`, bijeção entre `chunks`, `provenance` e `embedding_inputs`, `validate_chunk_provenance` e `item.validate_input(value, format_input(item.title, list(item.section_context), value))` usando o formatter puro injetado (`training_llm.document_input`), gravando tudo dentro de `with self.connection.begin_nested():` (savepoint) via `_put_history` idempotente por identidade (`:95-101`).
    - `LineageRepository.resolve_citation` (`:181-190`) exige ownership do chunk em `_owned_chunk` (`:84-93`, levantando `PermissionError` para outro `owner_id`), recusa `mapping is None or mapping.mapping_status != 'exact'` com `ValueError('Fonte canônica insuficiente')`, carrega o `CanonicalTextRecord` e os `CanonicalSpan` persistidos no banco e delega o recorte de interseções a `resolve_citation_spans`.
    - Provado em `test_C03_C04_citation_intersections_from_persisted_source` (`test_ressalvas_persistencia.py:149-171`) e nas 7 mutações de `test_C04_rejects_relational_mismatch_without_partial_artifacts` (`:174-188`).

### 2.3. Auditoria append-only, CAS por sequência, liberação e reclassificação sem mutar histórico (`ING-AUD-001`, `ING-LEG-001` / `C12`, `C14`)

- **Integridade de cadeia e CAS no banco e no repositório (`tables.py:99-129`, `0003_canonical_audit_protocol.py:280-368`, `repository.py:233-309`)**:
  - `ingestion_audit_event` possui `UNIQUE(chunk_id, sequence)`, `UNIQUE(chunk_id, attempt_id, stage)` e FKs compostas `(triage_event_id|judge_event_id|supersedes_decision_id, chunk_id, owner_id, document_version_id)` apontando para `(event_id, chunk_id, owner_id, document_version_id)` (`tables.py:110-115`), impedindo no nível de FK qualquer vínculo cruzado entre chunks, donos ou versões distintas.
  - O trigger `t011_audit_insert()` (`0003:280-356`) adquire `SELECT * INTO c FROM chunk WHERE chunk_id=NEW.chunk_id FOR UPDATE` (`:284`), serializando escritas concorrentes no chunk; exige `NEW.sequence = coalesce(max(sequence), 0) + 1` (`:289-290`, com `ERRCODE='23505'`); valida para `judge` e `decision` que `triage_event_id` aponta para o último evento `triage` do mesmo chunk/attempt/policy/input_sha256/heuristic (`:302-309`); valida esquema estrito e sanitizado do JSONB `judge_metadata` (`:310-330`); exige que `NEW.supersedes_decision_id` seja exatamente a `previous_decision` (`stage='decision'` de maior `sequence` anterior no chunk, `:341-342`); valida a combinação `(reason_code, outcome, t.outcome, j.outcome)` (`:343-351`, incluindo `rule_block` com `t.outcome='suspect'` mesmo quando `j.outcome='safe'`, e `legacy_insufficient` exigindo `mapping_status='insufficient'`); e impede `outcome='allowed'` sem `chunk_provenance` `exact` e `embedding_input` (`:352-354`).
  - O trigger `t011_effective_write()` (`0003:357-368`) garante que `effective_chunk_decision` aponte sempre para um evento `stage='decision'` cuja `sequence` seja o `max(sequence)` atual do chunk.
  - `LineageRepository.append_assessment` (`repository.py:246-282`) recalcula `sha256(evaluated_input.encode('utf-8')).hexdigest()`, confere `expected_revision` sob lock do chunk (`_owned_chunk(..., lock=True)`), insere os eventos em savepoint e só atualiza `effective_chunk_decision` quando o último evento é `stage == 'decision'`.
  - `LineageRepository.get_effective_decisions` (`repository.py:290-308`) faz join entre `effective_chunk_decision` e a subquery `heads` (`func.max(ingestion_audit_event.c.sequence)` por `chunk_id`): assim que um novo evento parcial (`triage` ou `judge`) ou decisão de erro (`outcome='unavailable'`) é anexado, `heads.c.revision` avança ou `allowed` passa a `False`, revogando imediatamente a decisão anterior sem apagar nem mutar eventos passados, `ChunkRecord` ou `DocumentVersionRecord`.
  - Provado em `test_C12_audit_preserves_judge_and_real_block_reason`, `test_C12_partial_error_revokes_old_release_and_stale_attempt`, `test_C12_clean_chain_has_no_invented_judge_and_cross_links_roll_back`, `test_C12_concurrent_cas_only_one_writer`, `test_C14_legacy_insufficient_is_explicit_without_fabricated_source` e `test_C14_new_policy_can_release_historical_flag_without_mutation` (`test_ressalvas_persistencia.py:191-241,278-297,314-343`).

### 2.4. Reserva com fencing, takeover, checkpoints, recibos de índice, `publish_ready` e `authorize_candidates` fail-closed (`repository.py:310-574`, `0003_canonical_audit_protocol.py:369-448`)

- **Reserva, relógio do Postgres e takeover (`repository.py:310-382`, `0003:369-384`)**:
  - Todas as comparações e cálculos de `lease_until` usam `func.clock_timestamp()` do Postgres (`_now`, `:310-311`).
  - `reserve` (`:329-358`) garante `UNIQUE(owner_id, source_sha256)`: retorna `('ready', record)` apenas se `record.phase == 'ready'` **e** `self._version_complete(...)` for verdadeiro (`:347-348`); retorna `'busy'` para outro `attempt_id` enquanto `lease_until > _now()` (`:349-350`); e, se a lease expirou ou a reserva falhou/ficou stale, faz takeover incrementando `generation = record.generation + 1`, preservando o mesmo `document_version_id` e posicionando `phase = 'persisted'` se `has_persisted_artifacts` for verdadeiro (ou `'reserved'` caso contrário, `:351-357`).
  - O trigger `t011_reservation_write()` (`0003:369-384`) impede mutar identidade (`reservation_id`, `owner_id`, `source_sha256`, `document_version_id`), impede incrementar `generation` enquanto a lease anterior estiver ativa em fase não terminal (`:380`) e bloqueia transição de fase na mesma `generation` após expiração da lease (`:382-383`).
- **Guarda de publicação `ready` e autorização fail-closed (`repository.py:471-574`, `0003:384-448`)**:
  - Tanto `LineageRepository.publish_ready` (`repository.py:531-544`, via `_version_complete` e `_projection_complete` em `:491-530`) quanto o trigger de banco `t011_reservation_write` (`0003:384-405`) exigem, para permitir `phase = 'ready'`, que:
    1. A reserva esteja em `phase == 'indexing'` com `generation` e `lease_until` vigentes;
    2. Exista ao menos um chunk e um `reconciliation_job` para a versão, e o job de maior `desired_revision` de cada `embedding_key` esteja em `state == 'complete'` na `generation` atual;
    3. Todo chunk da versão possua `chunk_provenance` com `mapping_status == 'exact'`, `effective_chunk_decision` na maior `sequence` de `ingestion_audit_event` com `outcome IN ('allowed', 'blocked')`, e `index_ack` na `generation` e `decision_revision` atuais (com `input_id` válido de `embedding_input` quando `allowed`, ou `input_id IS NULL` quando `blocked`, validado também por `t011_operational_scope` em `0003:433-446`).
  - `LineageRepository.authorize_candidates` (`repository.py:546-574`) consulta chunks em lotes de até 128 IDs com `with_for_update(read=True, of=chunk)` e filtra por `owner_id`: só retorna o `ChunkRecord` se `decision is not None`, `decision.allowed is True`, `observed_revision.get(chunk_id) == decision.revision`, existir reserva `phase == 'ready'` para `(owner_id, document_version_id)` **e** `self._version_complete(owner_id, document_version_id, reservation['generation'])` continuar verdadeiro no instante da leitura. Qualquer reclassificação bloqueante, evento parcial ou falha revoga imediatamente a autorização de leitura, mesmo antes de o ponto ser removido do Qdrant (`test_C07_C12_C14_reservation_fencing_ready_and_current_decision`, `test_ressalvas_persistencia.py:345-393`).

---

## 3. Achados e limites

- **Achados bloqueantes ou de retrabalho em T-011**: **Nenhum (0)**.
- **Observação de integração já registrada pelo executor para T-017 (`evidencias/T-011/resultado.md:36`, `evidencias/T-011/interfaces.md:32`)**:
  - Os testes históricos em `packages/lineage/tests/test_lineage.py` não foram editados por T-011 (respeitando o escopo da tarefa), mas contêm asserções escritas quando `head` era `0002` e o schema tinha apenas 2 tabelas. Sua atualização coordenada pertence à etapa de integração/prova final (`T-017`) sob decisão do coordenador/humano.
- **Limites explícitos desta aprovação**:
  1. Esta revisão aprova **exclusivamente a entrega de T-011** nos hashes e linhas identificados no cabeçalho (`tables.py`, `repository.py`, `0003_canonical_audit_protocol.py`, `test_ressalvas_persistencia.py` e `packages/lineage/README.md`).
  2. Qualquer ampliação subsequente de `repository.py` ou uso dessas operações pelo protocolo de ingestão fora de transação (`T-013`), bem como reconciliação/inventário Qdrant e elegibilidade na recuperação (`T-014`/`T-016`), ataques em juiz real (`T-015`) e prova integrada final + diff RAGAS (`T-017`), serão avaliados em suas próprias tarefas e revisões.
  3. Esta revisão não declara encerradas as 16 ressalvas e não substitui o Gate G4 humano.

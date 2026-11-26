# Revisão independente (Google) — T-010 (`2026-11-24-correcao-integral-ressalvas`)

- **Tarefa**: T-010 (`2026-11-24-correcao-integral-ressalvas`) — Implementar contratos aditivos e formalizar ADR
- **Requisitos avaliados no escopo de contratos/ADR**: `ING-PRO-001`, `ING-CAN-001`, `ING-AUD-001`, `ING-TXN-001`, `ING-REP-001`
- **Provedor efetivo do autor (mantido)**: `openai` (GPT-6.1 Sol, fallback autorizado do nominal `anthropic`/Opus 5.5 sem créditos; dispatch `ctx_45dbdb7a6e9e`)
- **Provedor efetivo da revisão**: `google` (Antigravity / Gemini 4 Argon; dispatch `ctx_e8ddb8fa3dab`, task `task_fe71ade9e03c`)
- **Distinção de escopo**: esta revisão avalia **somente T-010** (`packages/contracts` e `docs/adr/0015-canonico-decisoes-indice.md`) e é distinta da revisão local da slice de backend Google (`ctx_d072ae09d0c3`), sem duplicá-la.
- **Veredito para T-010**: **aprovado** (no escopo estrito de contratos, helpers puros, exports, JSON Schemas e ADR-0015; **não** aprova o lote de 16 ressalvas, **não** aprova T-011–T-017 e **não** concede nem substitui o Gate G4 humano).

---

## 1. Escopo e conformidade de governança

1. **Gates humanos respeitados (`governanca/mudancas/2026-11-24-correcao-integral-ressalvas/proposta.md:6,29-33`, `tarefas/T-010.md:11-12,63-68`)**:
   - G1 e G2 aprovados pelo humano (`aprovo_proposta_contratos_e_execucao_T010_T017` em `2026-11-24`).
   - G3 dispensado pelo humano (`dispenso_G3_T010_T017`), mantendo os casos escritos antes do produto (`evidencias/T-010/casos-antes-produto.txt:1-3`, `sha256: f7b933871407da588a1cfeb6c6aca686cd8303a1fe3b393e91ea4a0a27c6e99e` com `modulo_existe=False`, idêntico a `evidencias/T-010/casos-executados-sha256.txt:2`).
   - Casos G3 históricos de `T-001` preservados intactos (`test_classificacao.py` `b15e416b...`, `test_plano.py` `c897e706...`, `test_itens.py` `1623c0e1...`).
   - G4 permanece exclusivamente humano e pendente.
2. **Preservação de modelos e schemas históricos (`packages/contracts/src/training_contracts/__init__.py:8-132`, `schema.py:11-82`, `packages/contracts/schema/`)**:
   - `git diff --stat packages/contracts/ docs/adr/` confirma zero deleções em arquivos versionados: apenas adições em `__init__.py` (+35 linhas) e `schema.py` (+28 linhas), além dos arquivos novos `ingestao.py`, `test_ressalvas_contratos.py`, 13 JSON Schemas novos e `docs/adr/0015-canonico-decisoes-indice.md`.
   - Os 19 JSON Schemas históricos e `linhagem.py` (`DocumentVersionRecord`, `ChunkRecord`, `CitationLineage`, etc.) permanecem byte a byte inalterados.
3. **Sem reexecução nem acesso a credenciais**:
   - Conforme instrução do despacho, não foram reexecutados `pytest`, build, lint, validador nem chamadas de API/banco, e nenhum `.env` ou credencial exposta foi lido. As evidências reportadas pelo executor (`evidencias/T-010/pytest.txt`: `261 passed in 0.21s`; `evidencias/T-010/validador.txt`: `governança OK: 5 mudança(s), 17 tarefa(s), 28 requisito(s) na spec viva`) foram auditadas por inspeção estática cruzada com o código e os testes.

---

## 2. Análise técnica por invariante (`arquivo:linha`)

### 2.1. Texto canônico, SHA-256 UTF-8, offsets em codepoints e imutabilidade (`ING-CAN-001`)

- **Base estrita, imutável e UTC (`packages/contracts/src/training_contracts/ingestao.py:17-42`)**:
  - `IngestionContract` define `ConfigDict(extra="forbid", frozen=True)` (`:34`) e valida em `_utc` (`:36-41`) que qualquer `datetime` presente tenha `utcoffset() == timedelta(0)`.
  - `Id` exige `min_length=1` e `pattern=r"\S"` (`:17`); `Positive` exige `int` estrito `ge=1` (`:18`, rejeitando `bool` como `True`); `Offset` exige `int` estrito `ge=0` (`:19`).
  - Todas as coleções nos contratos novos usam `tuple[..., ...]` (`:75,92-93,125,133,197`), impedindo mutação in-place após construção.
- **Intervalos semiabertos (`ingestao.py:44-53`)**:
  - `TextRange` valida `self.end <= self.start` (`:48-52`), garantindo intervalos `[start, end)` não vazios em codepoints.
- **Registro canônico (`ingestao.py:55-69`)**:
  - `CanonicalTextRecord` exige `text` não vazio (`:60`), `text_sha256: Sha256` (`:61`, minúsculo de 64 hex via `comum.py:22`) e `created_at: AwareDatetime` (`:62`).
  - O validador `_digest` (`:64-68`) recalcula `sha256(self.text.encode("utf-8")).hexdigest() != self.text_sha256`, recusando divergência de conteúdo mesmo com mesmo comprimento.
- **Proveniência do chunk e histórico insuficiente (`ingestao.py:87-113`)**:
  - `ChunkProvenanceRecord` separa explicitamente `mapping_status: Literal["exact", "insufficient"]` (`:94`).
  - Quando `insufficient` (`:99-102`), proíbe fabricar coordenadas (`canonical_id`, `start`, `end` devem ser `None`, e `span_ids` / `overlap_ranges` vazios).
  - Quando `exact` (`:103-112`), exige `canonical_id`, `start < end`, `span_ids` não vazio e sem duplicatas, e `overlap_ranges` internos (`start <= interval.start < interval.end <= end`), ordenados e disjuntos (`interval.start >= previous_end`).

### 2.2. Spans e citações exatas sem inventar páginas (`ING-PRO-001`)

- **Spans canônicos (`ingestao.py:71-85`)**:
  - `CanonicalSpan` herda `TextRange` e modela `page: Positive | None`, `section_path: tuple[Id, ...]`, `h1_id: Id | None`, `kind: Literal["heading", "paragraph", "table", "separator"]` e `provenance: Literal["exact", "unknown"]`.
  - O validador `_page` (`:80-84`) impede página inventada quando `provenance == "unknown"` ou `kind == "separator"`, permitindo `page=None` com `provenance="exact"` para formatos sem paginação nativa (DOCX/MD).
- **Helpers puros de validação cruzada e resolução de citações (`ingestao.py:297-347`)**:
  - Os modelos isolados não fingem acesso ao banco; a validação relacional em memória recebe explicitamente `(canonical, spans, chunk, mapping)` nos helpers puros, enquanto FKs, autorização por dono e transações ficam para o repositório em T-011/T-013.
  - `validate_canonical_spans` (`:297-307`) verifica que `spans` pertence ao mesmo `canonical_id`, não repete `span_id` e particiona contiguamente `[0, len(canonical.text))` (`span.start == cursor` e `cursor == len(canonical.text)`).
  - `validate_chunk_provenance` (`:310-328`) chama `validate_canonical_spans`, exige `mapping_status == "exact"`, confere identidades (`chunk_id`, `document_version_id`, `canonical_id`), igualdade de offsets `(mapping.start, mapping.end) == (chunk.start_offset, chunk.end_offset)`, igualdade exata de substring em codepoints `chunk.text == canonical.text[mapping.start:mapping.end]` (`:322`), interseção exata de `span_ids` na ordem canônica (`:324-326`) e limite duro de no máximo um `h1_id` não nulo por chunk (`:327-328`).
  - `resolve_citation_spans` (`:331-347`) revalida a proveniência do chunk, confere `selection.chunk_id` e `selection.canonical_id`, exige que `[selection.start, selection.end)` esteja contido em `[mapping.start, mapping.end)` (`:339-340`), exige `selection.evidence == canonical.text[selection.start:selection.end]` (`:341-342`) e retorna apenas os `ResolvedSpan` que intersectam a seleção (`s.start < selection.end and selection.start < s.end`), recortando `[max(s.start, selection.start), min(s.end, selection.end))` e preservando `page`, `section_path` e `provenance` reais de cada span (sem propagar a página inicial do chunk para trechos em páginas seguintes).

### 2.3. Auditoria append-only, etapas/outcomes e sanitização (`ING-AUD-001`)

- **Metadados sanitizados do juiz (`ingestao.py:20-28,168-184`)**:
  - `JudgeMetadata` exige `provider`, `model`, `config_version`, `config_sha256`, `prompt_version`, `prompt_sha256`, `request_id: SafeId | None` (`^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$`, bloqueando URLs/query strings/tokens), `result: Literal["injection", "safe", "error"]` e `error_kind: ErrorCode | None`.
  - `_result` (`:179-183`) garante `(self.result == "error") == (self.error_kind is not None)`: erro/timeout do juiz exige `error_kind` e nunca se converte em `"safe"`; campos extras como `reason` bruto ou prompt são rejeitados por `extra="forbid"`.
- **Eventos de auditoria por etapa (`ingestao.py:186-243`)**:
  - `IngestionAuditEvent` impede autorreferência em `(triage_event_id, judge_event_id, supersedes_decision_id)` (`:208-210`), duplicidade em `heuristic_rule_ids` (`:211-212`) e uso de `supersedes_decision_id` fora de `stage == "decision"` (`:213-214`).
  - Em `stage == "triage"` (`:215-222`): proíbe `triage_event_id`, `judge_event_id` e `judge_metadata`; exige `(outcome="clean", reason_code="no_suspicion", heuristic_rule_ids=())` ou `(outcome="suspect", reason_code="suspected", len(heuristic_rule_ids) >= 1)`.
  - Em `stage == "judge"` (`:223-228`): exige `triage_event_id`, proíbe `judge_event_id`, exige `judge_metadata` e vincula biunivocamente `outcome -> reason_code` (`safe -> judge_release`, `injection -> judge_block`, `error -> judge_unavailable`) e `outcome == judge_metadata.result`.
  - Em `stage == "decision"` (`:229-243`): exige `triage_event_id`, proíbe duplicar `judge_metadata`, vincula `reason_code -> outcome` (`no_suspicion`/`judge_release` -> `allowed`; `rule_block`/`judge_block` -> `blocked`; `judge_unavailable`/`legacy_insufficient` -> `unavailable`), exige `judge_event_id` para todo `reason_code` iniciado por `judge_` (`:239-240`) e proíbe `judge_event_id` quando `reason_code == "no_suspicion"` (`:241-242`), permitindo `judge_event_id` opcional em `rule_block` (quando heurística de alta precisão bloqueia com ou sem chamada ao juiz registrada na trilha).

### 2.4. Reserva/fencing, decisões efetivas, reconciliação e inventário (`ING-TXN-001`, `ING-REP-001`)

- **Reserva com fencing (`ingestao.py:148-166`)**:
  - `IngestionReservation` modela `owner_id`, `source_sha256`, `document_version_id`, `phase` (`reserved`, `parsing`, `persisted`, `indexing`, `ready`, `failed`), `generation: Positive`, `lease_until: AwareDatetime`, `attempt_id` e `error_code: ErrorCode | None`, exigindo `(phase == "failed") == (error_code is not None)` (`:163-164`).
- **Entrada de embedding reproduzível (`ingestao.py:129-146`)**:
  - `EmbeddingInputRecord` persiste `title`, `section_context`, `text_sha256`, `format_version`, `embedding_key` e `input_sha256`, e fornece `validate_input(text, formatted_input)` (`:140-145`) para conferir os hashes UTF-8 do texto do chunk e da string produzida pelo formatter existente, sem criar um segundo formatter.
- **Decisão efetiva, checkpoint correlacionado e inventário de órfãos (`ingestao.py:246-295`, `docs/adr/0015-canonico-decisoes-indice.md:14-17`)**:
  - `EffectiveChunkDecision` (`:246-253`) exige `decision_event_id`, `revision: Positive`, `allowed: bool` estrito (`Field(strict=True)`) e `policy_version`, substituindo a leitura direta da flag histórica `ChunkRecord.injection_flagged`.
  - `ReconciliationJob` (`:255-274`) modela checkpoint de negócio correlacionado ao job do Procrastinate (sem criar fila paralela, compatível com ADR-0012 e ADR-0015 §4), exigindo `error_code` sanitizado se e somente se `state in ("retry", "blocked")`.
  - `OrphanInventoryEntry` (`:276-295`) exige `fixture_manifest_ref`, `owner_id` e `version_id` não nulos quando `classification == "fixture_proven"` (`:290-293`), impedindo classificar ponto como fixture descartável apenas por prefixo ou anti-join.

### 2.5. Coerência de exports, JSON Schemas e testes (`packages/contracts/`)

- Todos os 13 modelos novos (`CanonicalTextRecord`, `CanonicalSpan`, `ChunkProvenanceRecord`, `CitationSpanSelection`, `EmbeddingInputRecord`, `IngestionReservation`, `JudgeMetadata`, `IngestionAuditEvent`, `EffectiveChunkDecision`, `ReconciliationJob`, `OrphanInventoryEntry`, `ResolvedSpan`, `TextRange`) e os 3 helpers (`validate_canonical_spans`, `validate_chunk_provenance`, `resolve_citation_spans`) estão exportados em `packages/contracts/src/training_contracts/__init__.py:31-48,72-89`.
- Todos os 13 modelos estão registrados em `packages/contracts/src/training_contracts/schema.py:11-24,48-62` e seus respectivos arquivos `.schema.json` em `packages/contracts/schema/` possuem `"additionalProperties": false` e tipos/padrões coerentes com os modelos Pydantic.
- `packages/contracts/tests/test_ressalvas_contratos.py:1-342` cobre diretamente os casos de contrato de `C03`/`ING-PRO-001`, `C04`/`ING-CAN-001`, `C12`/`ING-AUD-001`, `ING-TXN-001` e `ING-REP-001` sem `skip`, `xfail` ou mock de produto.

---

## 3. Achados e limites

- **Achados bloqueantes ou de retrabalho em T-010**: **Nenhum (0)**.
- **Limites explícitos desta aprovação**:
  1. Esta revisão aprova **exclusivamente T-010** (contratos Pydantic, helpers puros em memória, exports, 13 JSON Schemas e ADR-0015).
  2. Não valida nem aprova persistência relacional/migrations/triggers/downgrade (`T-011`), parser Docling/chunker estrutural e Unicode (`T-012`), protocolo transacional/fencing/CAS em banco (`T-013`), reconciliação/Qdrant/leitura autoritativa/inventário (`T-014`/`T-016`), guarda de logs LiteLLM e 9 ataques com juiz real (`T-015`) nem a prova final integrada e diff RAGAS (`T-017`).
  3. Não aprova o lote de 16 ressalvas como concluído e não substitui a revisão `seguranca-llm` nas tarefas que tocam guardrail/prompt nem o Gate G4 do humano.

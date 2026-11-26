# Revisão `seguranca-llm` (Google) — T-014 e Reavaliação T-013 (`2026-11-24-correcao-integral-ressalvas`)

- **Papel**: `seguranca-llm` (`governanca/papeis/seguranca-llm.md`)
- **Provedor efetivo**: `google` (Antigravity / Gemini 4 Argon; dispatch `ctx_0501a76d93dc`, task `task_c8b07844f4e5` — fallback efetivo pois `anthropic`/Opus está indisponível por créditos e o autor é `openai` `ctx_df5af613a5b9`)
- **Arquivos auditados (snapshot T-014 + reavaliação T-013)**:
  - `packages/ingestion/src/training_ingestion/reconciliation.py` (`sha256: d1f5f627fc6b106a9a75f1dcd02d85e1f627f8e0d24c5bd9ae76db16b0399dbe`)
  - `packages/ingestion/src/training_ingestion/indexing.py` (`sha256: d5ef08f7b42fefdc60ce1c70cc7ad972a7fbf7cf1a83e465f92f708521c35b27`)
  - `packages/ingestion/src/training_ingestion/retrieval/eligibility.py` (`sha256: 39cd1767994b52d7f14de7f5128edebee9ba6f32fd0f4c4fb9d008c93ead024e`)
  - `packages/lineage/src/training_lineage/repository.py` (`sha256: 9c804d5f9a1fbd34a37d3eb8d51e10ad60ed5ef2d4db62854bd88c0009aefca1`)
  - `packages/ingestion/tests/test_ressalvas_reconciliacao.py` (`sha256: 58805f6b8f3b395d6e5936e49203c8645a422114490fb19a1abdca1eb9361582`)
  - `packages/ingestion/tests/test_ingestion.py` (`sha256: 4f779f336881090b896f371dc5cdf08b155d7d5aeb9597539d042333524d6d99`)
- **Veredito `seguranca-llm`**: **aprovado** (0 achados de segurança nos mecanismos de leitura autoritativa, reconciliação vetorial, proteção contra vazamento de candidatos bloqueados/stale/de outro dono, inventário/remoção de órfãos e sanitização de erros).

---

## Avaliação por categoria de risco (`governanca/papeis/seguranca-llm.md` — OWASP Top 10 for LLM 2025)

### 1. LLM01 — Prevenção de Injeção Indireta na Recuperação (Barreira Autoritativa Postgres sobre Qdrant)

- **Qdrant tratado exclusivamente como cache não autoritativo (`packages/ingestion/src/training_ingestion/retrieval/eligibility.py:18-72`)**:
  - `SafeRetriever.search` filtra inicialmente no Qdrant por `owner_id`, `embedding_key` e `injection_flagged == False` (`:28-32`) e exige presença de `chunk_id`, `decision_revision` (`int`), `decision_event_id` e `input_sha256` no payload (`:36-40`).
  - Antes de devolver qualquer texto ao consumidor/modelo, consulta obrigatoriamente o Postgres via `LineageRepository.authorize_candidates(owner_id, candidates.keys(), candidates)`, `get_effective_decisions` e `load_embedding_inputs` (`:47-52`).
  - Se um chunk foi reclassificado como bloqueado (`allowed=False`) ou indisponível (`outcome='unavailable'`), ou se o ponto no Qdrant pertence a uma revisão obsoleta (`payload.get('decision_revision') != decision.revision` ou `payload.get('decision_event_id') != decision.decision_event_id`), ou se o texto/seção/hash no payload do Qdrant foi adulterado em relação ao Postgres (`:60-70`), o candidato é descartado.
- **Falha fechada (Fail-Closed) quando o banco autoritativo falha (`eligibility.py:46-54`)**:
  - Qualquer exceção ao consultar o Postgres em `SafeRetriever.search` levanta `EligibilityUnavailable('Linhagem indisponível para autorizar candidatos') from None`, sem jamais liberar pontos do Qdrant por fallback e sem encadear detalhes internos da exceção de banco (`test_safe_retrieval_fails_closed_when_authoritative_store_fails`, `test_ressalvas_reconciliacao.py:312-317`).
- **Transição `blocked → allowed` e `allowed → blocked` sem janela de exposição indevida (`reconciliation.py:99-150`, `test_ressalvas_reconciliacao.py:193-250`)**:
  - Um chunk reclassificado como bloqueado ou com falha do juiz deixa de ser autorizado por `authorize_candidates` **imediatamente** no commit do evento no Postgres, antes mesmo de o ponto ser removido do Qdrant (`test_C14_reclassification_error_and_legacy_missing_input_fail_closed`, `:215-226`).
  - Um chunk reclassificado de `blocked` para `allowed` permanece invisível em `SafeRetriever.search` (`== []`) até que `Reconciler.reconcile_version` valide a entrada de embedding, grave o ponto com `decision_event_id`/`decision_revision`/`input_sha256`, verifique o lote em `_verify_batch`, registre `record_index_ack` e publique `publish_ready` (`test_C14_blocked_to_allowed_reconciles_before_public_read`, `:193-213`).
- **Legado sem contexto verificável bloqueado por omissão (`reconciliation.py:103-116,203-223`)**:
  - Chunks históricos sem `ChunkProvenanceRecord` `exact` ou sem `EmbeddingInputRecord` não recebem título vazio nem vetor fabricado: são marcados como `mapping_status='insufficient'` e recebem decisão `outcome='unavailable', reason_code='legacy_insufficient'`, permanecendo bloqueados (`state='blocked'`) e inacessíveis na busca.

### 2. Isolamento Multi-Tenant (`owner_id`) e Deleção Escopada no Qdrant

- **Verificação estrita de ownership antes de remover pontos derivados (`indexing.py:170-210`)**:
  - `QdrantIndex.delete_owned_point` e `delete_owned_points` não apagam cegamente por UUID: primeiro lêem os pontos via `client.retrieve(..., with_payload=True, with_vectors=False)`, verificam que `owner_id`, `document_version_id`, `chunk_id` e `embedding_key` coincidem com o escopo autorizado e executam a remoção com `FilterSelector` conjuntivo incluindo `owner_id`.

### 3. Proteção de Dados / Inventário Read-Only e Remoção Restrita a Fixtures Comprovadas

- **Sem cópia de texto/vetores no inventário e sem exclusão por prefixo ou anti-join (`reconciliation.py:224-282`)**:
  - `inventory_orphans` lê apenas metadados de payload (`with_vectors=False`) e armazena somente `payload_digest` (SHA-256 de 64 hex), `owner_id`, `version_id`, `point_id` e `classification` em `OrphanInventoryEntry`.
  - `execute_orphan_deletion` recusa qualquer execução sem callbacks explícitos `quiescence_check` e `authorization`, recusa inventários com mais de 300 segundos, revalida o digest global da coleção antes de apagar, recusa se houver qualquer ponto `unknown` no inventário e revalida o `payload_digest` de cada ponto antes do `delete`. Nenhum ponto da coleção compartilhada foi removido.

### 4. Sanitização de Exceções e Logs (`LLM07` / Dados Sensíveis)

- Todas as exceções de `Reconciler` (`ReconciliationError`, `ReconciliationLeaseLost` em `reconciliation.py:49-50,151-163`) e de `SafeRetriever` (`EligibilityUnavailable` em `eligibility.py:53-54`) usam `from None` e mensagens ou códigos constantes (`ErrorCode`), impedindo que payloads de documentos, vetores ou strings de conexão vazem em tracebacks (`test_C13_upsert_without_ack_is_inaccessible_then_resumes` `:155,159-160`, `test_C14_reclassification_error_and_legacy_missing_input_fail_closed` `:226`, `test_safe_retrieval_fails_closed_when_authoritative_store_fails` `:315-317`).

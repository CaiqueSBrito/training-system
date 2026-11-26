# Interfaces e invariantes entregues a T-014

Fonte única dos contratos: `training_contracts.ingestao` e `LineageRepository` T-011. As operações abaixo são síncronas; o consumidor deve criar uma transação curta para cada chamada de repositório e nunca mantê-la durante juiz, embedding ou Qdrant.

- `Ingestor.ingest(path, owner_id, *, document_id=None) -> IngestionResult`: fluxo público I1–I6; `IngestionBusy` comunica reserva ativa, `IngestionLeaseLost` fencing obsoleto, `IngestionStageError` categoria segura (`category`, opcional `status_code`/`retryable`). O retorno expõe somente chunks autorizados após ready.
- `Ingestor.reclassify(owner_id, document_version_id) -> dict[chunk_id, EffectiveChunkDecision | None]`: anexa avaliações; novas decisões invalidam ack anterior. Proveniência/embedding input ausentes produzem indisponibilidade `legacy_insufficient`, sem gerar título ou fonte.
- `LineageRepository.get_latest_reconciliation_job(owner_id, version_id, embedding_key) -> ReconciliationJob | None`: retorna a maior `desired_revision` sob escopo do dono.
- `list_reconciliation_chunks(owner_id, version_id, *, cursor=None, limit=128)`, `load_embedding_inputs(owner_id, chunk_ids, *, embedding_key=None)`, `get_effective_decisions(owner_id, chunk_ids)`, `get_assessment_revisions(owner_id, chunk_ids)`: leitura em lotes; falha/ausência é indisponível, sem permitir por omissão.
- `reproduce_embedding_input(owner_id, chunk_id, embedding_key, *, format_input) -> str`: verifica hash de texto e bytes formatados antes de gerar vetor.
- `record_index_ack(owner_id, chunk_id, embedding_key, *, reservation_id, generation, decision_revision)`: ack somente com reserva vigente e revisão auditável atual.
- `checkpoint_job(record, *, expected_attempt, expected_cursor, reservation_id, generation)` e `publish_ready(reservation_id, generation, *, owner_id, embedding_key)`: CAS de checkpoint e publicação; geração vencida não confirma.
- `authorize_candidates(owner_id, candidate_ids, observed_revision) -> dict[chunk_id, ChunkRecord]`: barreira final autoritativa; exige dono, ready, revisão observada atual e todos os receipts/projeções completos.

Responsabilidade T-014: acrescentar ao payload derivado de Qdrant os identificadores/revisões/hash de embedding exigidos pelo contrato, verificar candidatos por lotes e chamar `authorize_candidates` antes de entregar texto. O payload Qdrant continua cache, não autoridade. Não reutilizar caminho `_PartialIngestor`; ele foi removido.

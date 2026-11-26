# Reavaliação independente (Google) — Rodada 2 de T-013 (`2026-11-24-correcao-integral-ressalvas`)

- **Tarefa**: T-013 (`2026-11-24-correcao-integral-ressalvas`) — Construir reserva durável e trilha de avaliação
- **Provedor efetivo do autor (mantido)**: `openai` (`rag-backend`, dispatch inicial `ctx_d18611b3f44b`, complementado na slice autorizada `ctx_df5af613a5b9`)
- **Provedor efetivo da revisão (técnica + `seguranca-llm`)**: `google` (Antigravity / Gemini 4 Argon; dispatch `ctx_0501a76d93dc`, task `task_c8b07844f4e5`)
- **Histórico preservado**: A revisão da Rodada 1 (`ctx_bb88a686b3d5`, em `evidencias/T-013/revisao-google.md` e `evidencias/T-013/revisao-seguranca-llm-google.md`) permanece integralmente preservada sem edição.
- **Veredito da Rodada 2 para T-013**: **aprovado** (ambos os achados da Rodada 1 foram integralmente resolvidos e provados em Postgres e Qdrant reais descartáveis).

---

## Verificação ponto a ponto dos achados da Rodada 1 (`arquivo:linha`)

1. **[Resolvido] Achado 1 da Rodada 1 — Cutover de callers de `_PartialIngestor` em `packages/ingestion/tests/test_ingestion.py`**:
   - **Evidência (`packages/ingestion/tests/test_ingestion.py:14-18,24-72,115-143,195-219,227-250,302-306`, `sha256: 4f779f336881090b896f371dc5cdf08b155d7d5aeb9597539d042333524d6d99`)**:
     - O import quebrado `from training_ingestion.core import _PartialIngestor as Ingestor` foi removido. `test_ingestion.py:14-17` importa `Ingestor` diretamente da API pública `training_ingestion`, sem reintroduzir shim `_PartialIngestor` em `core.py`.
     - As chamadas de ingestão em `test_ingestion.py` (`:121, 127, 199, 207, 229, 243, 244`) passam `processor=processor` (instância real de `IndexingPipeline` conectada a container Qdrant descartável e dublê externo `_ExternalModels` restrito ao arquivo de teste).
     - Os 26 casos de `test_ingestion.py` foram coletados e aprovados (`evidencias/T-014/pytest.txt:9`).

2. **[Resolvido] Achado 2 da Rodada 1 — Cobertura executável dos ramos de `ING-LEG-001` / `C14` (`blocked → allowed`, falha na reclassificação e legado sem `embedding_input`)**:
   - **Evidência (`packages/ingestion/tests/test_ressalvas_reconciliacao.py:193-250`, `sha256: 58805f6b8f3b395d6e5936e49203c8645a422114490fb19a1abdca1eb9361582`; `evidencias/T-014/pytest-complemento.txt:1-20`)**:
     - `test_C14_blocked_to_allowed_reconciles_before_public_read` (`:193-213`) comprova a reclassificação `blocked → allowed` via `Ingestor.reclassify`, a exigência de reconciliação antes da visibilidade em `SafeRetriever.search` e a igualdade byte a byte de `DocumentVersionRecord` e `ChunkRecord` antes e depois da reclassificação.
     - `test_C14_reclassification_error_and_legacy_missing_input_fail_closed` (`:215-250`) comprova: (a) que erro/timeout do juiz durante `Ingestor.reclassify` lança `IngestionStageError` sanitizado, grava decisão `outcome='unavailable'` sem texto bruto da exceção e revoga imediatamente a leitura pública em `SafeRetriever.search`; e (b) que versão/chunk legado sem `CanonicalTextRecord`/`EmbeddingInputRecord` recebe `mapping_status='insufficient'` e decisão `outcome='unavailable', reason_code='legacy_insufficient'`, retornando estado `'blocked'` sem fabricar título, contexto ou vetor.

---

## Limites

- A aprovação de T-013 na Rodada 2 encerra a revisão independente do protocolo durável I1–I6 e da trilha de auditoria/reclassificação em Postgres/Qdrant descartáveis, mas não substitui a execução dos 9 ataques contra o juiz LLM real (`T-015`, aguardando confirmação de rotação da `GEMINI_API_KEY`), a prova integrada final e diff RAGAS (`T-017`, `Bloqueado`) nem o Gate G4 humano.

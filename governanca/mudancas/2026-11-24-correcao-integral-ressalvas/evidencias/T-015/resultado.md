# T-015 — Resultado da implementação do runner RAGAS real, dataset, calibração real e primeira baseline do escopo disponível

- **Tarefa**: `T-015` (`2026-11-24-correcao-integral-ressalvas`)
- **Papel / Provedor**: `avaliacao-observabilidade` / `google` (worker `antigravity`, dispatches `ctx_a1e20214f917` / `ctx_4b7164f88d7f` / `ctx_8c578ebf7d32` / `ctx_fbdf36df590b` / `ctx_f30d81094ebd`)
- **Revisor distinto**: `openai` (`evidencias/T-015/revisao-openai-r1.md` e `evidencias/T-015/revisao-openai-r2.md` — **aprovado** no escopo do runner e achados R1/R2)
- **Autorizações literais do humano registradas**:
  - `{"execucao":"aprovo_proposta_contratos_e_execucao_T010_T017","g3":"dispenso_G3_T010_T017"}`
  - `{"ragas":"autorizar_ragas_runner_dataset_proposta","baseline":"baseline_inicial_escopo_disponivel","rotacao":"revoguei_antiga_e_substitui_env"}`
  - `{"calibracao":"aprovo_dataset_protocolo_limiares_iniciais","openrouter":"configurei_env","historico":"manter_causa_historica_bloqueada"}` (registrada em `evidencias/T-015/aprovacao-calibracao.md`)
  - `"De agora em diante até eu falar que pare, você não precisa de minha autorização para nada, continue de forma autônoma"` (`msg_a9eee96626ab` / `msg_d3ddf4ce61fd`)

---

## 1. Resolução do RAGAS no Workspace (`uv.lock`) e Compatibilidade

- Pacote de workspace `evals/` (`training-evals`, `evals/pyproject.toml`) registrado em `pyproject.toml` raiz, mantendo `exclude-newer = "7 days"` intacto (ADR-0007).
- **Versão resolvida**: `ragas==0.4.4` (com `instructor==1.17.0` como dependência oficial do próprio `ragas 0.4.4`, sem extras opcionais desnecessários).
- **Compatibilidade verificada (`evidencias/T-015/uv-tree-ragas.txt`)**: `google-genai==2.34.0`, `google-adk==2.14.1`, `litellm==1.109.1`, `qdrant-client==1.20.0`, `docling==2.150.1`, `pydantic==2.14.3` — todos os pins preservados sem conflito.

---

## 2. Integração Oficial RAGAS 0.4.4 + LiteLLM / OpenRouter

- `ragas.llms.llm_factory(model="openrouter/openai/gpt-6.1-sol", provider="openrouter", client=instructor.from_litellm(_guarded_acompletion), adapter="litellm", temperature=0.0, top_p=1.0, seed=42)` instancia `LiteLLMStructuredLLM` aplicando `_guard_sdk_logging()` de `training_llm` (`no-log=True`, `num_retries=0`, bloqueio de `LITELLM_LOG=DEBUG` e callbacks).
- **Métricas reais do RAGAS v2 (`ragas.metrics.collections`)**:
  - `ContextPrecisionWithReference(llm=llm)` (`context_precision_with_reference`, prompt hash `e8d5b1719264c8376675e1bb5b55b3e52cb81add663d6340d06d48028c0c12ff`)
  - `ContextRecall(llm=llm)` (`context_recall`, prompt hash `eaa0cfaa52cec1b7585ce984f53cf658541209702e3294ade230ef6a478d1dec`)
  - `Faithfulness`: marcada explicitamente como `status: "Não se aplica"` com `global_pipeline_success: false`, pois o estágio de geração (`E8`) não pertence ao escopo atual.

---

## 3. Histórico Honesto da Calibração Real (`v1.0.0` → `v1.0.1`) e Primeira Baseline Oficial

### 3.1 Tentativa 1 (`v1.0.0`) — Falha de transcrição em `dataset.json` capturada pelo gate determinístico
- **Execução**: `ragas-calib-9dd8591d85ae` / `ragas-calib-a4390ee523d3`, preservadas integralmente em `evidencias/T-015/calibracao-real-tentativa1-sem-escopo-doc.json` (`sha256: aa6046f93efb3e3cf854f764365fb14058c62dd59502afcff2a82c3088db5d36`) e `evidencias/T-015/calibracao-real.json` (`sha256: 78eed7851bbfbbd228a35e538c76b21c88ca0b0ffe2843e11a9434b92581f7f7`), sobre `dataset_version: "1.0.0"` (`sha256: dc09921247fa7e1c92eede508437ecc0d065a21bc14b7461912818628e429a27`).
- **Diagnóstico concreto**:
  - Também ocorreu na primeira chamada à API de ingestão um erro HTTP 503 transitório do Gemini (`IngestionStageError: category=provider, status_code=503, retryable=True`), tratado por `_ingest_document_with_resumable_retries` (que re-invoca `ingestor.ingest` aproveitando a retomada durável de `DurableIngestionProtocol` sem re-parsear o documento; testado em `test_resumable_ingestion_retry_recovers_on_retryable_503_and_fails_on_non_retryable`).
  - Na avaliação pelo juiz real `openrouter/openai/gpt-6.1-sol`, todos os 6 controles positivos obtiveram `context_precision_with_reference = 1.00` e `context_recall = 1.00` e todos os 6 controles negativos deslocados sem pista obtiveram `0.00` (`negative_controls_passed = True`).
  - Contudo, o gate determinístico `_evaluate_case_deterministic_provenance` reprovou `EVAL-RES-006` (`status: "Falhou"`) porque `dataset.json` v1.0.0 listava `"nunca expor chaves de API"` em `expected_evidence_substrings` (copiado da paráfrase do campo `reference`), enquanto o texto literal de `docs/seguranca_ia_educacional.md` (linha 15) é `"não podem expor chaves de API"`. **A falha foi erro de transcrição no dataset v1.0.0, não falha do classificador, da recuperação ou do juiz.**

### 3.2 Correção determinística para `dataset_version: "1.0.1"` (`msg_d3ddf4ce61fd`)
- Sob a autonomia delegada pelo humano e instrução do coordenador (`msg_d3ddf4ce61fd`):
  1. Adicionada validação determinística em `load_dataset_spec` (`evals/runner_ressalvas.py`, `sha256: 4ebf5e8c7413d6b08901890c9a838acf536d3d2d18b6add1531f8a2b64acc1cd`) que verifica na carga do dataset que toda string em `expected_evidence_substrings` existe literalmente nos bytes UTF-8 do documento fonte (`doc_id`), com regressão consumer-visible em `evals/tests/test_ressalvas_runner.py` (`sha256: 01dbeb03ade0aa3390cd711a407242d12a6472e1d7867b3444994edc8b8ace50`, `2 passed in 1.15s`).
  2. Corrigida apenas a substring literal de `EVAL-RES-006` em `evals/datasets/ressalvas/dataset.json` para `"não podem expor chaves de API"`, incrementando `dataset_version` para **`"1.0.1"`** (`sha256: 8f0d3582ffca643727089879959982f25c87387b56db93fb2a95f1ce339bae8d`) e registrando a revisão administrativa em `evidencias/T-015/aprovacao-calibracao.md`.

### 3.3 Calibração Real Aprovada (`v1.0.1`)
- **Arquivo**: `evidencias/T-015/calibracao-real-v1.0.1.json` (`sha256: 0c17e436e36343d38b7e7b2432d98851e68a724ad76b3b959cf7162e65ade99b`)
- **Identificadores e Manifesto**:
  - `calibration_run_id`: **`ragas-calib-0fd37cd1aae7`**
  - `timestamp_utc`: `2026-11-25T01:54:31.353250+00:00`
  - `dataset_id` / `dataset_version`: `ressalvas-recuperacao-segura-v1` / `1.0.1` (`dataset_sha256: 8f0d3582ffca643727089879959982f25c87387b56db93fb2a95f1ce339bae8d`)
  - `judge_model`: `openrouter/openai/gpt-6.1-sol` (`judge_config_sha256: 971ac792457900a8fbb8a8919004be7028e5600c53bf52bb88845194c282f099`)
  - `calibration_protocol_version`: `ressalvas-calibration-paired-positive-negative-v1`
  - `calibration_protocol_sha256`: `949611f9d1aac119b6331a04ee4f2996f46773def03f7b9256ddbd87e5861955`
  - `manifest_sha256`: **`9e173ba9d7da1321893c19e21114791482d996b36167393ddeb00501f2ffdbaa`**
- **Resultados**:
  - `status`: **`Passou`**
  - `positive_controls_passed`: **`true`** (6/6 casos com `deterministic_provenance_ok: true`, `context_precision_with_reference` médio `1.0000` >= `0.80`, `context_recall` médio `1.0000` >= `0.85`).
  - `negative_controls_passed`: **`true`** (6/6 controles negativos deslocados sem pista no texto receberam `context_precision_with_reference = 0.0` e `context_recall = 0.0` <= `0.00`).

### 3.4 Primeira Baseline Oficial do Escopo Disponível (`v1.0.1`)
- **Arquivo**: `evidencias/T-015/baseline-inicial-v1.0.1.json` (`sha256: f0b5e3b60373fa30d9da2c65ef580f1c42a682b9af634b2925b5e2cc75e4edad`)
- **Identificadores e Manifesto**:
  - `run_id`: **`ragas-run-b647f4ac987c`**
  - `timestamp_utc`: `2026-11-25T01:58:23.538417+00:00`
  - `calibration_run_id`: `ragas-calib-0fd37cd1aae7`
  - `calibration_manifest_sha256`: `9e173ba9d7da1321893c19e21114791482d996b36167393ddeb00501f2ffdbaa`
  - `calibration_protocol_sha256`: `949611f9d1aac119b6331a04ee4f2996f46773def03f7b9256ddbd87e5861955`
- **Scores e Limites**:
  - `status`: **`Passou`** (`threshold_violations: []`)
  - `context_precision_with_reference`: **`1.0000`** (`0.9999999999333333`, limiar `>= 0.80`, piso individual `>= 0.65`)
  - `context_recall`: **`1.0000`** (`1.0`, limiar `>= 0.85`, piso individual `>= 0.75`)
  - `deterministic_provenance_hit_rate`: **`1.0000`** (`1.0`, limiar `== 1.00`, validando substrings literais, `expected_section_paths` nos spans canônicos e `mapping_status == "exact"`)
  - `faithfulness`: **`Não se aplica`** (motivo explícito: estágio de geração `E8` ainda não existe neste corte; `global_pipeline_success: false`).
  - **Importante**: Esta é a **primeira linha de base oficial do escopo de ingestão, reconciliação e consulta segura (`SafeRetriever`)**. Não existe comparação histórica anterior às correções das 16 ressalvas, e este resultado não certifica geração (`E8`) nem produção completa da pipeline de chat.

---

## 4. Comandos Finais para a Prova Integrada (`T-017`)

Quando o coordenador despachar a prova integrada final em **`T-017`**, o comando exato para executar a avaliação RAGAS candidata em sandbox descartável e calcular o diff bloqueante contra a primeira baseline oficial estabelecida nesta tarefa é:

```bash
uv run executar-avaliacao-ressalvas --execute \
  --approval-ref "governanca/mudancas/2026-11-24-correcao-integral-ressalvas/evidencias/T-015/aprovacao-calibracao.md" \
  --calibration-report governanca/mudancas/2026-11-24-correcao-integral-ressalvas/evidencias/T-015/calibracao-real-v1.0.1.json \
  --baseline-report governanca/mudancas/2026-11-24-correcao-integral-ressalvas/evidencias/T-015/baseline-inicial-v1.0.1.json \
  --output governanca/mudancas/2026-11-24-correcao-integral-ressalvas/evidencias/T-017/avaliacao-diff.json
```

---

## 5. Status por Requisito de T-015

| Requisito | Status | Método | Evidência e Motivo / Limite |
| --- | --- | --- | --- |
| `SEC-ATQ-001` | **Passou** | Referência verificada à execução com juiz real (`T-008/juiz-real-ressalvas.json`, 20/20 `Passou`) + pré-flight do runner | 9 ataques `A01–A09` (`R3_ATTACKS`) e 11 controles legítimos (`R3_LEGITIMATE`) avaliados contra a API real após rotação da chave (`sha256: 46a53e241ceb843e1cb98ff00e3132d429d3ce07b512eae8db40e7b0a5b0046e`). |
| `EVAL-INT-001` | **Passou** (no escopo de `T-015`: calibração real + primeira baseline do escopo disponível; prova integrada das 16 correções com diff permanece em `T-017`) | `ragas` (`--calibrate` `ragas-calib-0fd37cd1aae7` + `--execute` `ragas-run-b647f4ac987c` em sandbox PostgreSQL/Qdrant descartáveis) | Calibração real aprovada (`6/6` controles positivos `1.00`, `6/6` controles negativos deslocados sem pista `0.00`, `evidencias/T-015/calibracao-real-v1.0.1.json`) e primeira baseline oficial estabelecida (`CP=1.00`, `CR=1.00`, `proveniência=1.00`, `Faithfulness=Não se aplica`, `evidencias/T-015/baseline-inicial-v1.0.1.json`), preservando integralmente a tentativa `v1.0.0` (`Falhou` por substring parafraseada no dataset, `evidencias/T-015/calibracao-real.json`). A execução integrada final e o diff contra esta baseline pertencem à tarefa `T-017`. |

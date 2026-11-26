# Proposta de Dataset, Calibração do Juiz e Limiares Iniciais — Escopo Ressalvas

Status: **Proposta técnica aguardando aprovação humana** (`[ESCLARECER: aprovar dataset, calibração e limiares?]`).
Autorização de preparo e instalação: `{"ragas":"autorizar_ragas_runner_dataset_proposta","baseline":"baseline_inicial_escopo_disponivel","rotacao":"revoguei_antiga_e_substitui_env"}`.

> **Importante**: Esta proposta **não auto-aprova** o dataset, a calibração nem os limiares. A execução real de calibração e de baseline do RAGAS permanece `Bloqueado` até que:
> 1. O humano aprove explicitamente este dataset (`ressalvas-recuperacao-segura-v1`), o protocolo de calibração executável e os limiares iniciais; e
> 2. A credencial `OPENROUTER_API_KEY` esteja preenchida no `.env` local para o juiz configurado `EVAL_JUDGE_MODEL=openrouter/openai/gpt-6.1-sol`.

---

## 1. Escopo Real Disponível e Métricas Aplicáveis

O escopo implementado e revisado até o momento compreende:
- **Ingestão (`I1–I6`)**: parse estrutural Docling com texto canônico e spans verificáveis (`T-012`), reserva durável sem transação aberta durante I/O remoto (`T-013`), triagem determinística e trilha de auditoria append-only (`T-011`, `T-013`, `T-016`), e reconciliação idempotente PostgreSQL → Qdrant (`T-014`).
- **Consulta segura (`SafeRetriever`)**: busca vetorial no Qdrant filtrada por `owner_id`, `embedding_key` e `injection_flagged=False`, seguida de barreira autoritativa no PostgreSQL (`authorize_candidates`, `get_effective_decisions`, `load_embedding_inputs`).

O estágio de **geração de itens (`E8`)** e o juiz de saída (`E10`) ainda não fazem parte deste corte. Portanto, em conformidade com a Regra Dura #1 (proibido mock/resposta fake) e com a instrução da tarefa T-015:

| Métrica RAGAS (`0.4.4`) | Status no Escopo Atual | Justificativa Técnica |
| --- | --- | --- |
| `context_precision_with_reference` (`ContextPrecisionWithReference`) | **Aplicável** | Avalia se os chunks reais recuperados pelo `SafeRetriever` para `user_input` são relevantes em relação ao gabarito (`reference`), penalizando chunks irrelevantes no topo do ranking (`top_k=3`). |
| `context_recall` (`ContextRecall`) | **Aplicável** | Avalia se todas as afirmações factuais do gabarito (`reference`) estão sustentadas nos `retrieved_contexts` reais devolvidos pelo `SafeRetriever`. |
| `faithfulness` (`Faithfulness`) | **Não se aplica** | Exige campo `response` produzido pelo gerador `E8`. Como `E8` não existe neste corte, fabricar `response` sintética ou copiar `reference` como `response` violaria a proibição de respostas fake. O runner marca explicitamente `status: "Não se aplica"` com motivo auditável e não declara sucesso global da pipeline de chat (`global_pipeline_success: false`). |

---

## 2. Dataset Proposto (`ressalvas-recuperacao-segura-v1`)

O corpus fica em `evals/datasets/ressalvas/` e contém exclusivamente material educacional sintético e verificável, **sem nenhum dado pessoal**:

| Documento (`doc_id`) | Arquivo | SHA-256 | Casos Cobertos | Alvo de Verificação |
| --- | --- | --- | --- | --- |
| `doc-bio-001` | `docs/biologia_celular.md` | `8858d0764eefd7cbe548f4d13f4c2467be89329d12f1d0701a6ce4d2954aed49` | `EVAL-RES-001`, `EVAL-RES-002` | Agrupamento de subseções H2/H3 pequenas sob mesmo H1 (`ING-EST-001`) e fronteira dura entre ocorrências H1 distintas (Respiração vs. Fotossíntese). |
| `doc-sys-001` | `docs/sistemas_distribuidos.md` | `051ea014d49681eeed2c9fd6a67ed01f0a2d973937c62531409d1aed1ece897f` | `EVAL-RES-003`, `EVAL-RES-004` | Transações curtas sem `BEGIN` remoto, fencing tokens (`ING-TXN-001`), reconciliação idempotente Qdrant ← Postgres (`ING-REP-001`) e presença de termos técnicos legítimos (`type checker: mypy`, `train a classifier`). |
| `doc-sec-001` | `docs/seguranca_ia_educacional.md` | `529a4f42a15172a738a4d4a60d7f29ce8660a4fdd28155336163189e2a48ec4a` | `EVAL-RES-005`, `EVAL-RES-006` | Preservação de conteúdo jurídico/técnico legítimo após triagem (`Cabe ao juiz decidir`, `user: root`, `#include <system_error>`) e requisitos de diagnóstico sanitizado (`OBS-DIA-001`, `SEC-LOG-001`). |

- **Arquivo de manifesto do dataset**: `evals/datasets/ressalvas/dataset.json`
- **Versão e SHA-256 de `dataset.json`**:
  - `v1.0.0` (histórico, com erro de transcrição na substring literal de `EVAL-RES-006`): `dc09921247fa7e1c92eede508437ecc0d065a21bc14b7461912818628e429a27` (execução `ragas-calib-a4390ee523d3` preservada como `Falhou` em `evidencias/T-015/calibracao-real.json`).
  - `v1.0.1` (vigente, corrigindo apenas a substring literal `"não podem expor chaves de API"` da linha 15 de `docs/seguranca_ia_educacional.md` e validada deterministicamente contra a fonte por `load_dataset_spec`): **`8f0d3582ffca643727089879959982f25c87387b56db93fb2a95f1ce339bae8d`**.
- **Referência aos 9 ataques (`SEC-ATQ-001` / `C09`)**: O dataset referencia formalmente os 9 ataques (`A01–A09` / `R3_ATTACKS`) e os 11 controles legítimos (`R3_LEGITIMATE`) avaliados na suíte de ingestão (`packages/ingestion/tests/test_indexing.py` e evidência `governanca/mudancas/2026-11-24-ingestao/evidencias/T-008/juiz-real-ressalvas.json`, `sha256: 46a53e241ceb843e1cb98ff00e3132d429d3ce07b512eae8db40e7b0a5b0046e`, 20/20 `Passou`), sem repetir chamadas redundantes ao juiz de ingestão dentro do runner RAGAS.

---

## 3. Protocolo Executável de Calibração do Juiz e Limiares

### 3.1 Configuração do Juiz e Isolamento da Sandbox (ADR-0006)
- **Variável**: `EVAL_JUDGE_MODEL=openrouter/openai/gpt-6.1-sol`
- **Adaptador RAGAS 0.4.4**: `llm_factory(model="openrouter/openai/gpt-6.1-sol", provider="openrouter", client=instructor.from_litellm(_guarded_acompletion), adapter="litellm", temperature=0.0, top_p=1.0, seed=42)`
- **Validação de domínio**: Scores retornados pelas métricas são validados estritamente como números finitos em `[0.0, 1.0]` (`math.isfinite`, rejeitando `bool`, `NaN`, `±inf` e valores fora de `[0.0, 1.0]` com `EvalProviderError("invalid_output")`, sem clamp silencioso).
- **Isolamento de execução**: Tanto `--calibrate` quanto `--execute` rodam dentro de `_disposable_evaluation_sandbox`, que provisiona um container Qdrant descartável (`qdrant/qdrant:v1.20.2` em porta aleatória de loopback, sem volumes compartilhados) e um banco PostgreSQL descartável (`eval_ressalvas_<uuid>`), destruindo ambos em `finally` sem jamais tocar na coleção compartilhada e sem silenciar erros de limpeza.

### 3.2 Calibração Executável (`run_judge_calibration` / `--calibrate`)
A aprovação humana autoriza a execução, mas **não substitui a prova de calibração executada**:
1. **Controles positivos (`6 casos`)**: Para cada caso `EVAL-RES-001` a `EVAL-RES-006`, o runner verifica deterministicamente a proveniência canônica (`mapping_status == "exact"`, cobertura de 100% dos `expected_evidence_substrings` e cobertura de 100% dos `expected_section_paths` nos spans canônicos intersectados) e avalia os chunks recuperados pelo `SafeRetriever` com `EVAL_JUDGE_MODEL`. Para passar, cada controle positivo deve atingir `context_precision_with_reference >= 0.65` e `context_recall >= 0.75`, com média global `>= 0.80` e `>= 0.85`.
2. **Controles negativos pareados (`6 casos`, sem marcador ou pista no texto enviado ao juiz)**: Para cada caso, o runner seleciona deterministicamente os chunks recuperados de **outro documento distinto do corpus** (`doc_id != case.doc_id`, ordenados por `(doc_id, case_id)`) e envia **exatamente os bytes originais desses chunks** para `EVAL_JUDGE_MODEL`, sem qualquer prefixo, tag ou pista textual que revele tratar-se de controle negativo; os identificadores `control_kind: "negative_displaced_document"`, `source_doc_id`, `displaced_doc_id` e `displaced_case_id` são registrados exclusivamente nos metadados do relatório fora da chamada LLM. Para passar, o juiz deve discriminar o contexto deslocado pelo próprio conteúdo semântico, atribuindo `context_precision_with_reference <= 0.00` e `context_recall <= 0.00` (`max_negative_control_score = 0.00`). Se o juiz for leniente e pontuar contexto irrelevante, a calibração falha fechada (`status: "Falhou"`).
3. **Vínculo obrigatório com a Baseline, Limiares Efetivos e Receita (`CalibrationManifest`)**: A execução de `--execute` exige obrigatoriamente `--calibration-report <caminho>`. O `CalibrationManifest` registra `calibration_protocol_version`, `protocol_thresholds` (todos os limiares efetivos usados na calibração) e `calibration_protocol_sha256` (hash canônico da receita de controles positivos/negativos, pareamento determinístico, `ragas_version`, `metric_prompts_sha256` e `protocol_thresholds`). O runner valida via `validate_calibration_report` e `compare_with_baseline` que a calibração teve `status == "Passou"`, `positive_controls_passed == True`, `negative_controls_passed == True` e que `dataset_sha256`, `ragas_version`, `judge_model`, `judge_config_sha256`, `embedding_key`, `metric_prompts_sha256`, `protocol_thresholds` e `calibration_protocol_sha256` são idênticos aos da execução de baseline/candidato (rejeitando qualquer calibração feita sob limiares mais frouxos, mesmo que compartilhe o mesmo `approval_ref`).

### 3.3 Limiares Mínimos e Gates Obrigatórios (para aprovação humana)

| Métrica / Gate | Limiar Mínimo Proposto (Média do Dataset) | Piso por Caso Individual | Tolerância Máxima de Queda em Diff Futuro |
| --- | --- | --- | --- |
| `context_precision_with_reference` | `0.80` | `0.65` | `-0.02` (queda maior ou valor abaixo do limiar reprova `baseline_diff` e o `status` global da execução com `Falhou` / exit `1`) |
| `context_recall` | `0.85` | `0.75` | `-0.02` (queda maior ou valor abaixo do limiar reprova `baseline_diff` e o `status` global da execução com `Falhou` / exit `1`) |
| Cobertura determinística de proveniência (`deterministic_provenance_hit_rate`: substrings + `expected_section_paths` nos spans + `mapping_status == 'exact'`) | **`1.00` (gate bloqueante: `< 1.00` reprova o relatório com `Falhou` mesmo que o LLM dê score alto)** | `1.00` | `0.00` |
| Controles negativos deslocados na calibração (`max_negative_control_score`) | `0.00` (máximo permitido) | `0.00` | `0.00` |

---

## 4. Comandos Exatos Após Aprovação Humana e Configuracão de `OPENROUTER_API_KEY`

1. **Pré-flight (dry-run determinístico sem chamadas LLM)**:
   ```bash
   uv run executar-avaliacao-ressalvas --preflight \
     --approval-ref "governanca/mudancas/2026-11-24-correcao-integral-ressalvas/evidencias/T-015/aprovacao-calibracao.md" \
     --output governanca/mudancas/2026-11-24-correcao-integral-ressalvas/evidencias/T-015/preflight.json
   ```
2. **Passo 1 — Executar calibração real do juiz (6 controles positivos + 6 controles negativos sem pistas em sandbox isolada)**:
   ```bash
   uv run executar-avaliacao-ressalvas --calibrate \
     --approval-ref "governanca/mudancas/2026-11-24-correcao-integral-ressalvas/evidencias/T-015/aprovacao-calibracao.md" \
     --output governanca/mudancas/2026-11-24-correcao-integral-ressalvas/evidencias/T-015/calibracao-real-v1.0.1.json
   ```
3. **Passo 2 — Estabelecer a primeira baseline oficial vinculada ao manifesto de calibração aprovado**:
   ```bash
   uv run executar-avaliacao-ressalvas --execute \
     --approval-ref "governanca/mudancas/2026-11-24-correcao-integral-ressalvas/evidencias/T-015/aprovacao-calibracao.md" \
     --calibration-report governanca/mudancas/2026-11-24-correcao-integral-ressalvas/evidencias/T-015/calibracao-real-v1.0.1.json \
     --output governanca/mudancas/2026-11-24-correcao-integral-ressalvas/evidencias/T-015/baseline-inicial-v1.0.1.json
   ```
4. **Passo 3 (em mudanças futuras / integração T-017) — Executar avaliação candidata com diff bloqueante contra a baseline**:
   ```bash
   uv run executar-avaliacao-ressalvas --execute \
     --approval-ref "governanca/mudancas/2026-11-24-correcao-integral-ressalvas/evidencias/T-015/aprovacao-calibracao.md" \
     --calibration-report governanca/mudancas/2026-11-24-correcao-integral-ressalvas/evidencias/T-015/calibracao-real-v1.0.1.json \
     --baseline-report governanca/mudancas/2026-11-24-correcao-integral-ressalvas/evidencias/T-015/baseline-inicial-v1.0.1.json \
     --output governanca/mudancas/2026-11-24-correcao-integral-ressalvas/evidencias/T-017/avaliacao-diff.json
   ```

---

## 5. Decisão Solicitada ao Humano

Para desbloquear a execução da calibração real e da primeira baseline RAGAS (`EVAL-INT-001`), solicita-se ao humano:
1. Aprovar o dataset `ressalvas-recuperacao-segura-v1` (`sha256: dc09921247fa7e1c92eede508437ecc0d065a21bc14b7461912818628e429a27`);
2. Aprovar os limiares propostos (`context_precision_with_reference >= 0.80`, `context_recall >= 0.85`, `deterministic_provenance_hit_rate == 1.00`, `max_negative_control_score == 0.00`) e o protocolo executável de calibração acima;
3. Disponibilizar `OPENROUTER_API_KEY` no arquivo `.env` local.

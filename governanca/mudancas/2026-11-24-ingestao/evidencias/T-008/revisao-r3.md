# Revisão independente r3 — T-008 (google)

- **Tarefa**: T-008 (`2026-11-24-ingestao`)
- **Implementador (mantido)**: `openai` (`rag-backend`, dispatch de implementação `ctx_67c3f0679016`)
- **Revisor nominal**: `anthropic`
- **Provedor efetivo da revisão**: `google` (Antigravity / Gemini 4 Argon)
- **Dispatch da revisão**: `ctx_7929f4fbfb43` (task `task_12d662148b35`)
- **Motivo do redespacho**: O primário `anthropic` (Opus 5.5) atingiu limite de 5 horas / sem créditos no dispatch `ctx_0a8d498c16fb`, encerrado pelo coordenador. O fallback autorizado `GPT-6.1 Sol` (`openai`) não pode revisar implementação feita por `openai` ("Quem constrói não revisa"). Conforme a política de `governanca/papeis/README.md` (linhas 19–22), a revisão independente foi redespachada para `google`.
- **Veredito**: **aprovado com ressalvas** (G4 permanece pendente do humano)

---

## 1. Histórico de execução r3 e complemento

- **Histórico r3 preservado (`pytest-r3.txt` e `resultado-r3.md`)**: a execução focal única do executor (`LITELLM_LOG=ERROR uv run pytest packages/ingestion/tests/test_indexing.py packages/llm/tests/test_models.py -k 'not ten_real_requests'`) resultou em **119 passed, 2 failed, 1 deselected** em 45,94 s. As 2 falhas ocorreram exclusivamente nas duas parametrizações (`provider` e `invalid_output`) de `test_ING_INJ_001_r3_error_category_is_sanitized` porque a asserção `'private' not in ''.join(traceback.format_exception(caught.value))` encontrou o literal `'private'` no próprio trecho de código da linha do teste (`judge_injection('private')`) exibido no frame da traceback.
- **Complemento executado pelo principal**: após o executor extrair o literal para variável (`source = 'private'` em `packages/llm/tests/test_models.py:139-141`), o principal executou apenas o complemento `LITELLM_LOG=ERROR uv run pytest packages/llm/tests/test_models.py -k r3_error_category_is_sanitized`, obtendo **2 passed, 15 deselected em 1.97s**.
- **Sem alegação de suíte completa verde em r3**: o histórico da rodada r3 (`119 passed, 2 failed` na execução focal anterior) permanece registrado integralmente, junto com o complemento pontual (`2 passed, 15 deselected`). Nenhuma nova execução de testes ou chamadas reais foi disparada nesta revisão, e o Langfuse não foi religado.

---

## 2. Avaliação técnica das correções r3 (com `arquivo:linha`)

### 2.1. Falsos positivos e contornos da heurística / alta precisão (`ING-INJ-001`, `ING-INJ-002`)
- **Separação entre alta precisão e triagem suspeita**:
  - `packages/ingestion/src/training_ingestion/indexing.py:37-45`: `high_precision_reason` foi restrita a controles e delimitadores inequívocos: tags Unicode (`U+E0000`–`U+E007F`, linha 39), controles bidi (`U+202A`–`U+202E` e `U+2066`–`U+2069`, linha 41) e delimitadores formais de turno/modelo (`_MODEL_DELIMITER` em `indexing.py:23,43`: `<|im_start|>`, `<|im_end|>`, `<start_of_turn>`, `<end_of_turn>`, `[/?inst]`, `<<sys>>`, `<|system|>`, `<|assistant|>`).
  - `packages/ingestion/src/training_ingestion/indexing.py:21-22,48-67`: termos ambíguos de papel (`_ROLE`: `system:`, `assistant:`, `user:`, `human:`, `model:`, `root:`, `### system`, `### instruction`, `[c\d+]`, `<system>`, `<system_error>`, `[system]`) e menções a verificadores (`_CHECKER`: `verificador`, `juiz`, `classifier`, `checker`), além de categoria Unicode `Cf` genérica (como soft hyphen `U+00AD`, linha 52), preenchedores invisíveis (`_FILLERS`, linha 24 e 54) e escrita mista (`LATIN` + `CYRILLIC`/`GREEK`/`ARMENIAN`, linhas 56–60), permanecem apenas em `suspicious_reason`, encaminhando ao juiz sem forçar bloqueio unilateral.
  - `packages/llm/src/training_llm/__init__.py:117-126`: o prompt do juiz instrui explicitamente a diferenciar discussão acadêmica/jurídica/código (`juiz`, `classifier`, `type checker`, rótulos YAML, `<system_error>`, soft hyphen) de instruções ativas ao modelo ou ao verificador.
  - **Evidência nos testes**: `packages/ingestion/tests/test_indexing.py:329-368` (`R3_LEGITIMATE` e `test_ING_INJ_001_r3_legitimate_can_be_released_to_consumer`) comprova que os 11 casos legítimos (jurídicos, ML/CS, YAML `user: root`, `#include <system_error>`, soft hyphen) têm `high_precision_reason(value) is None`, acionam `suspicious_reason(value)` e, quando liberados pelo juiz, são indexados no Qdrant com `injection_flagged=False` e `injection_reason=None`. Além disso, `test_ING_INJ_001_r3_real_judge_legal_and_academic_negative` (`test_indexing.py:414-426`) passou contra o juiz real na execução focal r3.
- **Cobertura de contornos r2**:
  - `packages/ingestion/src/training_ingestion/indexing.py:12-19,27-35`: `_normalize` aplica NFKD + `casefold`, remove `Mn`/`Cf` e `_FILLERS`, traduz dotless `ı` (`U+0131`) e `օ` armênio (`U+0585`) para `i`/`o` (linha 31) e colapsa soletração espaçada de 3+ letras isoladas (`i g n o r e`, linhas 33–34) sem alterar o texto original enviado ao juiz e ao consumidor (`indexing.py:75,79`). `_PATTERNS` cobre negação de seguir instruções (`nao siga`, `do not follow`, `don't follow`), `nova tarefa`/`new task`, `novas instruc*`, `repita/repeat ... texto acima/anterior` e `obedeca/obey ... usuario/user`.
  - **Evidência nos testes**: `packages/ingestion/tests/test_indexing.py:336-342,370-388` (`R3_ATTACKS` e `test_ING_INJ_001_r3_contours_reach_judge_and_exclude_consumer`) valida os 9 contornos até a exclusão no Qdrant com juiz dublê restrito ao teste.
  - **Ressalva**: conforme honestamente registrado em `resultado-r3.md` (linha 31), os 9 novos contornos de `R3_ATTACKS` foram provados na triagem determinística e no fluxo com juiz dublê, mas não foram individualmente submetidos à API real do juiz.

### 2.2. Motivo persistido de bloqueio (`ING-INJ-001`)
- `packages/ingestion/src/training_ingestion/indexing.py:80-87`: `InjectionChecker.check` calcula `precise = high_precision_reason(value)` e define `reason = precise`; somente se `reason is None and verdict.injection` define `reason = f'juiz; motivo não confiável: {verdict.reason}'`.
- Assim:
  1. Se o juiz liberar (`verdict.injection == False`) um chunk sem sinal de alta precisão, `reason` é `None` e `injection_flagged` é `False` (`test_indexing.py:363`).
  2. Se um sinal de alta precisão (`high_precision_reason`) estiver presente e o juiz disser "seguro", o motivo persistido é o código da regra de alta precisão (`heuristica-injecao-v3:...`), nunca o texto "seguro" do juiz (`test_indexing.py:390-402`).
  3. Se o bloqueio vier da confirmação do juiz, o motivo persistido traz o prefixo explícito `juiz; motivo não confiável: ...` (`test_indexing.py:405-411`).

### 2.3. Limpeza restrita no Qdrant (`ING-IDX-001`)
- `packages/ingestion/src/training_ingestion/indexing.py:145-148`: `QdrantIndex.delete_version(version_id)` remove pontos exclusivamente via `FilterSelector` com `FieldCondition(key='document_version_id', match=MatchValue(value=version_id))`.
- `packages/ingestion/tests/test_indexing.py:183-210`: em `test_ING_INJ_001_ING_EMB_001_ING_IDX_001_real_end_to_end`, o bloco `finally` (linhas 195–205) instancia cliente de limpeza, chama `delete_version(result.version.document_version_id)` e faz `assert cleanup_client.count(settings.embedding_key, count_filter=own_filter).count == 0` antes de descartar o banco Postgres temporário.
- `packages/ingestion/tests/test_indexing.py:429-436`: `test_ING_IDX_001_r3_cleanup_preserves_other_versions` prova em coleção isolada que `delete_version` remove apenas a versão alvo e preserva pontos de outra `document_version_id`.
- **Ressalva operacional**: a limpeza por `result.version.document_version_id` não apaga pontos órfãos deixados por execuções anteriores à correção na coleção compartilhada local, nem cobre uma falha hipotética entre o `upsert` no Qdrant e o retorno de `ingest_document` caso a exceção ocorra fora de `_ingest_transaction` (dentro de `ingest`, `core.py:231-234` já aciona `self.processor.rollback(pending[0])`, que chama `delete_version`).

### 2.4. Erro sanitizado e causa da instabilidade r2 (`ING-INJ-001`)
- `packages/llm/src/training_llm/__init__.py:69-74,133-141`: criada `InjectionJudgeError(ValueError)` com atributo `self.kind` (`'provider'` na falha da chamada LiteLLM nas linhas 133–134; `'invalid_output'` na falha de parse/validação Pydantic ou `reason` em branco nas linhas 136–141), mensagem estática `'Falha na verificação de injeção'` e `raise ... from None`.
- `packages/llm/tests/test_models.py:128-146`: `test_ING_INJ_001_r3_error_category_is_sanitized` verifica `kind`, mensagem fixa, ausência do conteúdo privado na traceback formatada, ausência de `input_value` no `repr` e `__cause__ is None` (validado pelo complemento `2 passed`).
- **Causa da instabilidade r2**: corretamente mantida como **Bloqueado** / não comprovada (`investigacao-instabilidade-r3.md:1-5` e `resultado-r3.md:37`). O arquivo `pytest-r2-run3-incompleto.txt` não possui traceback nem resumo; não se inventou diagnóstico de rede/429/5xx e não se adicionou retry oculto.
- **Ressalva de logging**: `litellm.suppress_debug_info = True` (`training_llm/__init__.py:111`) não impede vazamento se `LITELLM_LOG=DEBUG` for ativado no ambiente; a restrição foi documentada em `packages/ingestion/README.md:137-139`.

### 2.5. Limites de `section_path`, palavras longas e imutabilidade (`T-006`, `T-007`, `T-008`)
- **Limites de `section_path` e fragmentação (`packages/ingestion/src/training_ingestion/core.py:142-147,176-178` e `README.md:145-148`)**:
  - `core.py:176`: `Chunker.split` emite o chunk pendente em toda mudança de `unit.section_path` (`pending[0].section_path != unit.section_path`), o que garante que nunca atravessa seção de nível 1 (`ING-CHK-001`), mas pode gerar chunks menores que `target_tokens` ao trocar de subseção (`H2`/`H3`).
  - `core.py:146-147`: sequências sem fronteira de espaço/pontuação que excedam `limit_bytes` (como URLs/base64 muito longos ou texto CJK contínuo acima de metade do teto) levantam `IngestionError("palavra_excede_limite_de_fragmento")`.
  - Ambos os comportamentos estão documentados em `packages/ingestion/README.md:145-148` como limitações pendentes de decisão humana no G4, sem alterar requisitos nem fingir aceite.
- **Imutabilidade e versões anteriores (`packages/lineage/src/training_lineage/migrations/versions/0001_ingestion.py:47-61`, `0002_document_immutable.py:9-20` e `README.md:149-150`)**:
  - As tabelas `chunk` e `document_version` têm triggers `BEFORE UPDATE OR DELETE` e `BEFORE TRUNCATE` que recusam mutação com `ERRCODE = '23514'`.
  - Como consequência, versões e chunks gravados antes da correção r3 da heurística são imutáveis no banco e (`owner_id`, `sha256`) reaproveita a versão existente (`core.py:242-247`); `README.md:149-150` e `resultado-r3.md:39` registram explicitamente que não houve reclassificação retroativa e que eventual reprocessamento exigirá decisão humana.

---

## 3. Conclusão e Ressalvas para o Gate G4 (humano)

As causas de reprovação da T-008 na rodada 2 (falsos positivos forçados por `high_precision_reason` sobre termos jurídicos/CS/papéis, contornos de normalização/vocabulário na triagem, `injection_reason` incoerente quando o juiz liberava, e ausência de limpeza do `document_version_id` no `finally` do teste real ponta a ponta) foram devidamente corrigidas no código e verificadas pelos testes da rodada 3 + complemento pontual de `test_ING_INJ_001_r3_error_category_is_sanitized`.

**Ressalvas registradas para deliberação do humano no G4**:
1. **Evidência de execução r3 por escopo**: na r3 foi executada a suíte focal (`119 passed, 2 failed, 1 deselected`) seguida do complemento pontual dos 2 testes corrigidos (`2 passed, 15 deselected`), sem reexecutar a suíte completa de 136 testes (que inclui `test_ING_EMB_001_ten_real_requests` e os testes de `lineage`/`test_ingestion.py` já aprovados em r2 sem alteração de código).
2. **Contornos novos (`R3_ATTACKS`) e API real**: os 9 contornos adicionados em r3 passam pela heurística determinística e pelo pipeline com juiz dublê + Qdrant real (`test_indexing.py:370-388`), mas não foram todos submetidos individualmente ao juiz LLM real.
3. **Causa da falha instável em r2 (`run3`)**: permanece **Bloqueado** por ausência de traceback na captura original (`pytest-r2-run3-incompleto.txt`); `InjectionJudgeError.kind` agora instrumenta falhas futuras sem expor dados.
4. **Limitações documentadas (T-006/T-007/T-008)**: corte em toda mudança de `section_path`, recusa de token contíguo sem espaço acima do limite de fragmento (`palavra_excede_limite_de_fragmento`), proibição operacional de `LITELLM_LOG=DEBUG` em produção, órfãos anteriores no Qdrant local e imutabilidade de versões já gravadas.

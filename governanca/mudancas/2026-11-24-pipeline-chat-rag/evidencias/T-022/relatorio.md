# T-022 — avaliação da geração real e calibração inicial do juiz de saída

Executor efetivo: Anthropic/Opus 5.5 (dispatch `ctx_4478531ceab3`), papel `avaliacao-observabilidade`. Foi um fallback porque a cota do Google acabou. Revisor: OpenAI, pendente. Nenhuma chamada a modelo foi feita nesta etapa.

## Entregue

| Arquivo | Conteúdo |
| --- | --- |
| `evals/datasets/geracao/dataset.json` | `geracao-juiz-faithfulness-v1` 1.0.0: 3 documentos sintéticos e educacionais, sem dados pessoais; 15 casos do juiz (6 aprovam, entre eles os controles acadêmicos de R3 e R4; 9 reprovam, cobrindo R1–R4); 3 casos de chat para execuções futuras, sem resposta de referência |
| `evals/datasets/geracao/docs/*.md` | Fontes dos contextos; cada contexto e cada evidência existem literalmente na fonte |
| `evals/runner_geracao.py` | Captura verificada, Faithfulness, calibração do juiz, comparação compatível e CLI |
| `evals/tests/test_geracao_runner.py` | 46 testes offline; dublês só no arquivo de teste (leitor da linhagem, métrica RAGAS, serviço do juiz) |
| `evals/pyproject.toml` | Dependência de workspace `training-generation` e script `executar-avaliacao-geracao` |

Sem mudanças em: `runner_ressalvas.py`, dataset `ressalvas` 1.0.1, baseline histórica, produto, prompts, rubrica, modelos, limiares e `uv.lock`.

**Manifesto pronto para o coordenador resolver o lock.** O único acréscimo é `training-generation` (workspace) em `evals/pyproject.toml`; não há pacote externo novo. Até o lock ser resolvido, o script não fica instalado; usar `uv run --no-sync python -m evals.runner_geracao …`.

## Contrato do artefato `training-evals/chat-generation-artifact/v1`

Montado por `capturar` a partir das saídas reais da T-021 (contrato confirmado pelo dispatch `ctx_c55621c67ed1`):

- `response.json`: stdout verbatim, validado como `ChatResponse`;
- `diagnostics.json`: `chat_turn_id`, `trace_id`, `durations_seconds`, `models`, `prompt_versions`, `status`, `errors`, `model_calls_admitted`, `embedding_key`, `reranker{model,revision,cutoff,manifest_sha256}`;
- `e8-capture.json` (0600): para cada chamada do E8, o `NumberedContext` exato passado a `generate()`, com `call_id`, `call` (inicial|reparo), `context_ref_mapping` local→global e `status`; os itens originais entregues, com `call_id`, `generation_item_index` e `attempt`.

O que a captura confere:

- `chat_turn_id`, `trace_id` e status são os mesmos nos três arquivos;
- itens originais e entregues têm os mesmos IDs, na mesma ordem;
- `render_item(content) == DeliveredItem.conteudo`;
- cada item resolve suas citações **só no contexto da própria chamada concluída**, nunca na união das chamadas, via mapeamento e `fontes`, e cada evidência aparece normalizada nesse contexto;
- o `attempt` é coerente com o tipo da chamada.

O artefato só é gerado depois de uma verificação:

- `lineage_postgres` (leitura): a versão pertence ao dono; o conteúdo original é igual a `ItemRecord.content`; os `chunk_id`s das citações coincidem; cada parte do texto do contexto E8 é substring de `chunk.text`;
- `sandbox_manifest_hashes`: os sha256 dos três arquivos batem com `manifest.files`; owner, session, turn, trace e versões batem com o manifesto.

## Avaliação (ainda não executada)

- **Faithfulness** (ADR-0006):
  - `user_input` = frente ou enunciado;
  - `response` = verso, ou alternativa correta + `\n` + explicação, sobre o conteúdo original pré-escape;
  - `retrieved_contexts` = trechos citados do contexto E8 da própria chamada; chunks enviados e não citados não entram.
  - Antes de qualquer chamada, bloqueia nestes casos: prompt diferente da versão atual do manifesto; juiz E10 ausente ou Gemini; embedding diferente de `gemini-embedding-2@3072@fmt-v1`; orçamento abaixo de 2 chamadas por item.
  - Uma tentativa por item (`num_retries=0`) e para no primeiro erro retryable, marcando os demais como `Não executado`.
  - Score fora de [0,1], NaN ou booleano vira `invalid_output`, sem clamp.
  - O relatório traz latências, modelos, reranker, hashes, `faithfulness_prompt_sha256` e config do juiz sem chave, com `thresholds: null` e `historical_comparison: Não se aplica`.
- **Calibração do juiz E10**:
  - Exige que `output_judge` e `output_rubric` atuais sejam iguais ao vínculo do dataset (`chat-output-judge-v1@b5073d15…`, `chat-output-rubric-v1@9108eff8…`) e que o juiz não seja Gemini.
  - Faz uma chamada real de `GenerationService.judge` por caso, com 1 item por lote. É diferente da produção, que julga o lote do turno, e isso fica declarado no relatório.
  - Mede:
    - acurácia de `aprovado`;
    - falsa aprovação e falsa rejeição;
    - acurácia de `fundamentado` e `conforme_rubrica` onde há rótulo;
    - taxa de critério correto no motivo.
  - Sempre registra `human_calibration: false` e `production_ready: false`.
- **Comparação antes/depois**: só quando juiz, prompts do RAGAS, prompts do produto, modelos, embedding, documentos e conjunto de `case_id` são idênticos. A única diferença aceita é o reranker. Caso contrário, bloqueia.

## Provas

1. Focal local única, offline: `uv run --no-sync pytest evals/tests/test_geracao_runner.py -q -p no:cacheprovider` → **46 passaram** (`pytest-focal.txt`).
2. Captura real, sem chamada de modelo, do run `t021-real-c513f0da772748a3970dfdf2959e25ad`:
   - `APP_POSTGRES_DB=test_t021_real_c513f0da772748a3970dfdf2959e25ad uv run --no-sync python -m evals.runner_geracao capturar … --lineage-env-file .env` → exit 0, `artefato-lineage.json`: 1 item, 1 chunk e 1 versão conferidos; documento `e0a353ae…`;
   - `… capturar … --manifest …/real-cli/manifest.json` → exit 0, `artefato-manifesto.json`, com o mesmo documento.
   - Uma entrada Faithfulness derivada: flashcard, 1 contexto, chamada inicial, attempt 1.
3. A T-021 foi avisada de que o sandbox PG/Qdrant não é mais necessário para a T-022.
4. `uv run validar-governanca` → `governança OK: 6 mudança(s), 22 tarefa(s), 28 requisito(s)` (`validador.txt`).
5. Sandbox da T-021 removido por ela após a coleta (`real-cli/cleanup.json`); o manifesto original continua intacto.

**Desvio declarado (lock):** o `uv run validar-governanca` fez o uv re-travar o workspace sozinho, às 00:31. Isso gravou no `uv.lock` a aresta de workspace `training-evals → training-generation` (editable) e reconstruiu o pacote `training-evals`. Não entrou nenhum pacote externo novo por esta tarefa. Não revertido: o lock já tinha outras alterações não commitadas, e reverter só esse trecho exigiria editar o lock à mão. O coordenador, dono do lock, deve conferir e aceitar ou refazer.

Hashes sha256:

    3c6e242403e0feb619fa3f51a76f03a597a92a3aed56759755d61d803b87510f  T-021/real-cli/e8-capture.json
    83af532a2afd1e3d2c7aaa2763b3343b62047a12c3df4c5704074e58ed470018  T-021/real-cli/response.json
    13562e41a4ddd27ec31b99755114b1302b26144b87cad13b161afc7588803b71  T-021/real-cli/diagnostics.json
    4d3724b1af62458d83b0c66c3e69d0b701d06bea33e229df2802514285c6e452  T-021/real-cli/manifest.json

## Comandos para depois da revisão OpenAI e da coordenação do principal

    uv run --no-sync python -m evals.runner_geracao faithfulness --artifact governanca/mudancas/2026-11-24-pipeline-chat-rag/evidencias/T-022/artefato-lineage.json --max-calls 2 --executar-chamadas-reais --out <relatorio-faithfulness.json>
    uv run --no-sync python -m evals.runner_geracao calibrar-juiz --max-calls 15 --executar-chamadas-reais --out <relatorio-juiz.json>

Sem `--executar-chamadas-reais`, os dois comandos saem com código 2 sem chamar nada.

## Limites declarados

- Um único item real (flashcard, sem reparo): o Faithfulness sobre ele é uma medição de exemplo, não uma estimativa de qualidade da geração.
- RAG-JUI-003 continua `Não executado`: o artefato real não teve reparo. O runner liga itens de reparo à própria chamada (há testes), mas isso não prova o comportamento do produto.
- Os 15 rótulos são técnicos, não humanos. A calibração robusta (50–100 casos rotulados por humano) e a prontidão para produção continuam não comprovadas.
- Nenhum limiar foi proposto. Se houver um, ele deve ser documentado antes dos resultados.
- O pacote `evals` é plano (`packages = ["evals"]`); por isso os módulos novos ficam na raiz do pacote.

## Correção da revisão OpenAI F1–F6 (dispatch `ctx_1b07747a04e9`, 2026-11-25)

O histórico acima fica preservado: a primeira entrega teve **46 passaram** (`pytest-focal.txt`) e foi **reprovada** pela revisão OpenAI (`revisao-openai.md`, `ctx_94f8e269a9f2`), com F1–F6. Esta correção só toca em `evals/runner_geracao.py` e `evals/tests/test_geracao_runner.py`. Dataset, documentos, `pyproject.toml`, `uv.lock`, produto, prompts, rubrica, modelos e limiares não mudaram. O `runner_ressalvas.py` continua com sha256 `4ebf5e8c…`, igual ao `manifesto-integracao.json` da T-017; a baseline `ragas-run-b647f4ac987c` não foi tocada. Nenhuma chamada real a RAGAS ou ao juiz foi feita.

| Achado | Correção | Prova offline |
| --- | --- | --- |
| F1 — orçamento × retentativas do Instructor | O cliente Instructor da T-022 agora é próprio, sem reuso do helper histórico. `llm_factory(..., max_retries=0)` chega a `chat.completions.create`, e o transporte força `num_retries=0`, `max_retries=0`, `no-log` e `timeout=60`. `CallBudget` admite cada chamada **na fronteira do transporte** e recusa a excedente (`EvalBudgetExceeded`). `classify_eval_exception` percorre a cadeia (`InstructorRetryException` → erro HTTP) e preserva status e retryable. | Cadeia real RAGAS → Instructor sobre `TransporteDuble`: sucesso com 2 chamadas por item; payload inválido na 1ª etapa com 1 chamada e na 2ª com 2, ambos `invalid_output`; 429/503 embrulhados com 1 chamada, `Bloqueado` com status e restantes `Não executado`; 400 sem parada; orçamento fechado com 1 vaga (1 chamada, categoria `budget`). **Controle de sensibilidade**: a mesma cadeia sem a correção faz 4 tentativas. |
| F2 — log do Instructor | `redacted_sdk_logging()` troca a fábrica de `LogRecord` durante avaliação, calibração e CLI. Em namespaces de SDK (`instructor`, `litellm`, `ragas`, `httpx`, `openai`, ADK/genai), mensagem, args, `exc_info` e stack são redigidos **na criação**, antes de handlers, filtros e `lastResort`. A fábrica anterior é restaurada. LiteLLM com `suppress_debug_info`. | Marcador sintético em payload inválido e em erro HTTP: ausente do relatório, do handler da raiz, do filho tardio com nível DEBUG e handler próprio, de stdout e de stderr; sem traceback. Teste de `lastResort` com a raiz sem handlers. **Controles**: sem a redação, o marcador aparece no log e no `lastResort`. |
| F3 — loader sem reverificação | `load_verified_artifact` exige o sha256 do manifesto **fixado fora do artefato**, remonta o artefato das fontes (bytes, hashes, identidade, vínculos de chamada) e compara campo a campo com o artefato armazenado. `run_faithfulness_evaluation` só aceita `VerifiedArtifact`, que só esse loader cria (token privado). O CLI reverifica antes de ler credenciais. | Rejeitados antes da rede: owner/session forjados, conteúdo trocado coerentemente com o render, `response_sha256` obsoleto, fonte alterada após o manifesto, manifesto diferente do fixado, artefato cru (0 chamadas) e construção direta de `VerifiedArtifact`. O `verification.method` declarado não serve como prova. |
| F4 — identidade opcional | `SandboxManifest` exige `run_id`, `owner_id`, `session_id`, `chat_turn_id`, `trace_id`, `case_id` e `document_version_ids`, além de `documents` **ou** `source_sha256`. Os três arquivos ficam em chaves exatas, sem alias, duplicata (casefold), caminho absoluto ou `..`; o caminho usado tem de ser o declarado. | Ausência de cada um dos 7 campos rejeitada; identidade divergente (4 campos) rejeitada; alias `./`, duplicata por caixa, `../` e arquivo faltante rejeitados. |
| F5 — comparação sem vínculo | `case_protocol` entra no artefato e no relatório. Para dataset: id, versão, sha256, `case_spec_sha256` e `request_sha256`, e o pedido do manifesto (`message_sha256`, `count`) tem de bater com o caso. Para caso externo: `request_sha256` do manifesto. A comparação exige protocolo igual por `case_id`. A calibração registra `dataset_sha256` e `calls_made`. | Bloqueiam a comparação: `count` diferente, mensagem diferente, pedido ausente, sha256 de dataset diferente e relatório sem `case_id`. Dataset com pedido divergente ou ausente é recusado na captura. |
| F6 — traceback no CLI | `main` responde só com categorias ou mensagens próprias para `ValidationError`, `OSError`/`ValueError` e qualquer outra exceção, sem repr, `input_value` ou traceback. As mensagens deixaram de interpolar `item_id` vindo do artefato. | CLI com marcador em campo extra do artefato, JSON inválido em `comparar` e payload inválido em `capturar`: código 1, sem marcador, sem `input_value` e sem `Traceback`; nenhum arquivo de saída criado. |

O mapeamento Faithfulness (ADR-0006, conteúdo original pré-escape, só trechos citados da própria chamada E8) não mudou; os testes de reparo e de "sem união" continuam.

### Execuções desta correção

1. Prova estreita R1, offline: `uv run --no-sync pytest evals/tests/test_geracao_runner.py -q -p no:cacheprovider` → **85 passaram, 1 falhou** (`pytest-correcao-f1-f6-r1.txt`). A falha era defeito do próprio controle de sensibilidade: ele passava a instância do dublê, cujo `__call__` assíncrono o Instructor não reconhece como coroutine, e por isso montou cliente síncrono sem chamar o transporte. Corrigido só no teste (`transporte.__call__`), sem afrouxar asserts.
2. Prova estreita R2, mesma linha → **86 passaram**, 4 warnings de bibliotecas (`pytest-correcao-f1-f6-r2.txt`).
3. Reverificação do artefato real sem rede (`reverificacao-loader.txt`). Com o manifesto real fixado em `4d3724b1…`, os dois artefatos armazenados (`d5e0b1f0…`, `015aa335…`) são idênticos ao remontado das fontes: 1 flashcard, chamada inicial, attempt 1, 1 contexto citado. `case_protocol=None`, porque o manifesto da T-021 não registra o pedido executado: **comparação antes/depois com esse run fica bloqueada**, o que é o comportamento correto.
4. Validador: ver `validador-correcao-f1-f6.txt`.

Hashes sha256 após a correção:

    0f7ff48994c288e212195a8ed8a2d6f9ac69ecba21912a3e37f19e3bd6bba5ae  evals/runner_geracao.py
    67a2ea991d98fbca20876022b89c569a8712715049d19a79d22b9fe0f24c5a5d  evals/tests/test_geracao_runner.py
    e3ac2a8f72147b43b393e3831406dcb760688f825a26b26eb4f9698f41609384  evals/datasets/geracao/dataset.json (inalterado)
    b2a18039fc31864e39021651ca7ec725007120d7cbf697ce3bea5461888a0c73  evals/pyproject.toml (inalterado)
    4ebf5e8c7413d6b08901890c9a838acf536d3d2d18b6add1531f8a2b64acc1cd  evals/runner_ressalvas.py (igual à T-017)
    cebc01406bdc9349593e0e957bce79f9f00e5e5f53ca32c09651e9f70b0a44e0  evidencias/T-022/pytest-correcao-f1-f6-r1.txt
    b7724488f6c4bb9d5d51d2675cd7c35894390e3a61f3149017b485a318a0368c  evidencias/T-022/pytest-correcao-f1-f6-r2.txt
    0c141ffb4c7d335f692d9217d4fad148a11d817f5c79d637647ad1dc09342853  evidencias/T-022/reverificacao-loader.txt

### Comandos atualizados (só depois da reavaliação OpenAI aprovar e da coordenação do principal)

    uv run --no-sync python -m evals.runner_geracao faithfulness --manifest governanca/mudancas/2026-11-24-pipeline-chat-rag/evidencias/T-021/real-cli/manifest.json --manifest-sha256 4d3724b1af62458d83b0c66c3e69d0b701d06bea33e229df2802514285c6e452 --artifact governanca/mudancas/2026-11-24-pipeline-chat-rag/evidencias/T-022/artefato-lineage.json --source-dispatch ctx_c55621c67ed1 --max-calls 2 --executar-chamadas-reais --out <relatorio-faithfulness.json>
    uv run --no-sync python -m evals.runner_geracao calibrar-juiz --max-calls 15 --executar-chamadas-reais --out <relatorio-juiz.json>

### Limites e pontos para o coordenador

- O `runner_ressalvas.py` histórico tem a mesma fragilidade de F1/F2 (`_build_real_litellm_instructor_client` sem `max_retries=0` e sem redação do log do Instructor). Não foi editado, por ser histórico e vinculado à baseline; corrigi-lo exige decisão do coordenador.
- Na calibração do juiz, a contagem é por chamada de `GenerationService.judge`. O produto chama `litellm.acompletion` com `num_retries=0` e sem Instructor, mas a T-022 não instrumenta o transporte interno do produto, que pertence à T-019/T-021.
- A redação de log atua por namespace de SDK. Código próprio que registre o conteúdo do provedor fora desses namespaces não é coberto; o runner não faz isso.
- Faithfulness, calibração do juiz e RAG-JUI-003 continuam `Não executado`. Nenhum score real, limiar ou calibração humana foi produzido ou inferido.

## Correção pós-reavaliação R1–R3 e entradas hostis (dispatch `ctx_b44f356be20d`, 2026-11-25)

Origem: `reavaliacao-openai-f1-f6.md`. Ela rodou em Anthropic, o mesmo provedor do autor, então é parecer técnico **não independente**. Somou-se a mensagem `msg_c18f69269925` sobre `case_id` hostil em mensagens de erro. A revisão independente OpenAI continua pendente antes de qualquer API. Só mudaram `evals/runner_geracao.py` e `evals/tests/test_geracao_runner.py`. Produto (`agents.py` não editado), baseline, modelos, limiares, dataset e lock ficaram intactos.

- **R1** (a correção do produto foi repassada à T-021): o relatório da calibração agora diz exatamente o que a T-022 controla. `protocol.retries` virou `{eval_runner: 0, litellm_num_retries_produto: 0, openai_sdk_max_retries: "padrão do produto, não controlado pela T-022 (até 2)"}`, e `calls_made_scope` deixa claro que a contagem é de chamadas a `GenerationService.judge`, não de requisições HTTP.
- **R2**: `_SDK_LOG_NAMESPACES` passou a incluir `LiteLLM Router`, `LiteLLM Proxy` e `google.auth`, alinhado a `training_llm._SDK_LOGGERS`.
- **R3**: `compare_faithfulness_reports` valida cada relatório contra um contrato mínimo:
  - `schema` = `training-evals/faithfulness-report/v1`;
  - `config`, `artifacts` e `items` com as chaves usadas;
  - item `scored` com score em [0,1];
  - itens ligados a turnos presentes, sem turno repetido.
  - Fora do contrato, o erro é de entrada (`relatório fora do contrato do Faithfulness: N erro(s)`, código 1), não `categoria: interna`.
- **Entradas hostis**: as mensagens de erro não interpolam mais valores de arquivos não confiáveis.
  - `case_id` do manifesto ou do artefato no vínculo ao dataset e na comparação: mensagens fixas.
  - `case_id`/`doc_id` do dataset: só a posição, como `chat_cases[i]`.
  - Nome da etapa de latência do diagnóstico: mensagem fixa.
  - Continuam interpolados só constantes internas (papel, nome de campo), `chunk_ref` validado por padrão `C\d+`, posições e contadores.

### Execuções (só a seleção estreita, sem repetir os 86 e sem APIs)

Comando: `uv run --no-sync pytest evals/tests/test_geracao_runner.py -q -p no:cacheprovider -k "<seleção>"`.

1. Rodada 1 → **5 falharam, 18 passaram** (`pytest-reavaliacao-r1-r3-rodada1.txt`). Defeito do teste novo de R2: punha nível DEBUG em loggers de SDK, e a guarda compartilhada fechou com `unsafe_logging`, como deve.
2. Rodada 2 → **5 falharam, 18 passaram** (`…-rodada2.txt`). Sem o DEBUG, o handler do teste não recebia nada, nem a versão redigida: os filtros da guarda compartilhada já descartam esses registros, o que mascarava a prova.
3. Rodada 3 → **24 passaram** (`…-rodada3.txt`). O teste de R2 passou a verificar diretamente a fábrica de `LogRecord` ativa, com controle fora da redação. Nenhuma asserção foi afrouxada.

Casos novos:
- R2: 5 namespaces.
- R3: 6 relatórios JSON válidos fora do contrato, por biblioteca e por CLI, todos com marcador ausente de stdout/stderr e sem traceback, `input_value` ou `interna`.
- `case_id` hostil:
  - num manifesto fixado válido, com o dataset sem o caso: CLI com código ≠ 0 antes da rede, sem arquivo de saída;
  - em pedido divergente e em documento divergente.
- Dataset hostil no `validar-dataset`.
- Qualificação das retentativas de R1.

Hashes sha256:

    03f72ae912a240900e6d0d0f653fe05c49b2b28d61739a688841e1f8f434b6a0  evals/runner_geracao.py
    1b8f97eed609781a2e2765128d48f1b93e8fe41196bbc37c0d08110e6636e5cb  evals/tests/test_geracao_runner.py
    4ebf5e8c7413d6b08901890c9a838acf536d3d2d18b6add1531f8a2b64acc1cd  evals/runner_ressalvas.py (igual à T-017)
    e3ac2a8f72147b43b393e3831406dcb760688f825a26b26eb4f9698f41609384  evals/datasets/geracao/dataset.json (inalterado)
    3bbe4252121622db8aad87ede34cb7e4110d18608562b5eb4b0d04471d0adbf7  evidencias/T-022/pytest-reavaliacao-r1-r3-rodada1.txt
    7c12d40a0433a88745d69079c3b2f9f4acd62c84495c38412abdc5ee3f6157b7  evidencias/T-022/pytest-reavaliacao-r1-r3-rodada2.txt
    7bd38eeb384a7f9c57cede481745c59d637180858831c4aa979bde709d2bf63d  evidencias/T-022/pytest-reavaliacao-r1-r3-rodada3.txt

O sha256 de `packages/generation/src/training_generation/agents.py` é hoje `283d4378…`, diferente do registrado na T-019. Essa mudança não veio desta tarefa, que não editou o arquivo; é da slice de produto.

Status: a suíte completa não foi reexecutada nesta etapa, só a seleção acima. Faithfulness, calibração do juiz, RAG-JUI-003 e comparação real continuam `Não executado`. A revisão independente OpenAI continua `Bloqueado` até haver cota ou decisão humana. Nada aqui é autoaprovação.

## Score estrito no `comparar` e política de retentativa observada (dispatch `ctx_796ffccd8a6a`, 2026-11-25)

Origem: leitura OpenAI das mudanças R1–R3. Só mudaram `evals/runner_geracao.py` e `evals/tests/test_geracao_runner.py`; `agents.py` não foi editado. Nenhuma API foi chamada.

- **Score estrito:** as views do relatório agora usam `strict=True`.
  - Item `scored` passa pelo mesmo helper das métricas (`_validate_metric_score_value`): só int/float finito em [0,1]. Bool, string, NaN, `None` e valor fora do intervalo são rejeitados, e um item não avaliado não pode trazer score.
  - `_validate_report` devolve uma **cópia validada**, com o score normalizado para float; o dict bruto deixou de ser usado no cálculo e a entrada do chamador não é alterada.
- **Retentativas da calibração:**
  - Saiu o texto fixo "padrão do produto… até 2". A calibração agora observa a fronteira `litellm.acompletion` usada pelo produto, sem substituí-la: registra a política pedida em cada chamada, admite cada requisição no orçamento e **recusa antes do transporte** qualquer chamada sem `num_retries=0` e `max_retries=0` explícitos (`retry_policy`, para os casos seguintes).
  - O relatório traz `observed_transport_policies`, `http_calls_admitted` e `transport_calls_observed`.
  - Julgamento que não passa pela fronteira observada fica `Bloqueado` (`transporte_nao_observado`): sem essa passagem não há prova de política nem de contagem.
  - O atributo original é restaurado ao sair.
  - No `agents.py` atual (sha256 `283d4378…`, correção da T-021), `_completion` já passa `num_retries=0` e `max_retries=0`, e o teste com o `GenerationService` real observa exatamente essa política. O relatório registra o que for observado na execução real, não uma afirmação sobre o código.
- **Testes:**
  - Fixture autouse dubla `litellm.acompletion` em todo o módulo, garantindo que nenhum teste chega ao provedor.
  - O dublê de serviço passa pela fronteira como o produto.
  - `comparar` rejeita score string, `true`, `false`, `"NaN"`, 1.5 e `null` por biblioteca e por CLI (código 1, sem traceback nem `interna`) e aceita score inteiro do contrato.
  - Calibração com o `GenerationService` real e transporte dublado: 15 julgados, 15 requisições admitidas, política `{max_retries: 0, num_retries: 0}`, sem "até 2".
  - Políticas sem zero explícito são recusadas com 0 requisições.
  - Julgamento fora da fronteira fecha.
  - O transporte original é restaurado.
  - O teste de R1 da rodada anterior, que afirmava o texto fixo, passou a exigir a política observada. A asserção foi trocada por mudança de contrato pedida, não afrouxada.

Execução: seleção estreita `-k "calibracao or comparar or comparacao or r3_ or score"` → **37 passaram**, 1 warning de biblioteca (`pytest-score-estrito-transporte-rodada1.txt`). A suíte completa não foi reexecutada.

Hashes sha256:

    0b4d09209337f29f2578d94918ef50a8ffab540671f395e1f14498ed0158c670  evals/runner_geracao.py
    958cf30a545a2346bc00ffe8f5ee624d40f4b5a672b70f56a2252e21ebd7c066  evals/tests/test_geracao_runner.py
    4ebf5e8c7413d6b08901890c9a838acf536d3d2d18b6add1531f8a2b64acc1cd  evals/runner_ressalvas.py (igual à T-017)
    e3ac2a8f72147b43b393e3831406dcb760688f825a26b26eb4f9698f41609384  evals/datasets/geracao/dataset.json (inalterado)
    283d437800d6bfa547a50b64984cf8ea367ad94922f748b0482323d9e8dab302  packages/generation/src/training_generation/agents.py (T-021; não editado aqui)
    b3542f2708eba7de654773c3edb89d18e648fd4edc7dece75b969eec5a389f9d  evidencias/T-022/pytest-score-estrito-transporte-rodada1.txt

Limite: a observação depende de o produto chamar `litellm.acompletion` pelo atributo do módulo, como faz hoje. Se isso mudar, a calibração fecha com `transporte_nao_observado` em vez de contar errado. Faithfulness, calibração e comparação reais continuam `Não executado`; a revisão independente segue pendente.

## Execuções reais autorizadas (dispatch `ctx_5450c9a9ee8e`, 2026-11-25)

Autorização técnica: parecer independente OpenAI (`revisao-independente-sessao-openai.md`) para uma calibração de 15 casos e uma Faithfulness com 2 chamadas por item, sem retentativa para obter sucesso. Langfuse desligado. Prompts, modelos, rubrica, dataset, limiares, baseline e `.env` não foram alterados. Nenhuma instalação, commit ou repetição para ficar verde.

### Calibração técnica do juiz de saída (E10)

1. **Tentativa 1 — Bloqueado (configuração), 0 chamadas a modelo.**
   - O CLI saiu com código 1, `{"status": "erro", "categoria": "entrada_ou_configuracao_invalida"}` (`calibracao-juiz-real.stderr.txt`), em 2 s, sem relatório.
   - Diagnóstico offline: `GenerationSettings.from_env` recusou o `.env` local porque `PROMPT_SOURCE` não estava definido ("deve ser escolhida explicitamente; suportada: offline", ADR-0016). A falha ocorreu antes de montar o serviço; nenhuma requisição saiu.
2. **Execução autorizada (R2), única com chamadas.**
   - Comando: `PROMPT_SOURCE=offline uv run --no-sync python -m evals.runner_geracao calibrar-juiz --max-calls 15 --executar-chamadas-reais --out evidencias/T-022/calibracao-juiz-real-r2.json`.
   - A fonte de prompts foi dada explicitamente no ambiente do processo, igual ao vínculo da rubrica, sem editar o `.env`.
   - Código 0; stdout e stderr vazios. Relatório `judge-calib-e538d61f941f`.
   - Juiz `openrouter/anthropic/claude-haiku-5.5`; `chat-output-judge-v1` / `chat-output-rubric-v1`; dataset `e3ac2a8f…`.
   - 15 chamadas a `judge`, 15 requisições admitidas, 15 observadas no transporte, todas com `{max_retries: 0, num_retries: 0}`. Nenhum 429/503 ou outro erro de provedor.

| Métrica | Valor |
| --- | --- |
| Concordância em `aprovado` | 14/15 (0,933) |
| Falsa aprovação | 1 de 9 negativos (**JUI-011**, R2: item trivial com a resposta no próprio enunciado; o juiz aprovou) |
| Falsa rejeição | 0 de 6 positivos (controles acadêmicos de R3 e R4 aprovados) |
| `fundamentado` (rotulados) | 13/13 (1,0) |
| `conforme_rubrica` (rotulados) | 10/11 (0,909) |
| Critério correto no motivo (negativos rejeitados) | 8/8 (1,0) |

Leitura técnica: neste corpus o juiz detectou todas as violações de R1, R3 e R4 e a de nível de Bloom (JUI-012), mas aprovou um item trivial (R2). É um sinal objetivo de fraqueza em trivialidade, a acompanhar na calibração humana. **Limite**: são 15 rótulos técnicos do implementador, não humanos. Não há limiar aprovado (`thresholds: null`), `human_calibration=false` e `production_ready=false`. Isso não substitui 50–100 casos rotulados por humano.

### Faithfulness do snapshot final da T-021

- Fonte: run `t021-real-22f305ef0d8849e4a5ffd4a38f86e6d2`, dispatch `ctx_86600b72d371`. Manifesto `evidencias/T-021/real-cli-r2/manifest.json`, com sha256 fixado pelo coordenador em `68d0268b…`, que confere.
- Captura, sem chamada de modelo:
  - linhagem Postgres do sandbox retido (leitura: 1 item, 1 chunk, 1 versão) → `artefato-r2-lineage.json`;
  - hashes do manifesto → `artefato-r2-manifesto.json`.
- Reverificação pelo loader contra o manifesto fixado: os dois são idênticos ao remontado (`reverificacao-loader-r2.txt`).
- `case_protocol` = `{kind: external, case_id: t021-cli-membrana-v1, request_sha256: 1a8ee800…}`, porque o manifesto novo registra o pedido.
- **Execução única:** `faithfulness --manifest …/real-cli-r2/manifest.json --manifest-sha256 68d0268b… --artifact evidencias/T-022/artefato-r2-lineage.json --source-dispatch ctx_86600b72d371 --max-calls 2 --executar-chamadas-reais`. Código 0; stdout e stderr vazios. Relatório `faith-c465863f39eb` (`faithfulness-real-r2.json`).
  - Juiz `openrouter/openai/gpt-6.1-sol`; RAGAS 0.4.4; prompt Faithfulness `93362d76…`.
  - 2 chamadas de 2 permitidas, sem retentativa, sem erro de provedor.
  - Resultado: **1 item (flashcard, chamada inicial, attempt 1, 1 contexto citado), Faithfulness 1,0**.
  - O relatório inclui as latências por etapa da T-021 (E4 30,4 s; E5–E7 14,1 s; E8 4,2 s; E10 2,3 s), os modelos, o reranker (`bge-reranker-v2-m3@953dc6f6`, corte 0,485) e o embedding `gemini-embedding-2@3072@fmt-v1`.
  - **Limite**: um único item; é medição de exemplo, não estimativa de qualidade da geração. Sem limiar, `production_ready=false`.
- **Comparação antes/depois: Não se aplica / Bloqueado.** O snapshot anterior (`t021-real-c513f0da…`) nunca foi avaliado com Faithfulness e seu manifesto não registra o pedido (`case_protocol=None`). A regra F5 bloqueia a comparação, e nenhum antes/depois foi fabricado. O histórico não foi usado como snapshot final.
- **Sandbox:** tentei avisar a T-021 (`ctx_86600b72d371`) de que PG/Qdrant e o env do sandbox podem ser limpos, mas o Orca recusou a mensagem direta (`task_not_found`: o dispatch não é helper deste). O aviso foi repassado ao coordenador no `worker_done`.

Hashes sha256:

    ebc858add0a01e6046c94083b872de5cc0c5d799dc5b7a2eda24d6731ffa94cb  evidencias/T-022/calibracao-juiz-real-r2.json
    e9ad43b435d9387fef924b9e45d05c4ca223b93c19f59e359711109614c1edee  evidencias/T-022/faithfulness-real-r2.json
    4ba4f444c3bbe2ee7f84ec6c8142f51fec3783e42ef452bb5b60ec770cd26870  evidencias/T-022/artefato-r2-lineage.json
    f203e3e724050d90ea464f67e70a1f4a0d5812426f82f858901098e182a6c1c5  evidencias/T-022/artefato-r2-manifesto.json
    60c5acb9459656efb1007226a40768f2af47eb4723d2808a1b8fb302dafae494  evidencias/T-022/reverificacao-loader-r2.txt
    68d0268b82f7dfe14116d7d9272e3f2579bee6a7ae6541ffea6d799d9f0d3fc3  evidencias/T-021/real-cli-r2/manifest.json
    474419821bd0218f97c484f662cf6242d499b96570056f2df6d4a3b9ee427819  evidencias/T-022/calibracao-juiz-real.stderr.txt
    cf205dbb8cea84897b488abcc281bf96698d5e94b1096b16657b4caba9082a22  evidencias/T-022/calibracao-juiz-real.exit.txt
    19eaf43821a7660ec323a87c8457bf74823beb296c39f5e01aa8a683aa50f061  evidencias/T-022/calibracao-juiz-real-r2.exit.txt
    19eaf43821a7660ec323a87c8457bf74823beb296c39f5e01aa8a683aa50f061  evidencias/T-022/faithfulness-real-r2.exit.txt

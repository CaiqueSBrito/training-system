# T-022 — reavaliação das correções F1–F6

Data: 2026-11-25. Alvo: só as correções F1–F6 em `evals/runner_geracao.py` e `evals/tests/test_geracao_runner.py` (despacho do executor `ctx_1b07747a04e9`), contra `revisao-openai.md` (`ctx_94f8e269a9f2`) e `relatorio.md`.

**Independência — declarar antes do veredito.** A tarefa registra `revisor: openai` (`tarefas/T-022.md`) e a regra dura 3 exige revisor de outro provedor. O autor efetivo da implementação e da correção foi **anthropic** (Opus 5.5, `ctx_4478531ceab3`). Esta reavaliação rodou no harness omp com modelo efetivo **anthropic/claude-opus-5**, porque a cota do CLI Codex/OpenAI acabou. Ou seja: **mesmo provedor do autor**. O nome do arquivo marca a vaga de revisão no pipeline, não o provedor efetivo. Vale como **reavaliação técnica e registro**; não substitui a revisão independente OpenAI nem é gate humano. O veredito independente de outro provedor sobre F1–F6 continua **Bloqueado** por falta de cota.

Veredito técnico: **F1–F6 corrigidos**; nenhum defeito bloqueante encontrado nas seis fronteiras reavaliadas. Restam três achados não bloqueantes (R1–R3), sendo R1 para o coordenador por envolver código do produto.

## Método e limites

- Leitura estática do runner e dos testes; leitura do caminho efetivo nas dependências instaladas (`ragas` 0.4.4, `instructor` 1.17.0, `litellm` 1.109.1) para conferir o que o código realmente faz na cadeia RAGAS → Instructor → LiteLLM.
- Conferência de sha256 dos arquivos tocados e do runner histórico.
- **Não executado** nesta reavaliação: build, lint, testes, formatadores, chamadas a API/modelo, instalação/download, git. Langfuse permaneceu desligado, nenhum `.env` ou segredo foi lido e nenhum conteúdo das capturas foi impresso. Nenhum arquivo além deste foi escrito; T-021, histórico, baseline e a tarefa não foram tocados.
- Os 86 testes offline são registro do executor (`pytest-correcao-f1-f6-r2.txt`), informado no despacho; esta reavaliação **não** os reexecutou e não afirma tê-los visto passar.

## F1 — orçamento × retentativas internas — corrigido

Cadeia conferida linha a linha, do runner até o SDK instalado:

- `build_faithfulness_metric` (`runner_geracao.py:833-849`) monta cliente próprio: `instructor.from_litellm(_admitted_transport(...))` e `llm_factory(..., adapter="litellm", max_retries=0)`. Não há mais reuso do helper histórico de `runner_ressalvas.py`.
- `llm_factory` (`ragas/llms/base.py:624`) repassa `**kwargs` ao adapter; `ragas/llms/adapters/litellm.py:16-45` só remove `cache`, então `max_retries=0` vira `model_args`; `ragas/llms/litellm_llm.py:246` faz `await client.chat.completions.create(..., **model_args)`.
- `AsyncInstructor.create` (`instructor/v2/core/client.py:793-840`) recebe `max_retries` como **parâmetro nomeado** — logo ele não é repassado ao transporte — e `instructor/v2/core/retry.py:570-571` usa `stop_after_attempt(max(max_retries, 0) + 1)`, isto é, **uma tentativa** com 0 (o padrão 3 daria quatro). `from_litellm` escolhe `AsyncInstructor` porque o transporte é corrotina (`instructor/v2/providers/litellm/client.py:55-57`).
- `_admitted_transport` (`:822-830`) chama `budget.admit()` **antes** de cada chamada real e força `api_key`, `no-log`, `num_retries=0`, `max_retries=0`, `timeout=60`. Conferido que `max_retries` chega ao cliente: o LiteLLM o carrega nos parâmetros e `litellm/llms/openai/openai.py:812` faz `inference_params.pop("max_retries", 2)` → `AsyncOpenAI(max_retries=0)`; sem esse argumento valeria `DEFAULT_MAX_RETRIES=2` (`litellm/constants.py:143`). É exatamente a lacuna que F1 apontava, agora fechada no transporte do Faithfulness.
- `CallBudget` (`:798-810`) recusa a chamada excedente com `EvalBudgetExceeded` (subclasse de `EvalBlockedError`); `classify_eval_exception` (`:860-876`) percorre `__cause__/__context__`, prioriza orçamento e depois `status_code`, preservando `retryable`; `_score_entries` (`:942-977`) para no primeiro erro retryable ou de orçamento e marca os demais como `Não executado`. `run_faithfulness_evaluation` (`:917-940`) ainda bloqueia antes de qualquer cliente quando `2 × itens > --max-calls`.
- Provas do autor cobrem a cadeia real sobre `TransporteDuble` com contagem exata (`tests:890`), payload inválido na 1ª e na 2ª etapa, 429/503/400 com status preservado, orçamento fechado na fronteira (`tests:945`) e **controle negativo** que, sem a correção, faz 4 tentativas e vaza (`tests:930`). O controle negativo é o que dá valor à prova.

## F2 — log do Instructor antes de handlers/lastResort — corrigido

- `redacted_sdk_logging` (`:776-795`) troca `logging.setLogRecordFactory`; em namespace de SDK, `msg`, `args`, `exc_info` e `sinfo` são substituídos **na criação do `LogRecord`**, antes de handler, filtro, filho tardio e `lastResort`, e a fábrica anterior é restaurada no `finally`. É proteção mais forte que o filtro por handler, que F2 mostrou ser insuficiente.
- Aplicada em `run_faithfulness_evaluation` (`:937`), `run_output_judge_calibration` (`:1078`) e em todo o `_run` do CLI (`:1294`), com `_guard_sdk_logging()` na entrada.
- Conferido que o caminho real de log cai nos namespaces cobertos: o `print_verbose` do LiteLLM escreve em `verbose_logger` (namespace `LiteLLM`), o banner impresso em stdout é desligado por `litellm.suppress_debug_info = True` (`:813-819`), e o erro final do Instructor sai em `instructor.v2.retry`. Testes verificam ausência do marcador em relatório, handler da raiz, filho tardio com nível DEBUG, stdout, stderr e `lastResort` (`tests:954`), com controle negativo.

## F3 — loader reverificado — corrigido

- `load_verified_artifact` (`:680-719`) exige o sha256 do manifesto **fixado fora do artefato**, bloqueia divergência (`EvalBlockedError`), remonta o artefato a partir das fontes ao lado do manifesto via `capture_artifact` (que reconfere bytes, hashes, identidade e vínculos de chamada) e, havendo artefato armazenado, compara campo a campo (`_COMPARED_FIELDS`). O objeto devolvido é sempre o **remontado**, então `verification.method` declarado deixa de funcionar como prova.
- `VerifiedArtifact` (`:667-677`) só nasce com token privado; `run_faithfulness_evaluation` (`:928-930`) recusa qualquer outro objeto antes de construir métrica ou tocar a rede, e o CLI reverifica antes de `EvalJudgeConfig.from_env` (`:1262-1265`).
- Testes cobrem owner/session forjados, troca de conteúdo coerente com o render, `response_sha256` obsoleto, fonte alterada depois do manifesto, manifesto fora do sha fixado, artefato cru e construção direta (`tests:1019` e seguintes). Reverificação do artefato real está em `reverificacao-loader.txt`.

## F4 — identidade obrigatória e caminhos exatos — corrigido

- `SandboxManifest` (`:509-556`) exige `run_id`, `owner_id`, `session_id`, `chat_turn_id`, `trace_id`, `case_id`, `document_version_ids` e `documents` **ou** `source_sha256` (não ambos). O validador rejeita chave vazia, `\`, caminho absoluto, `..`, forma não normalizada (`./x`) e duplicata por casefold, e exige as três chaves exatas de `REQUIRED_MANIFEST_FILES`.
- `_verify_with_manifest` (`:560-583`) exige que o arquivo usado seja exatamente `base/<nome declarado>` (sem achatar por basename), confere os sha256 e compara owner/session/turn/trace/versões e `response_status`; `case_id` é conferido logo após, na captura.
- Testes por campo ausente (7 casos), identidade divergente (4 campos) e alias/duplicata/travessia/arquivo faltante (`tests:1055` e seguintes).

## F5 — protocolo de dataset/pedido na comparação — corrigido

- `_case_protocol` (`:585-611`) grava `dataset_id`, `dataset_version`, `dataset_sha256`, `case_spec_sha256` e `request_sha256`, e exige que `message_sha256`/`count` do pedido executado batam com o caso; sem dataset, exige ao menos o pedido do manifesto; sem pedido verificável, devolve `None`.
- `_report_by_case` (`:1009-1027`) bloqueia relatório sem `case_id`, sem protocolo com `request_sha256` ou com protocolo de outro caso, e `compare_faithfulness_reports` (`:1029-1050`) compara `case_protocol`, `models`, `embedding_key` e `documents` por caso, deixando só o reranker como diferença aceita.
- A calibração passou a exigir e registrar `dataset_sha256` e a publicar `calls_made` (`:1072-1079`, `:1141-1143`).
- Comportamento no run real: como o manifesto da T-021 não registra o pedido, `case_protocol=None` e a comparação antes/depois fica bloqueada — resultado correto, não contornado.

## F6 — CLI sanitizado — corrigido

- `main` (`:1282-1309`) responde só com categorias próprias: `EvalBlockedError` → 3; `EvalError` → 1 com mensagem própria; `ValidationError` → 1 com apenas `error_count`; `OSError`/`ValueError` → 1 categórico; qualquer outra → `categoria: interna`. Nenhum `repr`, `input_value`, payload ou traceback é impresso, e nada é escrito em `--out` quando falha.
- Revisei as mensagens de `EvalConfigError` alcançáveis por arquivo não confiável: interpolam só identificadores curtos de contrato (`chunk_ref`, nome de campo, `case_id`) e contadores, nunca conteúdo do artefato ou do provedor.
- Testes com marcador sintético em campo extra do artefato, JSON inválido em `comparar` e payload inválido em `capturar` conferem ausência de marcador, de `input_value` e de `Traceback` (`tests:1100` e seguintes).

## Achados restantes

### R1 — média — orçamento da calibração do juiz não cobre as retentativas do SDK OpenAI

Local: `evals/runner_geracao.py:1081-1143` (contagem por chamada a `service.judge` e `protocol.retries: 0`); fronteira do produto em `packages/generation/src/training_generation/agents.py:390-408`.

O produto chama `litellm.acompletion` com `num_retries=0`, mas **sem** `max_retries=0`. Pelo código instalado, `litellm/llms/openai/openai.py:812` usa `inference_params.pop("max_retries", 2)` e `litellm/constants.py:143` define `DEFAULT_MAX_RETRIES = 2`; logo o cliente `AsyncOpenAI` pode repetir até duas vezes (408/429/5xx/erro de conexão) por caso julgado. Com `--max-calls 15`, o teto real é 45 requisições, e `protocol.retries: 0` afirma sobre um transporte que a T-022 não controla. É a mesma classe de F1, resolvida apenas no caminho do Faithfulness.

Encaminhamento: qualificar o campo no relatório da calibração (p. ex. `retries: {"litellm": 0, "openai_sdk": "padrão do produto"}`) e levar ao coordenador a decisão de forçar `max_retries=0` no `_completion` do produto — a T-022 não pode editar `packages/generation/`.

### R2 — baixa — cobertura de namespaces de log menor que a da guarda compartilhada

Local: `evals/runner_geracao.py:100-102` (`_SDK_LOG_NAMESPACES`).

Faltam `LiteLLM Router`, `LiteLLM Proxy` e `google.auth`, presentes em `training_llm._SDK_LOGGERS`; como `_is_sdk_logger` casa por igualdade ou prefixo com ponto, `"LiteLLM Router"` não é coberto por `"LiteLLM"`. Não há caminho atingível hoje — o runner usa `litellm.acompletion` direto e esses loggers só são usados nos módulos de roteamento/proxy — então é defesa em profundidade, não exposição demonstrada.

### R3 — baixa — `comparar` com JSON válido fora do schema cai em `categoria: interna`

Local: `evals/runner_geracao.py:1009-1035` (`before["config"]`, `report["artifacts"]`, `turnos[item["chat_turn_id"]]`).

A comparação não confere `schema` nem as chaves obrigatórias do relatório. Um objeto JSON que não seja relatório Faithfulness (ou com item apontando para turno ausente) levanta `KeyError`, capturado na fronteira final como `{"status": "erro", "categoria": "interna"}` com saída 1. Não há vazamento — nada do arquivo é impresso —, mas o erro de entrada esperado fica indistinguível de defeito interno, contra o pedido de F6 por categoria útil. Correção: validar `schema == training-evals/faithfulness-report/v1` e as chaves obrigatórias, bloqueando com mensagem categórica.

## Conferências de integridade

sha256 lidos localmente nesta reavaliação, todos iguais aos declarados:

    0f7ff48994c288e212195a8ed8a2d6f9ac69ecba21912a3e37f19e3bd6bba5ae  evals/runner_geracao.py
    67a2ea991d98fbca20876022b89c569a8712715049d19a79d22b9fe0f24c5a5d  evals/tests/test_geracao_runner.py
    4ebf5e8c7413d6b08901890c9a838acf536d3d2d18b6add1531f8a2b64acc1cd  evals/runner_ressalvas.py
    e3ac2a8f72147b43b393e3831406dcb760688f825a26b26eb4f9698f41609384  evals/datasets/geracao/dataset.json

O hash de `runner_ressalvas.py` é idêntico ao registrado em `2026-11-24-correcao-integral-ressalvas/evidencias/T-017/manifesto-integracao.json`: o runner histórico e a baseline continuam intactos, como a correção prometeu.

## Status das evidências

- Reavaliação estática de F1–F6 — **Passou** (esta nota).
- Suíte offline de T-022 — **Não executado** nesta reavaliação; registro do executor em `pytest-correcao-f1-f6-r2.txt`.
- Faithfulness real, calibração do juiz, RAG-JUI-003 e comparação antes/depois — **Não executado**: nenhuma chamada a modelo, nenhum score, nenhum limiar, Langfuse desligado, sem commit.
- Revisão independente de outro provedor sobre F1–F6 (regra dura 3) — **Bloqueado**: cota do CLI Codex/OpenAI esgotada; esta reavaliação rodou em `anthropic/claude-opus-5`, mesmo provedor do autor. Desbloqueio: reavaliação por worker OpenAI, ou decisão humana registrada.

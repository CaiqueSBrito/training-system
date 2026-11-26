# T-022 — revisão independente OpenAI

Data: 2026-11-25. Revisor efetivo OpenAI/Codex, dispatch `ctx_94f8e269a9f2`, tarefa Orca `task_31e875f51901`. Autor efetivo Anthropic, dispatch `ctx_4478531ceab3`: provedores distintos.

Veredito: **reprovado**, pelos achados F1–F6 abaixo. Não executar Faithfulness/RAGAS ou calibração real antes de corrigir e reavaliar as fronteiras de orçamento, segurança e proveniência. Esta conclusão é revisão técnica, não novo gate humano nem aceite de T-021/E1–E12/G4.

## Método e evidência

Lidos tarefa, relatório do executor, `evals/runner_geracao.py`, dataset/documentos e testes de T-022. Trechos das dependências locais e do cliente compartilhado foram consultados para conferir o caminho efetivo RAGAS → Instructor → LiteLLM, sem editar esses arquivos ou ampliar a revisão para o produto parcial de T-021. Nenhum `.env` ou segredo foi lido. Não foram impressos artefatos brutos de captura.

- **Passou** — registro do executor em `pytest-focal.txt`: 46 testes offline, 1 warning, 1,66 s. Isso comprova os casos executados pelo autor; os testes substituem a métrica inteira e não exercitam os retries/logs internos de Instructor.
- **Passou** — conferência independente por SHA-256 dos quatro arquivos seguros da captura real (E8, response, diagnostics, manifest): todos conferem com o relatório. Identidades owner/session/versions e turn/trace dos dois artefatos conferem com o manifesto atual. Nenhum conteúdo bruto foi exposto.
- **Passou** — os hashes dos três documentos do dataset conferem; há 15 casos de juiz e 3 casos de chat. Foram inspecionados os rótulos técnicos e seus controles acadêmicos.
- **Não executado** — APIs, suites, git, validador, instalação/download nesta revisão, conforme despacho. A conferência atual não reexecutou a leitura Postgres: o sandbox já foi removido. A verificação PG permanece evidência histórica do executor, sem alegação de repetição independente.
- **Não executado** — Faithfulness/calibração reais e comparação real antes/depois. Nenhum score ou baseline foi produzido nesta revisão. Langfuse permaneceu desligado e não houve commit.

## Achados

### F1 — alta — orçamento ignora as retentativas de parsing do Instructor

Local: `evals/runner_geracao.py:630`, `:685` e `:721`; caminho reutilizado em `evals/runner_ressalvas.py:668`/`:677`.

O runner reserva duas chamadas por item e declara `retries: 0`, mas não passa `max_retries=0` ao wrapper Instructor. `num_retries=0` é aplicado apenas à chamada LiteLLM. No código instalado, `ragas/llms/litellm_llm.py:246` chama `client.chat.completions.create` sem configurar o limite, `instructor/v2/core/client.py:797` usa `max_retries=3` e `instructor/v2/core/retry.py:571` permite `max_retries + 1` tentativas em erros de parsing. Cada uma das duas etapas Faithfulness pode fazer quatro chamadas; um item admitido com `--max-calls 2` pode consumir até oito.

Impacto: custo e reparo interno excedem o orçamento declarado, e a prova com `MetricaDuble` não detecta esse comportamento. O wrapper também pode transformar um erro HTTP em InstructorRetryException, perdendo a classificação direta por status usada para parar os demais itens.

Correção: desabilitar explicitamente as retentativas na fronteira Instructor usada por T-022 e manter `num_retries=0` no transporte; impor/contar a admissão na fronteira de cada chamada real. Acrescentar prova offline com o wrapper real e transporte de teste que devolve estrutura inválida, contando tentativas, e caso HTTP 429/503 encapsulado. A reutilização de um helper fora da propriedade da tarefa deve ser coordenada, sem editar silenciosamente o runner histórico.

### F2 — alta — logs do Instructor podem imprimir mensagens/respostas antes da sanitização

Local: `evals/runner_geracao.py:631` e `:702`; cliente reutilizado em `evals/runner_ressalvas.py:661`.

O catch do runner sanitiza o relatório depois da chamada, mas não protege o logger do Instructor. No SDK instalado, `instructor/v2/core/retry.py:49` usa `instructor.v2.retry`, `:604` registra a exceção da API em ERROR e `:716` registra o erro final, inclusive ValidationError com dados da resposta. A guarda compartilhada em `packages/llm/src/training_llm/__init__.py:88` não inclui `instructor`. Esses registros podem chegar a handlers da aplicação ou lastResort mesmo com `no-log` do LiteLLM.

Impacto: dados da resposta ou mensagem sensível do provedor podem sair por stderr/logs antes de `_sanitize_provider_exception`. Não foi observada nem provocada exposição de segredo real nesta revisão; a evidência é o caminho explícito de logging da dependência instalada.

Correção: incluir proteção efetiva do Instructor antes de entregar conteúdo à cadeia, cobrindo filhos tardios/lastResort e handlers, ou sanitizar a fronteira antes que mensagens sensíveis alcancem esse logger. Provar offline com wrapper real e marcador sintético em falha de parsing/provedor, verificando stdout/stderr/handlers. Desabilitar retries sozinho não elimina o log do erro final.

### F3 — alta — loader aceita artefato alterado sem revalidar sua proveniência

Local: `evals/runner_geracao.py:368`, `:643` e `:954`.

`faithfulness --artifact` só executa `GenerationEvalArtifact.model_validate_json`. O validator confere coerência interna e cobertura de versões, mas não reconfere `response_sha256`, `diagnostics_sha256`, `e8_capture_sha256`, manifesto ou linhagem. `verification.method/detail` são declarações lidas do próprio arquivo. Alterar somente `owner_id` de um artefato, ou alterar conteúdo original e sua cópia renderizada de modo coerente, mantendo os hashes/verificação antigos, não dispara uma checagem de integridade contra a origem. `_check_artifact_policy` tampouco verifica esses vínculos.

Impacto: a boa verificação feita em `capturar` não acompanha o uso posterior do arquivo; conteúdo manipulado pode ser avaliado e relatado como `lineage_postgres` ou `sandbox_manifest_hashes` sem ter essa prova. Não se constatou adulteração da captura real examinada, cujos hashes conferiram.

Correção: criar loader verificado para a etapa de avaliação, com vínculo do payload e identidade a evidência independente confiável (fontes/manifesto preservados e seus hashes, ou manifesto de captura persistido), verificando novamente antes de qualquer API. Um digest calculado somente sobre o próprio payload não demonstra que ele foi o verificado originalmente. Acrescentar casos de alteração de owner, conteúdo/render/contexto e hashes/verificação obsoletos; bloquear antes de construir/chamar a métrica.

### F4 — média — manifesto sem identidade é aceito como prova de owner/session

Local: `evals/runner_geracao.py:517`; teste que aceita esse caminho em `evals/tests/test_geracao_runner.py:329`.

A comparação de owner/session/turn/trace/versions só ocorre se a chave estiver no manifesto. O teste de hashes fornece apenas `files` e `documents` e recebe um artefato verificado. Assim, o mesmo manifesto sem identidade aceita valores arbitrários de owner/session fornecidos pelo chamador, sem prova correspondente. A igualdade dos hashes de conteúdo não preenche o vínculo de proprietário ausente.

Correção: exigir identidade completa em um schema de manifesto compatível com a prova prometida; formatos antigos sem esses campos devem bloquear ou ser explicitamente classificados como prova parcial, insuficiente para a avaliação. Validar caminhos/entradas exatos do manifesto em vez de achatar por basename em `:501`, rejeitando aliases/duplicatas ambíguos. Testar campos ausentes e identidades divergentes. O manifesto real inspecionado contém as identidades e não é evidência de falha concreta nesse run.

### F5 — média — comparação não vincula dataset nem pedido do caso

Local: `evals/runner_geracao.py:555`, `:661`, `:754` e `:850`.

`case_id` pode ser fornecido sem dataset. Mesmo quando há dataset, a captura só verifica existência do caso e hash do documento; não persiste identidade/hash do dataset nem o pedido/count/tipo como protocolo do caso. O relatório Faithfulness não contém esse vínculo, e `compare_faithfulness_reports` aceita o mesmo texto de `case_id`, modelos, documentos e configuração global. Dois datasets com o mesmo case_id/documento e pedidos ou quantidades diferentes passam como comparação compatível. A calibração também registra id/versão declarados, mas descarta o digest retornado pelo loader.

Impacto: o relatório pode atribuir uma diferença de tarefa/dataset ao reranker, contrariando o requisito de comparação compatível. O teste atual só muda prompt global/reranker e não cobre dataset/pedido alterados.

Correção: persistir e conferir dataset id/versão/hash, digest canônico da especificação do caso e vínculo ao pedido realmente executado. Exigir esse protocolo para comparar e registrar o hash do corpus de rótulos na calibração. Rejeitar dataset/pedido/count/tipo diferentes, mesmo com o mesmo case_id. Não reescrever a baseline histórica para obter compatibilidade.

### F6 — alta — erro de parsing do artefato escapa como traceback bruto no CLI

Local: `evals/runner_geracao.py:954` e `:972`.

O carregamento do artefato pode levantar Pydantic ValidationError, enquanto `main` captura somente EvalBlockedError/EvalError. Um artefato com campo extra contendo marcador privado gera erro com input_value desse campo e traceback não sanitizado. JSON inválido em `comparar`, falhas de I/O e erros de configuração externos também ficam fora dessa fronteira. Não é necessário chamar um modelo para atingir o caminho de carregamento inválido.

Correção: converter erros de parsing/configuração/I/O esperados em mensagens categóricas sanitizadas sem repr/input_value/caminho sensível, sem imprimir payload; preservar status/exit code útil. Acrescentar teste de CLI com marcador sintético no artefato inválido e conferir ausência em stdout/stderr e ausência de traceback. A correção deve tratar a entrada inválida na fronteira, sem mascarar falha como score ou sucesso.

## Partes conformes e delimitação

O mapeamento em `faithfulness_inputs` (`:593`) segue ADR-0006: frente/enunciado como pergunta e verso/alternativa correta + explicação como resposta original pré-escape. `_cited_chunks` (`:381`) usa o call_id do item, aplica o mapeamento local→global e confere chunk_id/evidência; não une contextos de chamadas distintas. Os contratos verificam chamada concluída, attempt inicial/reparo e índice único por chamada; os testes cobrem reparo e citações fora do contexto da própria chamada. Não há resposta de referência usada como item gerado nem score fixo no produto.

O loader do dataset resolve caminhos e verifica contenção após resolve (`:202`), hashes, literalidade de contextos/evidências e coerência dos rótulos. Os 15 casos são declarados técnicos do implementador, não humanos; relatórios conservam `human_calibration: false`, `production_ready: false` e `thresholds: null`. Isso não equivale a 50–100 casos aprovados por humano. A revisão não converte autoria de agente ou rotulagem técnica em aprovação humana.

As métricas de acurácia, falsa aprovação/rejeição e acurácia por campo usam somente julgados, com n e complete explícitos; ausência de resultados não recebe score padrão. `criterion_match_rate` é condicionada aos casos rejeitados por rótulo e pelo juiz, portanto deve ser lida junto das falsas aprovações. NaN/fora do intervalo/bool são rejeitados sem clamp. A interrupção por erro retryable funciona no nível do dublê, mas precisa da correção F1 para ser assegurada na cadeia real.

As proteções existentes de modelos/prompts, o opt-in do CLI e a ausência de escrita explícita na baseline histórica são positivos, mas não resolvem F1–F6. O desvio de lock relatado pelo autor continua para o coordenador, sem intervenção desta revisão. T-021 está em revisão independente própria: nenhuma aprovação ou reprovação do seu produto decorre deste relatório.

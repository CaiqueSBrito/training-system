# T-019 — reavaliação de segurança S1/S2

Data: 2026-11-25. Revisor efetivo OpenAI/Codex (`ctx_3ad4115a7293`), distinto do autor Anthropic (`ctx_bff1ab1f40c2`). Veredito: **aprovado com ressalvas**, limitado às duas correções. Relatórios anteriores preservados; não é aceite de E1–E12, produção ou G4.

## S1 — resolvida neste recorte

Status da inspeção: **Passou**. Em `packages/generation/src/training_generation/agents.py:117`, a antiga regex foi substituída por lista fechada de status/motivos/classes conhecidos e conjunto limitado de HTTP ASCII canônico. Valores externos desconhecidos viram `unknown` antes da construção da exceção ou do registro no span. Os testes em `packages/generation/tests/test_geracao_agentes.py:425`, `:440` e `:456` cobrem códigos, status do ADK e classes LiteLLM desconhecidos; a classificação conhecida 400/429/503 permanece preservada. Não foi identificada propagação arbitrária de dados pelo caminho que motivou S1.

## S2 — cobertura corrigida; isolamento residual S2-R

Status da cobertura de lastResort/filho tardio: **Passou** por inspeção e evidência focal do executor. A remoção dos handlers na fase de chamada, o controle negativo e o teste de sensibilidade verificam o caminho que o caplog podia mascarar. O teste com Runner ADK continua offline, com dublê somente do transporte no arquivo de testes. O limite declarado para reconfiguração de logger/handler durante a chamada continua explícito, sem garantia universal nova.

### S2-R — baixo — fixture deixa configuração global após o teste

Local: `packages/generation/tests/test_geracao_logging.py:54`, `:56`, `:63` e `:109`; snapshot em `:20`.

O teardown e seus asserts iteram apenas os nomes presentes no snapshot `adk`. O logger `google_adk.proprio_<uuid>` criado em `:107` recebe nível ERROR, handler com StringIO e filtros pela guarda; como não existia no snapshot, continua registrado com esse estado após o teste. Os filhos explícito/sensibilidade também mantêm nível próprio. Preservar identidades de loggers usados pelo SDK não exige preservar handlers e níveis adicionados exclusivamente pelo teste. Além disso, `_estado` guarda a lista de handlers, mas não os filtros de cada handler; a guarda altera esses filtros em objetos já existentes, cuja simples recolocação na lista não desfaz a mutação.

Impacto: poluição de estado entre testes e retenção de handlers/streams; possibilidade de resultados posteriores dependerem da ordem e de filtros remanescentes. Não foi constatado vazamento de produto ou segredo real. A afirmação em `:61` de que nada permanece no estado global é mais ampla que o teardown implementado.

Correção sugerida: salvar/restaurar também o estado dos handlers tocados; identificar loggers criados durante o teste e limpar seus handlers/filtros, níveis explícitos e demais configurações de teste, preservando as identidades necessárias ao SDK. Fechar somente handlers/streams pertencentes ao teste e demonstrar a limpeza em teardown, inclusive para o logger com handler próprio. Uma alternativa é isolar esta prova em processo separado. Não é necessário alterar a guarda de produto para resolver este achado.

## Evidência

`pytest-correcao-s1-s2.txt`: **Passou**, registro do executor com 27 testes e 41 desmarcados. Os cinco hashes publicados conferem. A focal comprova seus asserts atuais; não comprova a limpeza que esses asserts não verificam. APIs/suites/git/validador nesta revisão: **Não executado** por instrução expressa. Nenhum segredo, artefato antigo sensível ou implementação parcial de T-021 foi lido; nenhuma expansão da auditoria ocorreu.

# T-019 — reavaliação técnica e de segurança S2-R

Data: 2026-11-25. Revisor efetivo OpenAI/Codex, dispatch `ctx_cdccaf6a0f13`, tarefa Orca `task_b70bbd3c5d99`; autor da correção Anthropic, dispatch `ctx_ddbcb94c39d6`. Provedores distintos. Relatórios e vereditos anteriores permanecem como histórico.

Veredito: **aprovado no recorte T-019 revisado**. S2-R resolvida; S1 e a cobertura principal de S2 mantêm a aprovação anterior. Não foi identificado novo achado técnico ou de segurança na correção inspecionada. As ressalvas S1/S2/S2-R não permanecem abertas nesta revisão. Isso não antecipa a aprovação de T-021, E1–E12, prontidão de produção ou G4.

## Conferência da correção

Escopo: somente o novo estado de `packages/generation/tests/test_geracao_logging.py`, a seção correspondente de `relatorio.md` e `pytest-correcao-s2r.txt`. Hashes de agents/settings foram calculados somente para confirmar que a aplicação mantém os bytes da revisão anterior, sem nova auditoria de produto.

| Verificação | Status | Evidência por inspeção |
| --- | --- | --- |
| Identidade e configuração dos loggers preexistentes | Passou | `test_geracao_logging.py:49` captura objetos, níveis, filtros, handlers, propagate, disabled e parent; `:129` recoloca o objeto original quando houve substituição e `:144` restaura a configuração. A limpeza de nomes novos não remove nomes presentes na foto. |
| Filtros internos de handlers | Passou | `:41` inclui raiz, loggers e lastResort; `:62` captura nível/filtros por identidade do handler; `:153` restaura os próprios objetos e `:182` confere o resultado. Resolve a lacuna da cópia superficial de listas anterior. |
| Novos loggers e hierarquia | Passou | `:119` identifica novas entradas, `:129` remove as descartáveis e `:91` reconstrói loggerMap/parent, preservando o mapa histórico dos PlaceHolders. Loggers novos referenciados por módulos e ancestrais necessários são mantidos sem filtros da guarda (`:139`); a conferência distingue essas importações dos artefatos de teste. |
| Estado global adicional | Passou | `:149` restaura raiz/lastResort; `:155` restaura manager.disable e LITELLM_LOG; `:161` limpa caches de nível. O teardown da fixture em `:212` chama restauro e conferência em todos os testes. |
| Logger de usuário preservado | Passou | Fixture `:217` cria logger fictício antes da foto; teste `:320` altera nível/filtro/handler e manager.disable. O teardown posterior da fixture de usuário verifica identidade e estado, antes de remover somente seu próprio logger fictício. Não há exclusão de logger de usuário preexistente pelo mecanismo de limpeza. |
| Reparentamento e limpeza de artefatos | Passou | `:333` exercita PlaceHolder novo, conversão em Logger e neto reparentado; verifica remoção dos novos, identidade dos preexistentes e obtenção de objeto novo sem filtros/handlers/nível explícito residual. |
| Controles contra falso sucesso | Passou | Controle negativo em `:241`, filho tardio em `:249`, nível explícito em `:265`, handler próprio em `:275`, Runner ADK com transporte de teste em `:289` e controle sem filtro em `:306` permanecem. A remoção dos handlers continua na fase de chamada, sem mascaramento pelo caplog. |

O restauro inspecionado elimina a retenção de níveis/handlers de teste no manager e restaura filtros dos handlers preexistentes que motivaram S2-R. A manutenção de loggers de bibliotecas importadas é uma exceção explícita e conferida, para conservar suas referências de módulo. Não se afirma isolamento universal de qualquer biblioteca ou configuração concorrente; o limite documentado sobre referências somente em closures/atributos de classe permanece uma delimitação do harness, sem caso concreto observado neste recorte.

## Evidência focal e limites

`pytest-correcao-s2r.txt`: **Passou**, registro do executor com 8 testes em 0,70 s, incluindo teardown sem erro. O relatório preserva a tentativa anterior com 8 sucessos e 1 erro de teardown, que detectou a perda do loggerMap histórico, e o ajuste posterior. Não se apresenta a focal como reexecução dos 27 testes anteriores ou da suite completa.

Os quatro SHA-256 publicados (teste de logging, agents, settings e evidência focal) conferem com os bytes atuais. A aplicação mantém os hashes da revisão S1/S2. Conferência estática e da evidência: **Passou**; testes/APIs/suites/git/validador nesta revisão: **Não executado**, conforme despacho. Não houve instalação, download, leitura de segredos, ativação de Langfuse ou commit.

Somente este relatório e a seção Revisão da tarefa foram atualizados. Chat/CLI e a transformação compartilhada parcial de T-021 não foram inspecionados, editados ou julgados. Permanecem as delimitações anteriores sobre provas reais E4/E8, avaliação/calibração LLM e integração fim a fim; elas não são convertidas em evidência aprovada pelo fechamento de S2-R.

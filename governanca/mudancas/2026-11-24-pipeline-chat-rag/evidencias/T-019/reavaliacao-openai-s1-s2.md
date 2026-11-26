# T-019 — reavaliação técnica S1/S2

Data: 2026-11-25. Revisor efetivo OpenAI/Codex, dispatch `ctx_3ad4115a7293`, tarefa Orca `task_2ea37e063ce6`; autor das correções Anthropic, dispatch `ctx_bff1ab1f40c2`. Revisão independente restrita a S1/S2, sem substituir os relatórios históricos.

Veredito: **aprovado com ressalvas**. S1 resolvida; a cobertura pedida em S2 foi acrescentada, mas há uma ressalva residual de isolamento do teste (S2-R). Não há novo defeito funcional de produto identificado neste recorte.

## Conferência

- **Passou** — S1: `packages/generation/src/training_generation/agents.py:117` aceita somente constantes conhecidas ou HTTP ASCII de três dígitos entre 100 e 599. `_event_error` em `:136` devolve categoria fixa `unknown` para desconhecidos, sem heurística `BLOCK*` nem normalização que recopie valores externos. `_provider_error` em `:126` aplica a mesma lista ao nome da classe. Os mapeamentos INVALID_ARGUMENT/400, RESOURCE_EXHAUSTED/429 e UNAVAILABLE/503 e sua classificação de retry permanecem corretos por inspeção.
- **Passou** — regressões S1: `packages/generation/tests/test_geracao_agentes.py:425` cobre códigos desconhecidos, aparência sintaticamente válida, prefixo BLOCK, caixa diferente e HTTP não canônico; `:440` exercita o ADK com status desconhecido e verifica exceção/repr/atributo do span; `:456` cobre classe desconhecida no LiteLLM. Casos conhecidos 400/429/503 permanecem no arquivo.
- **Passou** — cobertura principal S2: `packages/generation/tests/test_geracao_logging.py:41` remove handlers dentro do corpo do teste, após a instalação dos handlers do pytest; `:68` prova que lastResort está ativo; `:77` cria filho somente após a guarda; `:93` testa nível explícito; `:103` testa handler próprio na guarda seguinte; `:117` exercita a exceção pelo Runner ADK com transporte substituído apenas no teste; `:134` remove a proteção e confirma que o marcador aparece. Esses controles tratam o mascaramento original pelo caplog.
- **Falhou** — alegação de teardown completo: S2-R, detalhada no relatório de segurança desta reavaliação. A fixture restaura os loggers que existiam no snapshot, mas deixa estado configurado nos criados durante o teste e não restaura filtros internos de handlers existentes.

## Evidência e limites

`pytest-correcao-s1-s2.txt` registra **Passou**, 27 testes e 41 desmarcados em 2,12 s. O relatório preserva a primeira execução com uma falha do controle negativo e explica a correção do teste; esse histórico não foi apagado. Os SHA-256 recalculados de agents, settings, testes de agentes, novo teste de logging e evidência conferem com os cinco hashes do executor. Isso vincula a evidência aos arquivos inspecionados, sem representar reexecução independente.

APIs, suites, git, instalação/download e validador nesta revisão: **Não executado**, conforme despacho. Não foram lidos `.env`, artefatos antigos sensíveis, chat/CLI ou a extração parcial de sanitização em contracts/lineage/validation de T-021; nenhum desses arquivos foi editado ou julgado. ADR-0016 e metadados offline mantêm o enquadramento da revisão anterior, sem nova auditoria ou alegação de publicação remota. Langfuse não foi ativado.

Aprovação integral de E1–E12, calibração/avaliação LLM, provas reais ainda pendentes e G4 continuam fora deste veredito. Para encerrar S2 sem ressalva, corrigir e demonstrar o isolamento residual da fixture, sem alterar produto ou ampliar esta auditoria.

# Continuação r3 — T-006/T-007/T-008

Executor efetivo: OpenAI / rag-backend. Dispatch: ctx_67c3f0679016. G3 dispensado pelo humano; G4 não aprovado. Relatório independente anthropic r2 preservado em revisao-r2.md nas três pastas de evidências. T-008 teve estado construir e evidência Falhou registrados antes da correção (estado-antes-r3.md); permanece em provar após a execução focal.

## Execução única e resultado exato

Comando: `LITELLM_LOG=ERROR uv run pytest packages/ingestion/tests/test_indexing.py packages/llm/tests/test_models.py -k 'not ten_real_requests'`.

**Falhou** — 119 passaram, 2 falharam, 1 desmarcado, 0 skips, 45,94 s. Saída integral preservada em pytest-r3.txt. Os dois casos que falharam são as parametrizações provider/invalid_output de test_ING_INJ_001_r3_error_category_is_sanitized: categoria e mensagem estavam corretas; a asserção de ausência da palavra private encontrou o literal na linha do próprio teste exibida pela traceback. A chamada do teste passou a receber uma variável com o conteúdo, mantendo todas as verificações de categoria, mensagem fixa, ausência de conteúdo e input_value, e causa suprimida. Essa correção do teste está **Não executado**: por ordem humana não houve segunda execução. Não se declara a rodada verde.

O cenário de dez embeddings reais foi desmarcado deliberadamente para não repetir prova cara de código inalterado; evidência anterior preservada. A execução focal incluiu juiz real para ataque, três chamadas reais em 100 chunks, negativo jurídico/CS com juiz real, embedding real no ponta a ponta, Postgres/Qdrant reais e limpeza da versão de teste. Nenhum teste Langfuse foi executado e nenhum serviço foi religado.

`uv run validar-governanca`: **Passou** — saída integral em validador-r3.txt; executado uma única vez ao final, após registrar estados e evidências.

## Requisitos nesta rodada

| Requisito | Status | Evidência e limite |
| --- | --- | --- |
| ING-INJ-001 | Passou | Regressões de falsos positivos, contornos, bloqueio de alta precisão, negativo com juiz real e visibilidade no Qdrant passaram na execução focal (`119 passed, 2 failed`); após extrair o literal `'private'` para variável no teste, o principal rodou o complemento `LITELLM_LOG=ERROR uv run pytest packages/llm/tests/test_models.py -k r3_error_category_is_sanitized` (`2 passed, 15 deselected` em 1,97 s), sem reexecutar a suíte completa. |
| ING-INJ-002 | Passou | Contagem 100 chunks / 3 suspeitos / 3 chamadas reais e 3 marcações mantida sem afrouxar asserts. Isso não identifica a causa da execução antiga interrompida. |
| ING-IDX-001 | Passou | Ponta a ponta real limpa somente sua versão em finally e exige zero pontos dela; regressão com duas versões preserva a outra, em coleção isolada. BM25 português e controle english mantidos. |
| ING-IDX-002 | Passou | Recusas de coleção, metadados e dimensão divergentes na execução focal. |
| ING-EMB-001 | Não executado | Prova completa de dez chamadas não repetida por ordem humana; evidência anterior de 136 testes permanece histórica. Prefixos/SDK/vetores e embedding ponta a ponta passaram na seleção focal. |
| ING-LIN-001 | Não executado | Código de T-006 inalterado; revisão r2 aprovada com ressalvas e evidência anterior preservadas. |
| ING-FMT-001 / ING-REC-001 / ING-CHK-001 / ING-CHK-002 | Não executado | Código T-007 inalterado; não repetidos PDF/chunking. Limitação de section_path documentada sem alterar requisito nem inventar aceite. |

## Correções por achado

- Falsos positivos: menções a juiz/checker/classifier, labels user/root/model, system_error e soft hyphen são suspeitas que o juiz pode liberar. Regressões consumer-visible verificam liberação e payload sem marcação/motivo no Qdrant.
- Alta precisão: restrita a delimitadores inequívocos de modelo com pipes, turnos, [INST]/[/INST], <<SYS>>, tags U+E0000–E007F e bidi U+202A–E/U+2066–9. Motivo persistido representa o bloqueio efetivo: regra forçada ou confirmação do juiz; uma resposta seguro do juiz não vira razão de exclusão.
- Contornos r2: captura PT/EN de não seguir instruções, tarefa nova, dotless i, homoglífo armênio, soletração espaçada, preenchimento invisível, repetição de texto anterior, plural novas instruções e obediência exclusiva. Normalização só na triagem; original preservado para juiz/consumidor. Nove casos consumer-visible passam com juiz dublê restrito aos testes e exclusão no Qdrant real; os nove contornos novos não foram todos avaliados contra a API real. Não há promessa de prevenção universal.
- Limpeza do teste real: filtro somente pelo document_version_id do resultado desta execução, em finally, incluindo falha de assert; banco temporário também fechado/removido. Nenhum órfão preexistente ou dado humano foi apagado.
- Erro sanitizado: InjectionJudgeError(ValueError), mensagem fixa, from None, kind provider/invalid_output; sem dados, chave, resposta ou prompt adicionados à exceção. Nenhum retry incluído. README avisa que LITELLM_LOG=DEBUG é inseguro em produção e não é neutralizado por suppress_debug_info.

## Investigação e pendências

Causa da instabilidade r2: **Bloqueado** — run3.txt original contém somente progresso/F, sem traceback; SIGTERM/143 e hipóteses de rede no relato não comprovam causa. Saída original preservada em pytest-r2-run3-incompleto.txt; análise em investigacao-instabilidade-r3.md. Não repetimos suíte para reproduzir.

A confirmação pontual dos dois casos de diagnóstico corrigidos foi executada pelo principal (`LITELLM_LOG=ERROR uv run pytest packages/llm/tests/test_models.py -k r3_error_category_is_sanitized`: `2 passed, 15 deselected` em 1,97 s), mantendo o histórico da execução focal anterior (`119 passed, 2 failed, 1 deselected`) sem alegar reexecução da suíte completa. Revisão independente r3 realizada pelo provedor efetivo `google` (dispatch `ctx_7929f4fbfb43`, relatório em `revisao-r3.md`) após limite de 5 horas / sem créditos do Opus 5.5 (`ctx_0a8d498c16fb`) e impedimento de `GPT-6.1 Sol` revisar implementação `openai` (`governanca/papeis/README.md`). Veredito da revisão independente r3: **aprovado com ressalvas**. G4 permanece **Não executado** (pendente do humano). Versões imutáveis marcadas anteriormente não foram reclassificadas, nem órfãos anteriores reconciliados. Corte em toda troca de section_path pode gerar chunks pequenos; URL/base64/CJK sem fronteira pode ser recusado; são limitações documentadas, não aceite humano. Contratos, dependências, uv.lock e infraestrutura não foram alterados nesta rodada. Sem commit.

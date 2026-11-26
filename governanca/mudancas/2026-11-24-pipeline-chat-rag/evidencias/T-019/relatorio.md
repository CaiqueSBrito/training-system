# T-019 — evidências (executor efetivo Anthropic/Opus 5.5, dispatch ctx_cc9dc1cd7150)

## Execuções

1. **Focal única com APIs reais** (`uv run --no-sync pytest packages/generation`): 102 passaram, 2 falharam, 19,5 s.
   - Falha 1 (`test_geracao_real.py::test_smoke_real_adk_classifica_e_planeja`): E3 real via ADK + Gemini devolveu um evento de erro que a primeira versão classificou como `GenerationError(kind="blocked")`. **A causa real não foi determinada**: o código do evento ficou oculto pelo defeito de classificação descrito abaixo, e a mensagem do provedor nunca é lida (sanitização). Status: **Falhou**. Não houve nova chamada real (orçamento de uma execução; nada de repetir até passar).
   - Falha 2 (offline): `test_erro_do_provedor_gemini_sanitizado_sem_retentativa`. Defeito real do produto: o ADK 2.14.1 converte exceções do modelo em `Event.error_code` textual (`UNAVAILABLE`, nome de classe etc.) e a primeira versão chamava todo código não numérico de `blocked`.
   - O smoke real do juiz de saída (`openrouter/anthropic/claude-haiku-5.5`, chamada única) **passou**: um veredito para o índice 0. Os valores `fundamentado`/`conforme_rubrica` impressos não foram capturados, porque a saída foi truncada pelo filtro do terminal; nenhum score é declarado.
2. **Correção**: `_event_error` mapeia os status canônicos do google-genai para HTTP (503/429 `retryable`), motivos de bloqueio para `blocked` e qualquer outro valor para `provider`, além de expor `provider_code` categórico sanitizado (nunca `error_message`) e usar `raise … from None`. Foram adicionados testes de regressão.
3. **Duas execuções corretivas só offline** (APIs reais desmarcadas): a primeira teve 109 passaram e 2 falharam (ajuste de `from None` e de código numérico), a segunda teve **111 passaram, 2 desmarcadas** (`pytest-offline.txt`).
4. `uv run validar-governanca`: OK (6 mudanças, 21 tarefas, 28 requisitos).

Desvio declarado: a instrução previa uma única focal. Rodaram 1 focal com APIs reais e 2 corretivas offline, sem chamada externa, para provar a correção de um defeito encontrado. A causa real do E3 continua **Bloqueado** até uma única chamada diagnóstica autorizada; com a correção, essa chamada já traria `provider_code`/`status_code`.

## Ferramentas

LSP Python indisponível nesta sessão (não usado). As interfaces do ADK 2.14.1 e do google-genai 2.34.0 foram conferidas pela leitura local do código instalado (`runners.py`, `llm_agent.py`, `google_llm.py`, `_transformers.py`, `workflow/_node_runner.py`) e pela conversão local de schema, sem rede.

## Decisões técnicas

- Layout: `packages/generation/src/training_generation/{agents,validation,settings}.py` e `prompts/`; `chat.py`/`cli.py` da T-021 ficam no mesmo pacote.
- E8 usa `GenerationOutput` restrito ao tipo pedido (`generation_response_schema`), derivado do contrato, porque o conversor do google-genai rejeita `discriminator`/`examples`. `RawGenerationOutput` recebe o resultado, e o ADK não valida o lote (sem `output_key`).
- Instrução ADK via InstructionProvider, sem injeção de estado `{var}`; `include_contents="none"`, sem ferramentas nem transferência; `HttpRetryOptions(attempts=1)`.
- `PROMPT_SOURCE=offline` é obrigatório e explícito (ADR-0016). Falta acrescentá-lo ao `.env.example` (fora do escopo desta tarefa).
- Guarda de logging: reusa `training_llm._guard_sdk_logging` (símbolo privado, sem editar `llm`) e acrescenta os loggers `google_adk`/`google.adk`, já que o ADK faz `logger.exception` com a mensagem do provedor.
- Duplicata: `DUPLICATE_RATIO = 0.9` (difflib sobre texto normalizado), valor inicial **não calibrado**.
- Rubrica `chat-output-rubric-v1`: R1 fundamentação, R2 pedagogia/Bloom, R3 estereótipos/atribuições sensíveis não fundadas com ressalva acadêmica e R4 instrução maliciosa/conteúdo perigoso. **Não calibrada**; não indica prontidão para produção.

## Diagnóstico real do E3 (dispatch ctx_d12b5ad850e6, 2026-11-25)

As falhas anteriores ficam preservadas: focal real com 102 passaram e 2 falharam.

1. **Chamada diagnóstica real única**, após a correção da classificação de erros: `GenerationError(kind="provider", stage="E3", status_code=400, retryable=False, provider_code="INVALID_ARGUMENT")`. Causa: a API Gemini Developer rejeita `additionalProperties` em `generation_config.response_schema`, campo que o google-genai serializa a partir de `extra="forbid"` dos contratos. Foi defeito de integração schema/ADK, não indisponibilidade nem bloqueio de segurança.
2. **Segundo defeito no mesmo diagnóstico**: o traceback do ADK (`logger.exception` em `google_adk.google.adk.workflow._node_runner`, logger filho criado sob demanda, sem filtro) chegou ao stderr pelo `logging.lastResort`, com a mensagem do provedor. Nesta chamada a mensagem não continha segredo nem prompt, mas o caminho podia vazar dados.
3. **Correções**:
   - `gemini_response_schema(contrato)` deriva o JSON Schema do contrato sem `additionalProperties`/`discriminator`/`examples`, para E3/E4/E8. Campos extras continuam rejeitados pela validação Pydantic da resposta.
   - `guard_logging` passou a pôr os ancestrais `google_adk`/`google.adk` em nível acima de CRITICAL e a filtrar o `lastResort`.
   - Regressões novas: schema sem `additionalProperties` após a conversão do google-genai, em todos os agentes; campo extra rejeitado; marcador da mensagem do provedor ausente de logs/stdout/stderr.
4. **Prova estreita offline** (`test_geracao_agentes.py` e `test_geracao_settings.py`): 79 passaram (`pytest-diagnostico-focal.txt`). A suíte offline completa (111) e o Haiku, que já passou, não foram reexecutados.
5. **Uma chamada real pós-correção do E3**: **Passou**, com `{"intencao": "gerar_flashcards", "modo": "consulta", "escopo": "fotossíntese"}` e saída de uma linha, sem traceback (`e3-real-pos-correcao.txt`). E4/E8 reais **não executados**; a mesma correção de schema cobre esses agentes, mas não há prova real deles.
6. `.env.example`: acrescentado `PROMPT_SOURCE=offline` (o `.env` real não foi lido nem editado).

Achado para o dono de `packages/llm` (não editado): `_guard_sdk_logging` tem a mesma fragilidade. Filtros só valem nos loggers que já existem em `loggerDict` no momento da guarda; filhos criados depois e o `lastResort` escapam. Recomendo a mesma estratégia de nível nos ancestrais, com revisão.

## Correção das ressalvas S1/S2 da revisão OpenAI (dispatch ctx_bff1ab1f40c2, 2026-11-25)

Origem: `revisao-openai.md` e `revisao-seguranca-openai.md` (ctx_e9413fc31c0a), aprovadas com ressalvas S1/S2. Histórico anterior preservado acima.

- **S1** (`agents.py`): a regex `_SAFE_CODE` foi removida. Agora `provider_code` só é propagado se pertencer a uma lista fechada: status canônicos do google-genai, motivos de bloqueio do modelo, HTTP de três dígitos (100–599) e categorias de exceção conhecidas (google-genai, LiteLLM/OpenAI, httpx/aiohttp, builtins). Qualquer outro valor, inclusive um que pareça código válido (`CONTEUDO_PRIVADO_DE_TESTE`, `sk_…`, `BLOCKED_BY_…`, `unavailable`, `0503`), vira `provider`/`provider_code="unknown"` e não é copiado para a exceção, o repr nem o span. A heurística `startswith("BLOCK")` foi removida. O mapeamento 400/429/503 já provado não mudou. Modelo, prompt, limiar e schema não mudaram.
- **S2** (`test_geracao_logging.py`, novo; `settings.py` sem alteração): a fixture salva e restaura o estado global e confere o restauro no teardown (raiz, loggers ADK existentes, `lastResort` e seus filtros). Os handlers da raiz são removidos **dentro do corpo do teste**, porque o pytest instala `LogCaptureHandler` na fase de chamada, depois do setup; isso foi detectado pelo controle negativo na primeira execução.
  - Controle negativo: um logger fora do ADK chega ao stderr pelo lastResort.
  - Regressões:
    - filho ADK criado só depois da guarda (`exception`/`critical`) sem vazamento;
    - filho com nível explícito, coberto pelo filtro do lastResort;
    - filho com handler próprio, filtrado na guarda seguinte;
    - exceção real do ADK sem handlers, sem vazamento.
  - Controle de sensibilidade: sem o filtro do lastResort, o mesmo registro aparece no stderr.
  - Limite: um logger com nível explícito e handler próprio configurado **durante** uma chamada, entre duas guardas, não é coberto.
- **Prova estreita offline** (seleção `-k` dos caminhos de erro/logging): primeira execução com 1 falha (o controle negativo detectou o mascaramento do pytest no teste, não no produto); após o ajuste do teste, **27 passaram, 41 desmarcados** (`pytest-correcao-s1-s2.txt`). Não foram repetidos: a suíte offline de 111, o Haiku real e o E3 real. E4/E8 reais ficam para a T-021.

Hashes sha256 após a correção:

    34546d8186c58c099dcdc6b78d94a5d5e59c3edaff647c6701ed1709e0d1ad5b  packages/generation/src/training_generation/agents.py
    33e0aec400b9a417138fd5d6ecdaeeb42f7b04ac145e29f128cc75c012cf7ac3  packages/generation/src/training_generation/settings.py
    f84c8eaf2532dd5e2274cb9924df515119159993cccc80688718ad763761e402  packages/generation/tests/test_geracao_agentes.py
    2d2761672c2cd485a5bec32758b4fb443a84aa2dc99a83902a5469b4af8527d4  packages/generation/tests/test_geracao_logging.py
    15316d36553e448a0c4355ac402c43a184900b89334e5ed799880a5134cf55f4  governanca/mudancas/2026-11-24-pipeline-chat-rag/evidencias/T-019/pytest-correcao-s1-s2.txt

## Correção S2-R do teardown de logging (dispatch ctx_ddbcb94c39d6, 2026-11-25)

Origem: `reavaliacao-openai-s1-s2.md` (S1 resolvida, S2 funcional, ressalva S2-R no teardown). Escopo: só `packages/generation/tests/test_geracao_logging.py` e evidência. Aplicação, prompts, modelo, limiar e schema sem alteração; os hashes de `agents.py`/`settings.py` abaixo podem ser comparados com a entrada anterior.

- A fixture `logging_isolado` agora fotografa todo o `logging.Logger.manager`: cada Logger e PlaceHolder (objeto, nível, filtros, handlers, `propagate`, `disabled`, `parent`, `loggerMap`), a raiz, o nível e os **filtros internos de todo handler alcançável** (raiz, loggers, `lastResort`), `manager.disable` e `LITELLM_LOG`, que a guarda define com `setdefault`.
- Restauro:
  - preexistentes voltam exatamente, com o mesmo objeto em `loggerDict`;
  - loggers criados pelo teste (nome com `TAG`) e loggers novos sem referência de módulo, como os criados pela guarda com `getLogger`, são **removidos** da hierarquia, assim como PlaceHolders novos órfãos;
  - loggers novos de biblioteca, criados por import tardio do ADK e referenciados pelo módulo, **não são apagados**, o que os desligaria da biblioteca, mas são neutralizados: os filtros da guarda saem deles e dos seus handlers;
  - os `loggerMap` originais voltam inteiros, porque o do stdlib é histórico e maior que a hierarquia mínima (a primeira tentativa de reconstruir do zero falhou na própria conferência), e os `parent` originais são mantidos;
  - `manager._clear_cache()`.
- Conferência no teardown (`_conferir`), em todos os testes do arquivo:
  - nenhum logger preexistente foi removido nem tem estado divergente;
  - raiz, `lastResort`, `disable` e `LITELLM_LOG` estão iguais;
  - todo handler preexistente tem o mesmo nível e os mesmos filtros;
  - nenhum logger com `TAG` permaneceu;
  - todo logger novo restante é de biblioteca e não carrega filtros da guarda.
- Provas novas:
  - logger de usuário fictício preexistente, com handler e filtros próprios, é alterado no teste (nível, filtro, filtro da guarda no handler, `manager.disable`, handler extra em `google_adk`) e sai intacto do teardown, conferido por uma segunda fixture depois da `logging_isolado`;
  - restauro explícito com PlaceHolder novo convertido em logger e neto reparentado: tudo removido, preexistentes com o mesmo objeto, e `getLogger` devolve um logger novo e limpo, sem referência ao removido.
- Sem esconder logs para passar: o controle negativo (lastResort ativo) e o de sensibilidade (sem filtro o vazamento aparece) continuam passando.
- **Focal estreita** (`test_geracao_logging.py`): primeira execução com 8 passaram e 1 erro de teardown (a própria conferência detectou a perda de `loggerMap` histórico); após o ajuste, **8 passaram** (`pytest-correcao-s2r.txt`). Não foram repetidos os 27 testes da correção anterior nem as chamadas reais.
- Limite residual: um logger de biblioteca referenciado só por objeto que não seja global de módulo (atributo de classe, closure) seria classificado como removível. Nenhum caso assim foi observado no caminho exercitado, e a conferência falharia se um logger preexistente fosse afetado.

Hashes sha256:

    1ca608bfb31cb7bb2ebccd75c49d2507f4083aa0344ce0f0d0882f2910f24711  packages/generation/tests/test_geracao_logging.py
    34546d8186c58c099dcdc6b78d94a5d5e59c3edaff647c6701ed1709e0d1ad5b  packages/generation/src/training_generation/agents.py
    33e0aec400b9a417138fd5d6ecdaeeb42f7b04ac145e29f128cc75c012cf7ac3  packages/generation/src/training_generation/settings.py
    4a29859ba797342fc64298644133c3d9ce379a09fd185c85d260656b43bd8acb  governanca/mudancas/2026-11-24-pipeline-chat-rag/evidencias/T-019/pytest-correcao-s2r.txt

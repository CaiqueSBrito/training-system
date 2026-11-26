# T-019 — revisão técnica independente OpenAI

Data: 2026-11-25. Revisor efetivo: OpenAI/Codex, dispatch `ctx_e9413fc31c0a`, tarefa Orca `task_1dd89408fc8b`. Produto de Anthropic/Opus, execução inicial `ctx_cc9dc1cd7150` e correção `ctx_d12b5ad850e6`: provedor distinto do revisor.

Veredito: **aprovado com ressalvas**, limitado à inspeção do serviço e das funções de T-019. Não é aprovação de E1–E12, de produção, de G4 ou da integração T-021. A revisão de segurança separada contém as ressalvas S1/S2.

## Método e limites

Lidos progresso, pipeline, papel seguranca-llm, tarefa/design/proposta, spec viva do chat, ADR-0016, `.env.example`, produto e testes de `packages/generation`, relatório e três evidências textuais seguras indicadas abaixo. Contratos e trechos das dependências locais foram consultados somente para conferir a semântica consumida. Não foram inspecionados chat/CLI futuros nem julgadas as alterações parciais de T-018/T-020. Nenhum artefato antigo potencialmente sensível foi aberto; `.env` não foi lido.

APIs, suites, instalações, downloads, git e validador: **Não executado**, por proibição expressa do despacho. Foi feita apenas conferência de existência das referências e SHA-256 com biblioteca padrão, sem importar produto ou executar testes. Uma tentativa inicial de usar `python` falhou por executável ausente; a conferência foi concluída com `python3`, sem instalar nada.

## Inspeção do produto

| Verificação | Status | Evidência e delimitação |
| --- | --- | --- |
| ADK real assíncrono sem ferramentas/transferência/histórico | Passou | `packages/generation/src/training_generation/agents.py:258` configura LlmAgent e Runner; `:344` chama `run_async` e fecha o stream. Não há mock no produto; dublês estão nos testes. |
| Configuração de modelos e fonte explícita | Passou | `packages/generation/src/training_generation/settings.py:141` exige modelos, credenciais e `offline`; `:155` recusa Gemini também roteado no juiz de saída e `:157` exige OpenRouter. `.env.example` inclui `PROMPT_SOURCE=offline`. |
| Adapter Gemini preserva contrato local | Passou | `agents.py:167` remove somente keywords incompatíveis; `:170` restringe E8 ao tipo solicitado. E3/E4 revalidam com Classification/SearchPlan em `:417`/`:433`; enums e invariante intenção/modo continuam nos contratos, sem ampliação da lista. E8 valida envelope bruto em `:486`, e o contrato estrito por item cabe a E9 (`validation.py:201`), não ao adapter. Não se afirma `strict=True` global do Pydantic: a garantia aqui é a dos contratos canônicos, incluindo rejeição de extras/enums inválidos. |
| Juízes com falha fechada no serviço | Passou | `agents.py:399` propaga erro sanitizado em falha/saída inválida; não devolve aprovação alternativa. `:495` chama E10 uma vez e `:520` exige exatamente os índices esperados. A recusa do turno e a ordenação E2/E3 são responsabilidade de T-021. |
| Um orçamento no orquestrador | Passou | `agents.py:280` usa `attempts=1`; `:380` usa `num_retries=0`; não há loop de reparo. `:452` aceita itens reprovados explicitamente e verifica a quantidade. Não prova o teto de reparo do turno, ainda em T-021. |
| Prompts/rubrica reais, hash e fonte | Passou | Os seis SHA-256 recalculados conferem com `prompts/manifest.json`. `prompts/__init__.py:53` verifica manifesto/arquivo/hash e `agents.py:302` registra referências reais, sem versão remota fictícia. ADR-0016 continua decisão sob autonomia com revisão/aceite delimitados. |
| Contexto como dado | Passou | `agents.py:466`/`:475` e `:509` delimitam contexto/julgamento com nonce; prompts instruem a tratar dados como não confiáveis. A inspeção da montagem não prova resistência empírica do modelo a ataques. |
| E9 por item, refs/evidência/questões/duplicatas | Passou | `validation.py:121`, `:149` e `:188` conferem schema, todas as citações, evidência normalizada no chunk correto, questões mal formadas, trivialidade e duplicatas no lote/itens anteriores. Erro de um item não libera nem derruba os demais. Similaridade 0,9 continua explicitamente não calibrada. |
| E11 e origem | Passou | `validation.py:243` escapa campos textuais, alternativas e evidência, remove controles e revalida; devolve original separado da cópia de entrega. `test_geracao_validacao.py:167` cobre preservação pré-escape; persistir o original corretamente depende da integração. |

## Evidência existente, sem reexecução

- **Falhou**: a focal histórica com 102 sucessos e 2 falhas permanece registrada em `relatorio.md`; não foi substituída pelo resultado posterior.
- **Passou** (registro do executor): `pytest-offline.txt`, 111 testes, 2 desmarcados. Essa prova antecede a última correção.
- **Passou** (registro do executor): `pytest-diagnostico-focal.txt`, 79 testes de agentes/settings após correção. Não representa reexecução da suite completa.
- **Passou** (artefato inspecionado): `e3-real-pos-correcao.txt` registra uma chamada E3 e classificação válida, stdout/stderr sem traceback. O relatório associa o diagnóstico anterior a 400 INVALID_ARGUMENT por `additionalProperties`. Não há atribuição retroativa à falha histórica sem captura.
- **Passou** apenas na estrutura, segundo `relatorio.md`: Haiku/OpenRouter real devolveu um veredito para índice 0 na execução anterior; os scores não foram capturados. Não foi reexecutado e não comprova qualidade/calibração.
- **Não executado**: E4/E8 reais após correção, integração E1–E12, teto de reparo do turno, trace `chat_turn`, RAGAS comparativo e calibração da rubrica nesta revisão. A ausência dessas provas impede aprovação integral da mudança; não deve ser preenchida com inferência a partir do smoke E3.

Não foi identificado defeito funcional bloqueante na inspeção delimitada. Recomenda-se regressão explícita de modo inválido/incompatível além do caso existente de intenção inválida; o código atual já delega essa rejeição ao contrato correto. Pendem a integração e suas provas próprias em T-021, as ressalvas de segurança e a avaliação exigida pela trilha LLM antes de declarar o comportamento global aprovado.

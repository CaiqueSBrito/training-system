# Estado desta slice de ressalvas

Ver resultado-slice-ressalvas.md: provas locais prontas para integrar; T-008 com corpus real Bloqueado por rotação. Este registro não concede G4 nem declara todas as ressalvas concluídas. Histórico abaixo.

# Resultado atual r3: complemento focal e revisão independente r3 concluídos (G4 pendente)

Ver `resultado-r3.md`, `pytest-r3.txt` e `revisao-r3.md`: na execução focal única r3, 119 passaram e 2 falharam; após corrigir a asserção dos dois testes, o principal executou o complemento `LITELLM_LOG=ERROR uv run pytest packages/llm/tests/test_models.py -k r3_error_category_is_sanitized` (`2 passed, 15 deselected` em 1,97 s), sem reexecutar a suíte completa. Revisão independente r3 por `google` (dispatch `ctx_7929f4fbfb43`): **aprovado com ressalvas**. Histórico abaixo não substitui o resultado atual.

# T-008 — resultado após correções

Data: 2026-11-24. Executor: openai / rag-backend. Correções autorizadas pelo humano no dispatch ctx_a87a3f65ae9b.

- `uv run pytest packages/lineage packages/ingestion packages/llm`: **Passou** — 136 testes, 0 skips, 10 avisos de dependências, 42,04 s; saída integral em `pytest.txt` (mesma execução conjunta nas três pastas).
- `uv run validar-governanca`: **Passou** — 4 mudanças, 9 tarefas, 28 requisitos; saída em `validador.txt`.
- `git diff --check -- packages/lineage packages/ingestion packages/llm .env.example`: **Passou**.
- API real: **Passou** — juiz Gemini configurado no `.env`, 10 embeddings individuais de documentos e um de consulta com 3072 dimensões, ingestão ponta a ponta em Postgres/Qdrant e 100 chunks com somente 3 chamadas reais ao juiz. Nenhum cenário omitido por falta de chave.
- RAGAS: **Não se aplica** — dispensa registrada no G1 desta mudança; a linha de base nasce na recuperação.
- Nova revisão por outro provedor e G4 após as correções: **Não executado** — cabem ao coordenador e ao humano.

Sem dependência nova, commit, alterações em contratos ou infraestrutura, ou edição da governança fora de evidências. A chave permaneceu somente no `.env` local. O `uv.lock` foi alterado pelo worker da T-004, não por este executor. Por decisão expressa do coordenador, `packages/llm/pyproject.toml` passou a exigir `google-genai>=2.30,<3` para compatibilidade com ADK; a suíte usou google-genai 2.34.0 sem adaptação necessária nas chamadas do SDK.

## Requisitos

| Requisito | Status | Prova |
| --- | --- | --- |
| ING-INJ-001 | Passou | Corpus PT/EN de contornos e negativos acadêmicos; título/seção/texto inspecionados; juiz real confirma ataque; alta precisão permanece marcada quando juiz libera; chunk marcado ausente no índice, presente na linhagem. |
| ING-INJ-002 | Passou | 100 chunks e apenas 3 suspeitos: exatamente 3 chamadas ao juiz real e 3 marcações; teste determinístico separado preserva proveniência. |
| ING-EMB-001 | Passou | Dez requisições reais individuais + consulta, vetores 3072, prefixos e título extraído; rejeição de vetor inválido. |
| ING-IDX-001 | Passou | Qdrant real dense/cosseno, sparse/IDF, BM25 português; bombeia encontra bombeiam; controle com indexação/consulta english não encontra; integração real de finalização/compensação. |
| ING-IDX-002 | Passou | Recusa de chave, metadados e dimensão divergentes; coleção validada na entrada pública. |

## Achados de segurança

- **A1 — Passou**: radicais e verbos/marcadores ampliados; papéis/turnos, IDs [C\d+], palavras com alfabetos mistos, Cf/tags Unicode e preenchedores invisíveis. Regra morta obedeça removida. Corpus permanente cobre cada família e negativos acadêmicos que falam sobre prompts sem ordenar ação.
- **A2 — Passou**: `InjectionChecker.check` recebe título + section_path + texto; a pipeline propaga o título a todos os chunks. Casos específicos cobrem ataque só no título ou na seção.
- **A3 — Passou**: papel/turno, Cf/tags e texto dirigido ao juiz/verificador/classifier/checker determinam marcação independentemente do veredito; falha do juiz ainda aborta ingestão. Prompt explica esse indício, retorno usa json_schema estrito e reason tem máximo de 512 caracteres, rotulado e documentado como não confiável. Auditoria de suspeitos liberados continua ressalva porque exigiria contrato.
- **A4 / anthropic A — Passou**: prefixos gemini/ e openrouter/ selecionam exclusivamente GEMINI_API_KEY e OPENROUTER_API_KEY, respectivamente; outros prefixos (inclusive modelo sem barra) são recusados. Testes capturam a chave passada ao LiteLLM; credenciais fictícias existem somente nos testes.
- **A5 — Passou**: erros de chamada/JSON/validação viram ValueError com mensagem fixa, `from None`, sem `input_value`; LiteLLM com suppress_debug_info=True. Regressões verificam JSON inválido, reason excessivo e exceção do provedor sem exposição na traceback formatada. Comportamento documentado no README.

## Achados anthropic

- **B — Passou**: removidos os dois padrões fixos do modelo do juiz; variáveis ausentes/vazias levam a recusa explícita. Nenhum modelo novo escolhido.
- **C — Passou**: helper público `training_ingestion.bm25_document` fixa portuguese e é usado pela indexação e pelas consultas de teste; pronto para ser importado pela futura recuperação. Controle english em Qdrant real não encontra bombeiam com consulta bombeia.
- **E — Passou**: `Ingestor` público exige processor I4–I6; `ingest_document` constrói a pipeline real obrigatória. A entrada parcial foi renomeada `_PartialIngestor`, não exportada, e fica restrita aos testes de I1–I3; reaproveitamento completo mantém verificação dos pontos. Sem novo campo no contrato.
- **F — Passou**: parser retém primeiro título de nível 1 extraído pelo Docling e usa nome do arquivo apenas como fallback. Teste com arquivo filename.md exige título extraído no embedding. README corrigido para I1–I6.

Nits G–I, limites de reconciliação distribuída, futura exclusão na E5 e aspectos operacionais de Qdrant continuam ressalvas; nenhum foi implementado. O teste ponta a ponta usa banco temporário migrado, pois o banco principal local estava sem tabelas; não migrou nem apagou a base do usuário. Os testes com dublês permanecem exclusivamente em arquivos de teste, além dos cenários adicionais com API real.

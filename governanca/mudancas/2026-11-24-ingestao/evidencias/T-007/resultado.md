# T-007 — resultado após correções

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
| ING-FMT-001 | Passou | Docling real em PDF, DOCX e Markdown, persistência em Postgres e recusas atômicas. |
| ING-REC-001 | Passou | Hash, isolamento por dono, duplicatas concorrentes, reenvio sem parsear e rollback. |
| ING-CHK-001 | Passou | Proveniência, offsets, IDs determinísticos e `section_path` Biology > Details equivalente nos três formatos. |
| ING-CHK-002 | Passou | Limites, sobreposição condicionada a múltiplas unidades, fragmentação em fronteiras de palavras e tabelas indivisíveis. |

## Achados corrigidos

1. **Passou** — `core.Chunker.split` só sobrepõe se o chunk emitido contém mais de uma unidade. Regressão usa alvo menor que um parágrafo e exige intervalos distintos, sem conter integralmente o chunk anterior.
2. **Passou** — fragmentos buscam espaço ou fim de frase dentro do orçamento UTF-8, mantendo todos os caracteres e offsets. Palavra sem fronteira que excede o limite do fragmento é recusada explicitamente; regressão específica. Versão do chunker atualizada para `structure-v2`. O teste antigo com strings sem espaços foi adaptado para parágrafos com palavras; exige avanço e sobreposição quando há múltiplas unidades, conforme a correção autorizada.
3. **Passou** — fixture DOCX inclui Heading2 e estilo OOXML correspondente; gerador reproduzível e teste de caminho equivalente em PDF/DOCX/Markdown. Nenhuma mudança na política de corte em toda troca de section_path.

Os demais achados (offsets canônicos não persistidos, parse dentro da transação, recriação do conversor e página inicial) continuam ressalvas. O caminho I1–I3 usado nos testes é agora interno, conforme correção E da T-008.


## Continuação r3 — ctx_67c3f0679016

Revisão independente r2 preservada em `revisao-r2.md`: aprovada com ressalvas. Código desta tarefa não mudou nesta rodada; a evidência anterior foi preservada, sem repetição de suíte. G4 não aprovado.
A política de cortar em toda troca de section_path permanece e pode produzir chunks pequenos; isto é limitação pendente, não aceite inventado. Palavra sem espaço acima do limite continua recusada (inclui URL/base64/CJK); a política não mudou.

# Estado desta slice de ressalvas

Ver resultado-slice-ressalvas.md: provas locais prontas para integrar; T-008 com corpus real Bloqueado por rotação. Este registro não concede G4 nem declara todas as ressalvas concluídas. Histórico abaixo.

# T-006 — resultado após correções

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
| ING-LIN-001 | Passou | 27 testes de linhagem em Postgres real: contratos, FK, unicidade, ordem, rollback, migrations e imutabilidade das duas tabelas. |

## Achados corrigidos

1. **Passou** — migration incremental `0002_document_immutable.py`, sem reescrever a 0001, protege versões existentes e novas por triggers de UPDATE, DELETE e TRUNCATE. Regressão tenta alterar sha256, owner_id e parser_version por SQL direto; inclui DELETE/TRUNCATE. Upgrade/downgrade passam.
2. **Passou** — `test_migrated_schema_matches_metadata` compara `tables.metadata` ao banco migrado com `alembic.autogenerate.compare_metadata` e exige lista vazia.

O nit sobre `migrate()` foi mantido como ressalva, conforme escopo. README atualizado para as duas migrations e duas tabelas imutáveis.


## Continuação r3 — ctx_67c3f0679016

Revisão independente r2 preservada em `revisao-r2.md`: aprovada com ressalvas. Código desta tarefa não mudou nesta rodada; a evidência anterior foi preservada, sem repetição de suíte. G4 não aprovado.

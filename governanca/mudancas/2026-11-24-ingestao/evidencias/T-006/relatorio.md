# T-006 — ING-LIN-001

Status: **Passou** (implementação e provas; revisão de outro provedor e G4 pendentes).

## Arquivos

- `packages/lineage/pyproject.toml`: pacote `training-lineage`, módulo `training_lineage`, build `uv_build`, dependência workspace em `training-contracts` e as três bibliotecas aprovadas.
- `packages/lineage/src/training_lineage/{__init__,config,tables,repository,migration}.py`: API pública, configuração `.env`, tabelas SQLAlchemy 2 Core, repositório e execução Alembic.
- `packages/lineage/src/training_lineage/migrations/env.py` e `migrations/versions/0001_ingestion.py`: ambiente Alembic e migration reversível.
- `packages/lineage/alembic.ini`: entrada CLI para migrations.
- `packages/lineage/tests/test_lineage.py`: testes escritos antes da implementação, identificados com `ING_LIN_001`.
- `packages/lineage/README.md`: uso, esquema, transações, ordenação e configuração.
- `uv.lock`: dependências resolvidas, preservando os membros de workspace e alterações já existentes de outros workers.
- Esta pasta: `pytest.txt`, `validador.txt`, `relatorio.md`.

Nenhum contrato, outro pacote, infraestrutura ou arquivo de governança fora desta pasta foi editado; `.env.example` não precisou de variável nova.

## Esquema

`document_version`: chave primária textual `document_version_id`; `document_id`,
`owner_id`, `sha256`, `parser_version` textuais obrigatórios; `created_at TIMESTAMPTZ`
obrigatório; identificadores não vazios, hash hexadecimal minúsculo de 64 caracteres,
unicidade `(owner_id, sha256)`.

`chunk`: chave primária textual `chunk_id`; `document_version_id` obrigatório com FK
para `document_version` e `ON DELETE RESTRICT`; `chunker_version TEXT`,
`page BIGINT NULL`, `section_path TEXT[]`, `start_offset/end_offset BIGINT`,
`text TEXT`, `injection_flagged BOOLEAN`, `injection_reason TEXT NULL`.
Checks de página, offsets, texto, identificadores, seção e coerência da marcação de injeção.
Triggers impedem UPDATE, DELETE e TRUNCATE, inclusive SQL direto.
Índice `(document_version_id, page, start_offset, end_offset, chunk_id)`.
`alembic_version` registra a revisão `0001`.

O repositório revalida registros na entrada e saída e usa a transação da Connection
fornecida, sem commit interno. Ordenação: página crescente, NULL por último, offsets
inicial/final e ID para desempate; sem páginas, offsets determinam a ordem.
Datas sem fuso são interpretadas como UTC; datas com fuso preservam o instante.
Chunks marcados são preservados nas consultas de auditoria: a exclusão da recuperação
é responsabilidade da ingestão/recuperação.

## Provas

- **Passou** — `uv sync --all-packages`; psycopg 3.3.9 (publicado em 2026-11-12), SQLAlchemy 2.0.58 (2026-11-16) e Alembic 1.20.1 (2026-10-22), todos anteriores ao limite de sete dias do workspace em 2026-11-24.
- **Passou** — teste primeiro: antes da implementação, `uv run pytest packages/lineage` encerrou com código 2, `ImportError: cannot import name 'LineageRepository' from 'training_lineage'`, comportamento ainda inexistente.
- **Passou** — execução final `uv run pytest packages/lineage`: **21 passed in 3.35s**, exit code 0; saída em `pytest.txt`.
- **Passou** — roundtrip com contratos, página ausente/seção vazia, marcação e motivo de injeção, FK sem versão, separação por versão, ordenação paginada e sem páginas, desempates, duplicatas, rollback e revalidação.
- **Passou** — rejeição de UPDATE (inclusive sem mudança), DELETE, TRUNCATE e estados inválidos por SQL direto, com confirmação de que o chunk original permanece.
- **Passou** — upgrade/downgrade/reupgrade em banco inicialmente vazio (`template0`), inclusive remoção da função de imutabilidade no downgrade; os testes usam e removem apenas bancos temporários `test_lineage_<uuid>`, sem alterar os dados do banco da aplicação.
- **Passou** — `uv run validar-governanca`: `governança OK: 3 mudança(s), 8 tarefa(s), 28 requisito(s) na spec viva`; exit code 0, saída em `validador.txt`.
- **Passou** — `uv build --package training-lineage --out-dir /tmp/training-lineage-build`; wheel e sdist gerados, wheel inspecionado e contém ambiente e revisão Alembic dentro de `training_lineage/migrations/`.
- **Passou** — `git diff --check` sem saída e exit code 0.
- **Não se aplica** — RAGAS nesta tarefa: G1 da proposta autoriza provas determinísticas/de integração para os requisitos ING; baseline será criada na mudança de recuperação.
- **Não executado** — revisão anthropic e aceite humano G4: próximos passos do orquestrador; sem commit.

Documentação técnica consultada: [SQLAlchemy Engine Configuration](https://docs.sqlalchemy.org/en/20/core/engines.html) e [Alembic, compartilhamento de conexão](https://alembic.sqlalchemy.org/en/latest/cookbook.html#connection-sharing).

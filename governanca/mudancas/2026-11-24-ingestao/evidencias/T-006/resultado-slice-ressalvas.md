# T-006 — slice de ressalvas / ctx_3017e7ea9815

Executor OpenAI / rag-backend. Autorização humana ampliada: “corrija todas as ressalvas”; G3 previamente dispensado, sem G4. Estado construir registrado antes da implementação, agora provar para integração. A afirmação histórica “migrate só aceita head/base” era imprecisa: a leitura confirmou que a subida intermediária já funcionava; faltava a descida intermediária.

## Corrigido e provado

**ING-LIN-001 — Passou**: os 50 testes de lineage passaram na execução focal inicial. Recorte em pytest-lineage-ressalvas.txt, saída conjunta sanitizada em ../T-008/pytest-slice-ressalvas.txt. Essa mesma execução global teve 60 passed / 22 failed / 8 errors, causados fora de lineage pela guarda SDK; não é uma suíte integrada verde e não foi repetida.

- migrate resolve alvo exato pelo ScriptDirectory Alembic, infere upgrade/downgrade pela ancestralidade e recusa prefixos ambíguos, deslocamentos, intervalos e alvos desconhecidos antes de DDL. Nenhuma migration histórica foi reescrita.
- Roundtrip base → 0001 → 0002 → 0001 → head → base em Postgres temporário real: preservação de linhas entre revisões, imutabilidade UPDATE/DELETE/TRUNCATE das duas tabelas, remoção da função/triggers document_version ao voltar a 0001, preservação dos triggers chunk e remoção final das duas funções/triggers no downgrade base.
- Cada CHECK migrado foi exercitado por INSERT SQL direto e nome de constraint, além de compare_metadata já existente. Alvo inválido preserva registros e revisão.

## Pendente

Integração com a outra slice e G4: **Não executado**. Revisão independente Google desta slice concluída no dispatch `ctx_d072ae09d0c3` (`revisao-slice-ressalvas-google.md` e `../T-008/revisao-slice-ressalvas-google.md`): **aprovado na slice local (itens 1 e 2 sobre as revisões 0001/0002)**, sem exigir reexecução dos 50 testes de `lineage` já aprovados. Nenhuma edição em tables.py, repository.py, migrations de schema, contratos, dependências ou uv.lock. Sem commit. Validador: **Passou**, 5 mudanças / 17 tarefas / 28 requisitos; executado uma única vez ao final desta slice, saída em validador-slice-ressalvas.txt.

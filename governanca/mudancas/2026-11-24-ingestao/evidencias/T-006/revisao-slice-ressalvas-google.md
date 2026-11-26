# Revisão independente (Google) — Slice de ressalvas `ctx_3017e7ea9815` (T-006)

- **Tarefa**: T-006 (`2026-11-24-ingestao`) — itens **1** (`ING-MIG-001`) e **2** (`ING-MIG-002`) da matriz de 16 ressalvas.
- **Provedor efetivo da revisão**: `google` (Antigravity / Gemini 4 Argon, dispatch `ctx_d072ae09d0c3`, task `task_705a0f068a0e`)
- **Autor da implementação (mantido)**: `openai` (`rag-backend`, dispatch `ctx_3017e7ea9815`)
- **Relatório completo da slice**: ver `../T-008/revisao-slice-ressalvas-google.md`.
- **Veredito para a slice de T-006 (itens 1 e 2 sobre revisões 0001/0002)**: **aprovado na slice local** (50/50 testes de `packages/lineage/tests/test_lineage.py` passaram em `pytest-lineage-ressalvas.txt`; integração com as futuras tabelas aditivas de T-011 e Gate G4 permanecem pendentes).

## Resumo técnico (`arquivo:linha`)

1. **Upgrade e downgrade intermediário atômico sem dano (`packages/lineage/src/training_lineage/migration.py:14-54`)**:
   - Valida `target` estrito (`str` sem espaços, `'base'`, `'head'` ou revisão exata existente em `ScriptDirectory`), recusando prefixos parciais (`'000'`), deslocamentos (`'-1'`, `'+1'`), intervalos (`'0001:0002'`), SQL injection, vazio ou `None` com `ValueError('Revisão de migração inválida') from None` antes de abrir DDL (`:23-32`).
   - Determina a revisão atual via `MigrationContext.configure(connection).get_current_heads()`, recusa múltiplos heads (`:34-36`), trata `current == target_id` como no-op (`:41-42`) e escolhe `command.upgrade` ou `command.downgrade` pela ancestralidade real em `script.walk_revisions` (`:43-54`).
2. **Ciclo de vida de triggers/funções e preservação de dados (`packages/lineage/tests/test_lineage.py:245-288`)**:
   - `test_ING_LIN_001_intermediate_roundtrip_and_trigger_lifecycle` (`:245-275`) comprova o roundtrip `base → 0001 → 0002 → 0001 → head → 0002 → base` preservando registros, removendo `reject_document_version_mutation` e seus triggers no downgrade a `0001` enquanto mantém `chunk_immutable_%`, e limpando ambas as funções e triggers no downgrade a `base`.
   - `test_ING_LIN_001_invalid_target_preserves_data` (`:277-288`) comprova que 8 alvos inválidos não alteram `alembic_version` nem os dados.
3. **Verificação individual de CHECK constraints (`packages/lineage/tests/test_lineage.py:290-314`)**:
   - `test_ING_LIN_001_each_migrated_check` exercita cada uma das 14 violações de `CHECK` em `document_version` e `chunk` via `INSERT` direto e confere `caught.value.orig.diag.constraint_name == constraint`.

# Tarefa <T-NNN> — <título>

Você executa o papel `<papel>` do Training System. Antes de qualquer ação, leia:
1. `AGENTS.md` (regras duras, ordem de autoridade);
2. `governanca/papeis/<papel>.md` (o que é seu e o que não é);
3. `governanca/mudancas/<mudanca>/tarefas/<T-NNN>.md` (requisitos, escopo, plano);
4. os requisitos citados em `governanca/specs/` e no `delta.md` da mudança.

## O que fazer

<passos concretos>

## Pode editar

<pastas e arquivos>

## Não pode

- editar fora das pastas acima, alterar casos aprovados, adicionar dependência, fazer commit;
- entregar mock ou placeholder em código de aplicação.

## Pronto quando

- cada requisito da tarefa tem evidência no frontmatter com status exato;
- `uv run pytest` e `uv run validar-governanca` passam (saída anexada em `evidencias/<T-NNN>/`);
- [trilha LLM] diff do RAGAS anexado conforme `governanca/instrucoes/avaliacao-guiada.md`.

Comando que termina com código diferente de zero significa tarefa **não** concluída: reporte `Falhou` ou `Bloqueado` com o motivo.

## Ao terminar

Atualize o estado da tarefa para `provar` (ou `bloqueada` com `bloqueio`) e reporte: o que mudou, evidências e pendências.

---
name: governanca-arquivar
description: Arquiva uma mudança concluída do Training System — aplica o delta.md à spec viva, move a pasta para governanca/mudancas/arquivo, atualiza o progresso e roda o validador. Use depois do aceite final (gate G4) ou quando o usuário pede para "arquivar" ou "fechar" uma mudança.
---

# Arquivar uma mudança

Pré-condição: aceite final do humano (G4) e todas as tarefas em `concluida` ou `cancelada`. Sem isso, pare e reporte o que falta.

1. Aplique o `delta.md` à spec viva indicada nele (`governanca/specs/<capacidade>/spec.md`; crie a partir de `governanca/modelos/spec.md` se não existir):
   - ADICIONA → acrescente cada requisito (título, frase EARS, cenários) na seção Requisitos;
   - MODIFICA → substitua o requisito de mesmo ID pelo texto novo;
   - REMOVE → apague o requisito de mesmo ID.
2. Na `proposta.md`, `estado: concluida`.
3. Mova a pasta: `git mv governanca/mudancas/<id> governanca/mudancas/arquivo/<id>`.
4. Acrescente no topo de `governanca/progresso/progresso.md` a entrada da mudança (o que foi entregue, evidências principais, pendências).
5. Para cada falha encontrada durante a mudança, registre o caso em `governanca/progresso/regressao.md` e confira que o caso executável existe.
6. Rode `uv run validar-governanca` e corrija até passar. O commit continua com o humano.

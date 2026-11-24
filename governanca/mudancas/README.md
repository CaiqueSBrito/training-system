# Mudanças

```text
mudancas/<AAAA-MM-DD-slug>/
  proposta.md          obrigatório (frontmatter: id, titulo, trilha, estado, aprovacao)
  delta.md             requisitos ADICIONA / MODIFICA / REMOVE
  design.md            só com decisão técnica local
  tarefas/T-NNN.md     uma por arquivo
  evidencias/T-NNN/    saídas de teste, diffs de avaliação, prints
mudancas/arquivo/      mudanças concluídas ou canceladas, com o delta já aplicado à spec viva
```

Criar: skill `governanca-mudanca`. Arquivar: skill `governanca-arquivar`. Conferir: `uv run validar-governanca`.

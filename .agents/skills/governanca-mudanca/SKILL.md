---
name: governanca-mudanca
description: Abre uma mudança no Training System — cria a pasta em governanca/mudancas com proposta, delta e tarefas a partir dos modelos e roda o validador. Use quando um pedido muda comportamento (trilha Padrão ou LLM) ou o usuário pede para "abrir mudança", "criar proposta" ou "criar tarefa".
---

# Abrir uma mudança

1. Escolha a trilha em `governanca/pipeline.md`. Trilha Rápida não abre mudança: registre em `governanca/progresso/progresso.md` e pare.
2. Crie `governanca/mudancas/<AAAA-MM-DD-slug>/` (data de hoje; slug em minúsculas com hífens).
3. Copie `governanca/modelos/proposta.md` para `proposta.md`; preencha `id` (= nome da pasta), `titulo`, `trilha`; deixe `estado: proposta` e `aprovacao: null`. Escreva por quê, o que muda, fora do escopo, riscos e `[ESCLARECER]`.
4. **Pare no gate G1**: a proposta vai ao humano. Só depois da aprovação registre `aprovacao: { por: humano, em: <data>, palavras: "<frase literal>" }` e `estado: aprovada`.
5. Copie `governanca/modelos/delta.md` para `delta.md`; escreva os requisitos EARS em ADICIONA / MODIFICA / REMOVE (`governanca/instrucoes/rastreabilidade.md`). IDs de ADICIONA não podem existir na spec viva; os de MODIFICA/REMOVE precisam existir.
6. Para cada unidade executável, copie `governanca/modelos/tarefa.md` para `tarefas/T-NNN.md` (próximo número livre no repositório: `grep -rhoE '^id: T-[0-9]+' governanca/mudancas | sort -V | tail -1`). Preencha requisitos, papel, provedor e revisor conforme `governanca/papeis/README.md`, `depende_de` e o plano. Estado inicial: `aguardando-permissao`.
7. Rode `uv run validar-governanca` e corrija até passar.
8. **Pare no gate G2**: cada tarefa precisa da permissão do humano antes de `construir`.

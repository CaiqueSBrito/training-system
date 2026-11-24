# Progresso

Entrada mais recente no topo. Cada entrada: data, o que foi feito, estado do validador, próximo passo e pendências com o humano.

## 2026-11-24 — mudança `2026-11-24-spec-contratos-chat` concluída e arquivada

- Spec viva criada: `governanca/specs/chat/spec.md` (22 requisitos) e `governanca/specs/ingestao/spec.md` (6).
- `packages/contracts` (T-001, papel `arquiteto`, construída por anthropic e revisada por google): modelos Pydantic e 19 JSON Schemas; 189 testes passam; RAG-CLS-001, RAG-PLN-001 e RAG-GEN-002 com evidência `Passou`. Revisão: reprovado → aprovado com ressalvas → aprovado. Aceite do humano (G4): "Aceito".
- Decisões do humano: intenções do E3 (G1), Bloom `compreender` (taxonomia revisada), E1 sem intenção nem modo.
- Três casos de regressão registrados (`regressao.md`, REG-001 a REG-003).
- Ambiente: Antigravity CLI oficial nativo em `~/.local/bin/agy` (o Snap comunitário não acessa `uv` nem o Orca).
- Vigilância: `_ItemBase` depende de `__pydantic_base_init__`; conferir ao atualizar o Pydantic.
- Validador: OK (1 mudança arquivada, 1 tarefa, 28 requisitos na spec viva).

Próximo passo: commit da fundação pelo humano (habilita worktrees isolados para os workers); depois, a próxima mudança de implementação (trilha LLM).

## 2026-11-24 — fundação

- Arquitetura da pipeline RAG aceita (`docs/arquitetura/pipeline-rag.md`) e ADRs 0001–0007 aceitas.
- Workspace uv (Python 3.13, carência de 7 dias) com `packages/llm` (litellm 1.109.1).
- Governança neutra criada: `AGENTS.md`, `CLAUDE.md`, `.omp/RULES.md`, `governanca/` (pipeline, papéis, instruções, modelos), skills em `.agents/skills/` e validador `uv run validar-governanca`.
- Sem specs vivas e sem mudanças abertas.

Próximo passo: primeira mudança (spec e contratos da pipeline de chat).

Pendências com o humano (arquitetura §11): banco da linhagem, parser de documentos, lista de intenções do E3, rubrica de viés/padrão do E10, calibração do juiz de saída, plataforma de CI.

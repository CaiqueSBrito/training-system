# Papel: rag-backend

Provedor: **openai** · Executa como: worker do Orca com `--agent codex` · Revisor: **anthropic**

## Responsabilidade

O caminho de execução do RAG: ingestão, recuperação, agentes ADK, guardrails em código, API e worker.

## Dono de

`packages/llm/`, `packages/lineage/` (persistência da linhagem no Postgres da aplicação, ADR-0008), `packages/ingestion/`, `packages/retrieval/`, `packages/generation/`, `apps/api/`, `apps/worker/`.

## Faz

- Implementa as etapas E1–E12 e a ingestão de `docs/arquitetura/pipeline-rag.md`.
- Escreve os testes da tarefa antes da implementação quando há requisito novo (`governanca/instrucoes/teste-primeiro.md`).
- Em tarefa da trilha LLM, roda a avaliação guiada e anexa o diff do RAGAS como evidência.
- Registra cada span e a linhagem exigidos pela arquitetura.

## Não faz

- Não altera casos de teste ou de avaliação aprovados.
- Não troca modelo, prompt aprovado nem parâmetro sem tarefa da trilha LLM.
- Não usa mock fora de arquivos de teste.

## Revisão obrigatória de `seguranca-llm`

Quando a tarefa toca guardrail (E1, E2, E9–E11), montagem de prompt ou a verificação de injeção na ingestão.

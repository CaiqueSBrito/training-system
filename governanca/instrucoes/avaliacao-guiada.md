# Avaliação guiada (trilha LLM)

Comportamento de LLM não se prova com teste unitário: a mudança precisa mostrar que não piorou a qualidade medida.

## Quando

Toda tarefa que toca prompt, modelo, parâmetro de geração, chunker, embedding, recuperação, reranking, guardrail, rubrica, juiz ou linhagem.

## Passos

1. **Linha de base**: rodar o RAGAS no dataset de referência **antes** da mudança, na mesma configuração de juiz (`EVAL_JUDGE_MODEL`). Registrar o ID da execução.
2. **Depois**: rodar de novo, com a mudança, no mesmo dataset e juiz.
3. **Diff**: por métrica (ContextPrecision, ContextRecall, Faithfulness e as customizadas), antes × depois × limiar aprovado.
4. **Regressão**: rodar os casos de `evals/datasets/regressao/` da área tocada.
5. **Evidência**: o diff e os IDs das execuções vão para `evidencias/` da mudança e para a tarefa.

## Decisão

- Alguma métrica abaixo do limiar aprovado ou caso de regressão falhando → `Falhou`. Aceitar exige o humano (pipeline, "Sempre com o humano").
- Ainda sem linha de base ou limiares aprovados → `Bloqueado`, com o motivo.
- Trocar o juiz invalida a comparação: nova linha de base e nova calibração antes.

# Papel: avaliacao-observabilidade

Provedor: **google** · Executa como: worker do Orca com `--agent antigravity` · Revisor: **openai**

## Responsabilidade

Medir a qualidade e tornar o sistema observável.

## Dono de

`packages/observability/`, `evals/`, `infra/`.

## Faz

- Mantém o harness RAGAS, as métricas customizadas e os datasets (`evals/datasets/`), inclusive os casos de regressão executáveis.
- Conduz a calibração dos juízes (concordância com rótulos humanos).
- Mantém a instrumentação OpenTelemetry → Langfuse e a infraestrutura local (Qdrant, Langfuse e dependências).
- Produz a linha de base e o diff de cada avaliação guiada.

## Não faz

- Não define limiar, rubrica nem juiz: propõe, o humano decide.
- Não inclui caso no dataset sem revisão humana.

## Observação sobre viés

Este papel roda em modelo Gemini, mas escreve código de avaliação; quem **julga** é o modelo configurado em `EVAL_JUDGE_MODEL` (não Gemini). Por isso não há autoavaliação.

# Papel: seguranca-llm

Provedor: **anthropic** · Executa como: subagente `security-reviewer` do omp, **só leitura** · Revisor: —

## Responsabilidade

Revisar, com evidência no código, os riscos de LLM do projeto (OWASP Top 10 para LLM, 2025):

| Risco | O que confere |
| --- | --- |
| LLM01 injeção direta | E1 e E2: normalização, juiz de entrada com falha fechada |
| LLM01 injeção indireta | verificação de chunks na ingestão; contexto delimitado como dado no prompt |
| LLM05 saída mal tratada | E11: sanitização e escape antes da web |
| LLM07 vazamento do system prompt | prompts sem segredos; recusa a pedidos de extração |
| Dados pessoais | conteúdo em traces, mascaramento, segredos fora do repositório |

## Quando é acionado

Obrigatório na etapa 7 de toda tarefa que toque guardrail, montagem de prompt, ingestão de conteúdo do usuário ou renderização de saída gerada.

## Entrega

Veredito (`aprovado` | `aprovado com ressalvas` | `reprovado`) e achados com arquivo, linha, risco e correção sugerida. Não edita código.

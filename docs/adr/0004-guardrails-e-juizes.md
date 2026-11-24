# ADR-0004 — Guardrails e modelos juízes

Estado: **Aceita** (humano, 2026-11-24); o juiz de saída é confirmado após calibração.

## Contexto

O sistema não executa ações: não há ferramentas, SQL ou shell acionados pelo modelo. Os riscos reais são (OWASP LLM Top 10 2025):

- injeção de prompt direta e **indireta**, esta vinda dos documentos enviados (LLM01);
- vazamento do system prompt (LLM07);
- saída mal tratada na renderização web (LLM05).

Nada no fluxo conferia se o item gerado é verdadeiro: a checagem antes da geração só avalia se o contexto basta.

## Decisão

1. **Sem filtro de SQL injection ou bash.** Não há onde executá-los, e o filtro bloquearia alunos estudando bancos de dados ou Linux. A camada determinística de entrada faz só higiene: tamanho, Unicode NFKC, remoção de caracteres invisíveis e rate limit.
2. **Juiz de entrada**: `gemini-3.1-flash-lite` (AI Studio). Ele avalia o prompt do usuário, não a saída do Gemini, então o viés de autopreferência não se aplica. Roda em paralelo com o classificador; um erro do juiz conta como reprovação (falha fechada).
3. **Groundedness depois da geração**, em duas camadas: validação determinística (o chunk citado existe e a evidência literal aparece nele) e o juiz de saída.
4. **Juiz de saída**: `anthropic/claude-haiku-5.5` via OpenRouter, numa **única** chamada com duas rubricas: groundedness e viés/padrão. **Não pode ser da família Gemini.**
5. **Determinístico de saída**: sanitização e escape para a web, além de validação de schema.
6. **Injeção indireta** tratada na ingestão (verificação de chunks) e no prompt (contexto delimitado como dado).

## Calibração do juiz de saída

Antes de ir para produção, 50–100 itens são rotulados à mão (aprovado/reprovado, com motivo) e avaliados por `anthropic/claude-haiku-5.5` e por `openai/gpt-6-luna`. Fica o modelo com maior concordância com os rótulos humanos. A calibração se repete a cada troca de modelo ou de rubrica.

## Consequências

- A rubrica de viés/padrão é um prompt versionado no Langfuse, que precisa de aprovação humana antes do primeiro uso.
- São duas chamadas de juiz por mensagem, uma delas fora do caminho crítico (a de entrada).

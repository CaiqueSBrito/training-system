# Segurança de Aplicações com Modelos de Linguagem (OWASP LLM)

Aplicações baseadas em geração aumentada por recuperação (RAG) processam documentos submetidos por usuários como fonte de conhecimento externo. Esse fluxo introduz o risco de injeção indireta de prompt (OWASP LLM01), na qual instruções ocultas no documento tentam desviar o comportamento do modelo ou extrair configurações.

## Defesa em Profundidade na Ingestão e Recuperação

A defesa contra injeção indireta combina triagem determinística (normalização Unicode NFKD, detecção de caracteres invisíveis, tags Unicode, controles bidirecionais e padrões léxicos) com avaliação por um modelo juiz independente nos trechos suspeitos. Todo documento recuperado deve ser delimitado explicitamente como dado não confiável, e nunca interpretado como comando operacional.

### Contexto Jurídico e Técnico Legítimo

Em textos de direito processual, expressões como "Cabe ao juiz decidir" ou "O juiz, ao receber a denúncia, decide" descrevem a autoridade judicial e não constituem comando ao verificador automatizado. Da mesma forma, arquivos de configuração contendo `user: root` ou cabeçalhos `#include <system_error>` são artefatos técnicos legítimos que o juiz semântico deve diferenciar de ataques ativos.

## Sanitização de Diagnósticos e Proteção de Segredos

Falhas em chamadas a provedores de LLM ou erros de validação de schema (OWASP LLM05 e LLM07) não podem expor chaves de API, prompts completos nem o conteúdo bruto do documento em logs, tracebacks, stdout ou stderr. O subsistema de chamada bloqueia a execução caso níveis de depuração inseguros (`LITELLM_LOG=DEBUG`) ou callbacks não autorizados estejam ativos.

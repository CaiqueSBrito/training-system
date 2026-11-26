# Papéis

Um arquivo por papel. O nome do arquivo (sem `.md`) é o valor do campo `papel` nas tarefas; o validador recusa papel inexistente.

| Papel | Provedor | Revisor |
| --- | --- | --- |
| [orquestrador](orquestrador.md) | anthropic | humano |
| [requisitos](requisitos.md) | anthropic | humano |
| [arquiteto](arquiteto.md) | anthropic | google |
| [rag-backend](rag-backend.md) | openai | anthropic |
| [avaliacao-observabilidade](avaliacao-observabilidade.md) | google | openai |
| [frontend](frontend.md) | google | openai |
| [seguranca-llm](seguranca-llm.md) | anthropic | — |

Provedores válidos: `anthropic`, `openai`, `google`. Mudar papel, provedor ou revisor exige aprovação do humano.

## Modelos e fallback de execução

- Primário dos papéis e revisões atribuídos a `anthropic`: **Opus 5.5**.
- Fallback autorizado pelo humano: **GPT-6.1 Sol**, via `codex --model gpt-6.1-sol` no despacho do Orca (`--agent codex --model gpt-6.1-sol`).
- Acionar o fallback quando o primário estiver indisponível, limitado ou falhar; registrar na tarefa o provedor efetivo, o modelo, o motivo e o novo `orca_dispatch`. Não alterar a atribuição nominal dos papéis.
- A independência considera o provedor efetivo: GPT-6.1 Sol não pode revisar uma implementação feita por OpenAI. Nesse caso, usar revisor Google; se indisponível, bloquear a revisão, nunca autoaprovar.
- O fallback é conduzido pelo orquestrador por novo despacho; não pressupõe failover automático do Orca. Não muda modelos da aplicação RAG.
- Autorização: "bote o 6.1 Sol como fallback para o Opus 5.5 e também na governança, continue o trabalho".

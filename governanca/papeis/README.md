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

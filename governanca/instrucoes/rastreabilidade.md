# Rastreabilidade

## Cadeia

```text
requisito EARS (ID) → mudança → tarefa (T-NNN) → commit (Refs: T-NNN) → teste / caso de avaliação (cita o ID) → evidência (arquivo ou trace do Langfuse)
```

Cada elo aponta para o anterior; o validador confere os elos que estão dentro de `governanca/`.

## IDs

| Item | Formato | Exemplo |
| --- | --- | --- |
| Requisito | `ÁREA-SUBÁREA-NNN` em maiúsculas | `RAG-GRD-003` |
| Mudança | pasta `AAAA-MM-DD-slug` | `2026-11-25-guardrail-entrada` |
| Tarefa | `T-NNN`, único no repositório | `T-014` |

Áreas iniciais (novas áreas entram por mudança aprovada): `ING` ingestão, `RAG` pipeline de chat, `EVAL` avaliação, `OBS` observabilidade, `WEB` interface, `SEC` segurança.

## Requisitos em EARS

Cada requisito é um título `### <ID> — <nome curto>` seguido de **uma** frase num destes padrões:

| Padrão | Forma |
| --- | --- |
| Ubíquo | O sistema DEVE <resposta>. |
| Evento | QUANDO <gatilho>, o sistema DEVE <resposta>. |
| Estado | ENQUANTO <estado>, o sistema DEVE <resposta>. |
| Indesejado | SE <condição indesejada>, ENTÃO o sistema DEVE <resposta>. |
| Opcional | ONDE <recurso presente>, o sistema DEVE <resposta>. |

Depois da frase, um ou mais **cenários** (`Dado / Quando / Então`) que viram testes ou casos de avaliação. Requisito que não dá para testar está mal escrito.

Exemplo:

```md
### RAG-GRD-003 — Recusa de prompt malicioso
QUANDO o juiz de entrada classificar o prompt como malicioso, o sistema DEVE recusar a mensagem sem executar as etapas E3 a E12.

- Cenário: Dado um prompt pedindo o system prompt, Quando a mensagem é enviada, Então a resposta é uma recusa e o trace não tem spans de E3 a E12.
```

## No código

- Teste e caso de avaliação citam o ID no nome ou na docstring (`test_rag_grd_003_recusa_prompt_malicioso`).
- Comentário só para o que o código não diz; o ID do requisito vai no teste, não espalhado no código de produção.
- Commit: assunto no imperativo e o trailer `Refs: T-NNN` (um por tarefa).

## Na evidência

Cada entrada de `evidencias` da tarefa aponta para um requisito (`criterio`), diz o método, o status exato e uma referência: caminho em `evidencias/` da mudança, ID de trace do Langfuse ou ID de execução de avaliação. A última entrada de cada requisito é a que vale.

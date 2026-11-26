# T-005 — inspeção da ADR-0009 contra ING-FMT-001

Data: 2026-11-24 · Papel: arquiteto (anthropic)

| Verificação | Onde na ADR-0009 | Status |
| --- | --- | --- |
| Docling como parser, decisão do humano em 2026-11-24 | cabeçalho; Decisão, item 1 | Passou |
| Formatos da v1: PDF, DOCX e Markdown (ING-FMT-001) | Contexto; Decisão, item 2 | Passou |
| Outro formato recusado com motivo `formato_nao_suportado`, sem gravar nada (cenário de ING-FMT-001) | Decisão, item 2 (checagem na etapa 1, antes do parser) | Passou |
| Preserva página, títulos/seções e tabelas | Decisão, item 3 | Passou |
| Página ausente em DOCX/Markdown coerente com os contratos (`page: int \| None` em `packages/contracts`) | Decisão, item 3 | Passou |
| Alternativas consideradas | seção própria (5 opções) | Passou |
| Consequência: o Docling puxa `torch` | Consequências, item 1 | Passou |
| Estado "Aceita (humano, 2026-11-24)" | cabeçalho | Passou |
| Seções Contexto, Decisão, Alternativas consideradas, Consequências | corpo | Passou |
| §11 item 2 marcado como resolvido, apontando para a ADR-0009 | `docs/arquitetura/pipeline-rag.md` §11 | Passou |
| Linha 0009 no índice | `docs/README.md` | Passou |
| Cenário de ING-FMT-001 em execução (`.xlsx` recusado) | — | Não se aplica: a T-005 só documenta; a prova em execução é da implementação da ingestão (T-007) |
| Validador | `validador.txt` | Passou (exit 0) |
| Casos de teste (G3) | — | Não se aplica: tarefa só de documentação |

Observação fora do escopo: `docs/arquitetura/pipeline-rag.md` §3, passo 2, ainda diz "o parser ainda está em aberto, seção 10" (e a seção certa é a 11). A T-005 só pode editar a linha do item 2 da §11; fica para o orquestrador decidir quem corrige.

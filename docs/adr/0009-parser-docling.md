# ADR-0009 — Docling como parser de documentos

Estado: **Aceita** (humano, 2026-11-24). Resolve o item 2 da arquitetura §11.

## Contexto

A etapa 2 da ingestão (arquitetura §3) extrai o texto do arquivo recebido. As etapas seguintes dependem do que o parser entregar:

- o chunker (etapa 3, ING-CHK-001) divide pela estrutura do documento, não atravessa seção de nível 1 e guarda página, `section_path` e offsets em cada chunk;
- a citação (§7) navega de item → citação → chunk → **página** do documento, então a página precisa sair do parser, não ser estimada depois;
- tabelas são conteúdo frequente em material de estudo; achatadas em texto corrido, perdem a relação linha × coluna e geram chunks que o modelo interpreta mal.

A mudança `2026-11-24-ingestao` fixou os formatos da v1 em PDF, DOCX e Markdown (ING-FMT-001), recusando qualquer outro com motivo e sem gravar nada. Faltava escolher o parser; o item 2 da §11 deixava a ingestão bloqueada.

## Decisão

- O parser da ingestão é o **Docling** (decisão do humano em 2026-11-24). Um só parser para os três formatos, com saída num modelo de documento único (`DoclingDocument`), de onde o chunker lê a estrutura.
- **Formatos da v1: PDF, DOCX e Markdown.** O Docling lê outros formatos, mas a ingestão só aceita esses três: a checagem acontece na etapa 1 (Receber), antes de chamar o parser, e um formato fora da lista é recusado com motivo `formato_nao_suportado` sem criar versão de documento (ING-FMT-001). Acrescentar formato é mudança nova.
- O parser **precisa preservar**, e a ingestão usa a partir da saída dele:
  - **página**: o número da página de origem de cada elemento (no PDF; DOCX e Markdown não têm paginação fixa, e o registro de proveniência deixa isso explícito em vez de inventar página);
  - **títulos e seções**: a hierarquia de títulos, de onde sai o `section_path` e o limite de seção de nível 1 do chunker;
  - **tabelas**: a estrutura de linhas e colunas, para o chunker tratar a tabela como unidade e não cortá-la no meio.
- O parser roda **localmente**, dentro do worker de ingestão; o conteúdo do documento não sai para serviço externo nesta etapa.

## Alternativas consideradas

| Opção | Por que não |
| --- | --- |
| Uma biblioteca por formato (`pypdf`/`pdfplumber` para PDF, `python-docx` para DOCX, parser de Markdown) | três saídas diferentes para normalizar à mão; extração de PDF por texto não reconhece títulos nem a estrutura de tabelas, que teriam de ser inferidas por heurística de fonte e posição |
| PyMuPDF | só cobre PDF (DOCX continuaria precisando de outra biblioteca) e a licença AGPL contamina a distribuição da aplicação |
| Unstructured | cobre os formatos, mas a versão local de qualidade exige um conjunto grande de dependências de sistema, e a de melhor resultado é a API hospedada, que tira o documento do ambiente |
| Conversão para Markdown sem layout (p. ex. MarkItDown) | leve, mas descarta o número de página, que a citação da §7 precisa |
| Extração por LLM multimodal (Gemini lendo o PDF) | custo e latência por página, saída não determinística (quebra o `chunk_id` determinístico de ING-CHK-001) e o conteúdo do usuário passaria pelo modelo antes da verificação de injeção (etapa 4) |

## Consequências

- **O Docling puxa `torch`** e modelos de layout e de estrutura de tabela. A instalação fica pesada (centenas de MB de dependências) e a imagem do worker cresce. O `torch` já entraria pelo reranker local (ADR-0003, via `sentence-transformers`); com a mesma versão resolvida no workspace, o custo é compartilhado, não dobrado.
- Os modelos do Docling são baixados na primeira execução. Ambiente sem rede, CI e imagem do worker precisam dos artefatos pré-baixados, senão a primeira ingestão falha ou fica lenta.
- O parse de PDF é intensivo em CPU (ou GPU, se houver); a ingestão já é assíncrona (§3), então isso pesa no tempo de ingestão, não na latência do chat.
- O `docling` é dependência nova: entra com a aprovação do humano no G1 da mudança `2026-11-24-ingestao` e passa pela carência de 7 dias (ADR-0007), que já resolveu `docling` 2.150.1 com o restante do stack.
- O chunker passa a depender do modelo de documento do Docling. Para não espalhar esse acoplamento, `packages/ingestion` converte a saída do Docling para a proveniência dos contratos (página, `section_path`, offsets) num ponto só; trocar de parser no futuro mexe nesse adaptador e exige nova ADR que substitua esta.
- Mudança de versão do Docling pode alterar a extração e, portanto, os chunks; ela conta como mudança de chunker e passa pela avaliação guiada, como a proposta da ingestão estabelece para alterações de chunker.

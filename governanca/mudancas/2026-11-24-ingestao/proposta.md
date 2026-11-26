---
id: 2026-11-24-ingestao
titulo: Ingestão de documentos
trilha: llm
estado: aprovada
aprovacao: { por: humano, em: 2026-11-24, palavras: "aprovo as duas propostas" }
---

## Por quê

O chat só recupera o que foi ingerido. A spec viva já define a ingestão (`governanca/specs/ingestao/spec.md`, ING-REC-001 a ING-IDX-002), mas nada a implementa.

## O que muda

1. **ADR-0009** (papel `arquiteto`): Docling como parser de documentos, decisão do humano em 2026-11-24.
2. **`packages/lineage`** (pacote novo, dono `rag-backend`): acesso ao Postgres da aplicação com migrations. Nesta mudança entram só as tabelas `document_version` e `chunk`, seguindo os registros de `packages/contracts`. O pacote fica compartilhado, porque o E12 vai gravar a linhagem do chat nele. Exige acrescentar `packages/lineage` ao papel `rag-backend`, em `AGENTS.md` e `governanca/papeis/`.
3. **`packages/ingestion`** (papel `rag-backend`, codex; revisor anthropic), as etapas I1–I6 da arquitetura:
   - receber o arquivo, com idempotência por `sha256` (ING-REC-001);
   - parsear com Docling;
   - dividir em chunks pela estrutura do documento, guardando a proveniência (ING-CHK-001);
   - verificar injeção indireta (ING-INJ-001);
   - gerar o embedding com `gemini-embedding-2` @ 3072, uma requisição por chunk e com o prefixo de tarefa (ING-EMB-001);
   - indexar no Qdrant com o vetor denso e BM25 em português (ING-IDX-001), recusando subir se a chave de embedding divergir da coleção (ING-IDX-002).
4. Formatos na v1: PDF, DOCX e Markdown.

Depende da mudança `2026-11-24-infra-local-observabilidade`, que sobe o Qdrant e o Postgres usados nos testes de integração.

## Fora do escopo

- Recuperação, reranking e chat.
- Tabelas de linhagem do chat (E12).
- Upload pela API ou pela interface web: a ingestão recebe um caminho de arquivo e o dono.

## Riscos

- **Dependências novas** (aprovadas junto com o G1): `docling`, `qdrant-client`, `google-genai`, `psycopg`, `sqlalchemy`, `alembic`. O Docling puxa `torch`, que é pesado. Todos passam pela carência de 7 dias.
- Custo e cota do Gemini: um embedding por chunk e, conforme a decisão abaixo, uma chamada de LLM por chunk na verificação de injeção.

## Esclarecido (humano, G1, 2026-11-24)

- Tamanho dos chunks: alvo de ~800 tokens, teto de ~1500, sobreposição de 1 parágrafo, configuráveis e ajustados depois pelo RAGAS.
- Injeção indireta: heurística determinística em todo chunk e juiz LLM só nos chunks suspeitos.
- Avaliação guiada: nesta mudança, os requisitos ING são provados com testes determinísticos e de integração. A linha de base do RAGAS nasce na mudança da recuperação; dali em diante, toda alteração de chunker ou embedding passa pela avaliação guiada.

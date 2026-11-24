# ADR-0002 — Embeddings `gemini-embedding-2` (3072) e Qdrant com busca híbrida

Estado: **Aceita** (humano, 2026-11-24). Substitui a escolha inicial do ChromaDB.

## Contexto

O requisito é um banco que suporte pelo menos 3000 dimensões e, pela análise da arquitetura, busca híbrida (vetor + BM25) rodando localmente. A Search API do Chroma, que faz busca híbrida com RRF, só existe no Chroma Cloud; a documentação diz que o suporte local "virá numa versão futura".

## Decisão

- Embeddings: **`gemini-embedding-2`**, **3072 dimensões** (padrão e máximo do modelo).
- Banco vetorial: **Qdrant** self-hosted (v1.20.x), com dois vetores nomeados por ponto:
  - `dense`: 3072 dimensões, distância cosseno;
  - `bm25`: esparso, calculado pelo próprio servidor (`qdrant/bm25`, disponível no self-hosted desde a v1.15.3), com **`language: portuguese`**.
- Consulta híbrida: `query_points` com dois `prefetch` e fusão por **RRF**.

## Regras de uso do `gemini-embedding-2`

Verificadas na documentação oficial do Gemini API em 2026-11-24:

1. O modelo não aceita `task_type`; a tarefa vai no texto:
   - documento: `title: {titulo} — {section_path} | text: {chunk}`
   - consulta: `task: question answering | query: {consulta}`
2. Várias entradas numa mesma chamada geram **um único embedding agregado**: uma requisição por chunk, ou Batch API.
3. A chave de embedding (`gemini-embedding-2@3072@fmt-v1`) nomeia a coleção; mudar o formato do prefixo é mudar a chave.

## Alternativas consideradas

| Opção | Por que não |
| --- | --- |
| pgvector | o índice HNSW de `vector` vai até 2.000 dimensões; 3072 exige `halfvec` (até 4.000); a busca textual do Postgres não é BM25 (para isso precisaria do ParadeDB) |
| Chroma local | sem busca híbrida local |
| Weaviate / Milvus | atendem, mas são mais pesados para operar que um container Qdrant |

## Consequências

- O stemming padrão do BM25 do Qdrant é em inglês; a configuração em português é obrigatória e deve ser verificada na criação da coleção.
- O Qdrant guarda vetor + payload de filtro; a linhagem completa fica fora dele (decisão em aberto, arquitetura §11).

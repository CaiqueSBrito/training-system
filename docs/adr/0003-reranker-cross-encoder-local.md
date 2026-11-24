# ADR-0003 — Reranking com cross-encoder local `BAAI/bge-reranker-v2-m3`

Estado: **Aceita** (humano, 2026-11-24)

## Contexto

O reranker só reordena o que recebe: se o chunk certo não está entre os candidatos, não aparece. A referência de produção (Anthropic Contextual Retrieval) traz 150 candidatos e fica com 20 após o reranking. O desenho original deste projeto rerankeava 5 candidatos, o que quase não tem efeito. O conteúdo é em português e a interação é chat síncrono.

## Decisão

- Cross-encoder **local** **`BAAI/bge-reranker-v2-m3`**: multilíngue, licença Apache 2.0.
- Entrada inicial: **30–50 candidatos** vindos da busca híbrida; saída: **5–10**.
- O score do reranker também serve de corte para a checagem de suficiência (E7).

## Alternativas consideradas

- `jina-reranker-v2-base-multilingual`: licença CC-BY-NC, não permite uso comercial.
- Rerankers por API (Vertex AI Ranking, Cohere): descartados por decisão do humano de rodar localmente.

## Consequências

- A latência cresce com o número de candidatos e depende do hardware (CPU × GPU). Os números finais de candidatos e de corte saem da medição ponta a ponta e do RAGAS (arquitetura §11, item 9).
- O modelo (~568M parâmetros) é baixado e versionado como dependência de runtime, não de build.

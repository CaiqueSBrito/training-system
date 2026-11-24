# ADR-0007 — Monorepo Python com uv workspace

Estado: **Aceita** (humano, 2026-11-24)

## Decisão

- Monorepo com **uv workspace**; membros em `packages/*` e `apps/*`.
- **Python 3.13** (`.python-version`). Os limites que restringem a escolha: `google-adk` exige ≥ 3.11, `langfuse` exige < 4.0 e `litellm` exige < 3.16.
- Raiz virtual: a raiz não é um pacote, só coordena os membros e o `uv.lock` único.
- **Carência de 7 dias** (`exclude-newer = "7 days"`): uma versão publicada há menos de 7 dias não é resolvida. Reduz a exposição a versões maliciosas retiradas logo após a publicação, como no caso do litellm (ADR-0006).
- O `uv.lock` é versionado e inclui os hashes dos pacotes.

## Verificação de compatibilidade

Em 2026-11-24, com Python 3.13 e a carência de 7 dias, o stack completo resolveu sem conflito: `google-adk` 2.14.1, `qdrant-client` 1.20.0, `langfuse` 5.2.0, `ragas` 0.4.4, `litellm` 1.109.1, `openinference-instrumentation-google-adk`, `fastapi`, `docling` 2.150.1 e `sentence-transformers` 6.3.0 (para o reranker, que puxa `torch` 2.15.0). Só o `litellm` está instalado até aqui; os demais entram quando cada pacote for criado.

## Consequências

- Dependência nova passa pela aprovação do humano e pela carência.
- Atualizar uma dependência com urgência (correção de segurança) exige sobrescrever a carência explicitamente para aquele pacote, com registro do motivo.

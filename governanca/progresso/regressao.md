# Regressão

Índice dos casos de regressão. O caso em si é executável: teste em `tests/` do pacote dono ou caso de avaliação em `evals/datasets/regressao/`. Casos nunca são apagados para algo passar.

| ID do caso | Requisito | Área | Onde roda | Origem (tarefa / falha) |
| --- | --- | --- | --- | --- |
| REG-001 | RAG-GEN-002 | contratos | `packages/contracts/tests/test_geracao_bruta.py` (item sem `tipo` rejeitado coerentemente com o schema) | T-001, revisão rodada 1, achado 1 |
| REG-002 | RAG-VAL-001, RAG-JUI-003 | contratos | `packages/contracts/tests/test_geracao_bruta.py` (item mal formado validado item a item, sem derrubar o lote) | T-001, revisão rodada 1, achado 3 |
| REG-003 | RAG-ENT-001 | contratos | `packages/contracts/tests/test_resposta_linhagem.py` (recusa em E1 sem intenção, modo ou veredito de entrada) | T-001, revisão rodada 2, ressalva 2 |

# T-018 — Revisão independente Anthropic

- Revisor: anthropic (Claude Opus 5.5), worker Orca `task_64bb76ad5004` / `ctx_6048186daf1c`.
- Produto: OpenAI/Codex, dispatch `ctx_bdc63d216c9e`. O provedor do produto é diferente do revisor.
- Autonomia humana registrada em `tarefas/T-018.md`. Não exijo gates extras.
- Data: 2026-11-24.
- Modo: **só leitura**. Nenhuma suíte, API, download de pesos ou git; nenhum segredo lido; Langfuse desligado; sem commit; produto não editado.
- Lido:
  - `tarefas/T-018.md` e `design.md`; spec viva `governanca/specs/chat/spec.md` (RAG-REC-001 a 004).
  - `packages/retrieval/evidence/{result.md,calibration-separation.json,focal.txt (resumo),focal-correction.txt}` e `packages/retrieval/calibration/bge-v1.json`.
  - `packages/retrieval/src/training_retrieval/{service.py,reranker.py}`, `packages/retrieval/tests/test_retrieval.py` e `packages/ingestion/src/training_ingestion/retrieval/eligibility.py`.
  - Como referência: `indexing.py:90-92` e `core.py:150-185`.
- Fora do escopo:
  - Locks de publicação e concorrência do repositório: são da T-020.
  - T-019, que está ativa em outro contexto.
  - Integração E1–E12, que só existe depois da T-021.

## Veredito

**Aprovado parcialmente.**

Aprovados:
- RAG-REC-001, RAG-REC-003 e RAG-REC-004, nos limites abaixo;
- a barreira compartilhada e a segurança de acesso;
- a calibração inicial do BGE.

**Não aprovado:** RAG-REC-002 (achado 1). A alocação do modo cobertura não é proporcional ao tamanho da subseção.

O achado 2 pede decisão ou registro explícito.

As falhas preservadas continuam válidas como estão registradas:
- a focal original terminou com 12 passed e 5 failed;
- o complemento autorizado teve 5 passed;
- o diff RAGAS do novo serviço segue `Bloqueado`.

Não infiro qualidade de produção da calibração.

## Propriedades conferidas

| Propriedade | Resultado | Base |
| --- | --- | --- |
| Dois prefetches (dense e BM25 português) com fusão RRF e o mesmo filtro | Aprovado | `service.py:170-179` faz uma `query_points` por consulta do plano, com `Prefetch` dense e `Prefetch` bm25 (`bm25_document` usa `language: portuguese`, `indexing.py:90-92`), `FusionQuery(RRF)` e o mesmo `filter_` nos prefetches e no `query_filter`. O teste `test_retrieval.py:136-149` intercepta as chamadas reais ao Qdrant descartável. |
| União deduplicada, 50 candidatos e top 5 | Aprovado | Dedup por `chunk_id` com o maior RRF entre consultas e corte em `candidate_limit` (`service.py:180-185`; limite 30..50 em `:37`). O top N configurável, padrão 5, é aplicado depois do reranker (`:211-214`). |
| Filtros por owner, documentos e seção | Aprovado | Versões resolvidas no Postgres pelo owner (`service.py:98-102`). O prefixo de seção é comparado exatamente sobre os arrays do Postgres (`:115`), e não por pertinência de array no Qdrant. O filtro do Qdrant inclui owner, versões, `embedding_key`, `injection_flagged=False` e a lista de `chunk_id` do escopo (`:135-143`). Owner estrangeiro, versão ausente e prefixo ausente falham fechado (`test_retrieval.py:160-166`). |
| Autorização no Postgres (revisão, hash, ack) antes de qualquer texto sair | Aprovado | `authorize_points` (`eligibility.py:39-92`) exige revisão, evento e hash no payload e passa por `authorize_candidates`. Confere decisão, `input_sha256`, ack da mesma revisão e da mesma `input_id`, `embedding_key`, e texto, seção, versão e owner do payload contra o Postgres. Devolve só o `ChunkRecord` do Postgres. O texto cru do Qdrant fica em `points` e nunca é exposto. Reranker, `RetrievalRunRecord` e contexto só recebem candidatos autorizados (`service.py:197-199,241-245`). Há nova autorização depois da inferência na CPU (`:226-229`). Adulteração de payload e ack ausente são negados nos dois consumidores (`test_retrieval.py:192-211`). A falha do banco é sanitizada (`:214-218`). |
| `SafeRetriever` como consumidor da mesma barreira | Aprovado | `eligibility.py:14-36` usa a mesma `authorize_points`, então não há dois critérios divergentes. |
| Escopo ambíguo falha fechado | Aprovado no código | Prefixo presente em mais de uma versão, heading repetido (a seção do heading inclui o próprio título, `core.py:167-180`) ou trecho não contíguo levantam `ScopeError` (`service.py:116-130`). Ressalva: não há teste de consumidor para esses três casos (achado 3). |
| Continuação sem reset e sem owner errado | Aprovado | O `binding` é o sha256 de owner, versões, prefixo e modo (`service.py:153-159`). Chunks usados fora do escopo resultam em `ContinuationError` (`:163`). Os usados são excluídos antes da busca, e o cursor acumula (`:239`). Owner estrangeiro com o cursor é recusado (`test_retrieval.py:176-181`). O cursor não é autenticado; a T-020 o guarda no servidor (`result.md`). |
| BGE real, revisão fixada, lazy na CPU, sem fallback falso | Aprovado | `reranker.py:10-11,37-56`: `CrossEncoder` com revisão fixada, `device='cpu'`, `max_length=512`, `trust_remote_code=False`, singleton com `lru_cache` e lock, batch 2 e sigmoid explícito. A saída é validada no domínio 0..1, e qualquer falha vira `RetrievalUnavailable` (`service.py:201-207`), sem score alternativo. |
| Calibração 4 positivos / 4 negativos, separada, sem vazamento e sem ajuste depois da avaliação | Aprovado, como calibração inicial | O manifesto é assinado e conferido contra os pares do código, modelo, revisão e domínio (`reranker.py:59-78`). O corte é o ponto médio entre o maior negativo (3.5869e-05) e o menor positivo (0.97085), o que dá exatamente `0.48544316837251245` (`bge-v1.json`). `calibration-separation.json` registra que nenhuma passagem completa aparece literalmente nos documentos de avaliação e que nenhum rótulo de avaliação escolheu o corte. `test_retrieval.py:227-242` reutiliza o artefato existente sem recalibrar e só confere a reprodução da inferência. Limite: os negativos são fora de domínio, fáceis demais, e isso não mede hard negatives nem produção, como o próprio `result.md` declara. |
| Contexto insuficiente sem contexto inventado | Aprovado | Sem nenhum candidato acima do corte, o resultado tem `context=None`, `chunks=[]` e nenhum selecionado (`service.py:211,241-242`; `test_retrieval.py:184-189`). |

## Achados

1. **[Médio, bloqueante para RAG-REC-002] A alocação da cobertura usa o tamanho dos chunks da janela, não o tamanho da subseção.** Arquivo: `service.py:216-237`.
   - Os pesos de `largest_remainder` são `sum(len(c.text))` dos chunks **selecionados na janela** para cada grupo (`:234`).
   - A janela é preenchida em round-robin entre os grupos (`:221-223`), que tende a pegar o mesmo número de chunks de cada subseção. Por isso a alocação tende à igualdade, e não ao 3:1.
   - Exemplo: duas subseções, uma com o triplo do tamanho da outra e chunks de tamanho parecido, `top_n=5`. Os pesos ficam próximos de 3:2 ou 1:1, e com 8 itens a divisão sai perto de 5:3 ou 4:4, em vez dos 6:2 da spec (`spec.md:42-45`).
   - A prova de 6:2 existe só na função pura (`test_retrieval.py:130`). O teste do serviço confere apenas que a soma é 8 (`:173`).
   - Correção sugerida: usar como peso o tamanho total autorizado de cada subseção no escopo (calculado a partir de `rows`/`authorized`), e cobrir com um teste de consumidor 3:1 → 6:2 pelo `RetrievalService`.

2. **[Médio, decisão de spec] O modo cobertura aplica o corte do reranker e descarta chunks autorizados.** Arquivo: `service.py:199-211,239-240`.
   - RAG-REC-002 manda "percorrer os chunks do escopo em ordem de leitura". Aqui, na cobertura, só entram os chunks com score acima do corte para as consultas do plano. Subseções autorizadas com pouca relevância para as consultas geradas nunca entram em nenhuma janela.
   - `remaining` também não conta esses chunks.
   - Pode ser intencional, aplicando o RAG-REC-004 também à cobertura. Se for, isso precisa ficar explícito na spec ou no design. Se não for, o corte deveria servir só para declarar insuficiência na cobertura.
   - Custo associado: cada janela roda o BGE na CPU sobre **todos** os chunks restantes do escopo (`:201-203`).

3. **[Baixo, cobertura de teste] Faltam testes de consumidor para escopo ambíguo e isolamento entre owners nos candidatos.**
   - Não há teste para prefixo presente em duas versões do mesmo owner, heading repetido ou trecho não contíguo (`service.py:116-130`).
   - O teste híbrido confere a versão dos `chunks`, mas não que `run.candidates` contém só chunks do owner. Pelo código isso vale, porque os candidatos vêm de `authorize_points` com o owner.
   - Sugestão: acrescentar esses casos.

## Observações (sem ação obrigatória)

- **Download dos pesos em tempo de execução.** `CrossEncoder` não usa `local_files_only`, então a primeira chamada em produção pode baixar os pesos. Uma falha vira `RetrievalUnavailable` e falha fechado. Vale fixar cache ou pré-download na operação da T-021.
- **Filtro de `chunk_id` com todos os IDs do escopo** (`service.py:142`). Escala com o tamanho do documento por requisição. É só desempenho, não segurança.
- **Ordem de leitura com página nula por último** (`service.py:132-133`). Segue a convenção já existente em `list_chunks`. Em PDFs com spans de página desconhecida, essa ordem pode divergir do offset canônico.
- **Locks.** A autorização é feita numa conexão curta, sem locks mantidos. A revalidação com lock até o commit pertence à T-020 e não é julgada aqui.

## Não executado

- Suítes, focal, inferência BGE, download de pesos e diff RAGAS: **Não executado** por esta revisão (proibido pelo brief). O diff RAGAS do serviço novo continua `Bloqueado` (`result.md`).
- Integração E1–E12: **Não se aplica**; não é declarada antes da T-021.

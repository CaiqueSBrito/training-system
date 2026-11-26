# T-018 — Reavaliação independente Anthropic de RAG-REC-002

- Revisor: anthropic (Claude Opus 5.5), worker Orca `task_b1436dcda11c` / `ctx_9f3ea114e46a`.
- Correção revisada: OpenAI/Codex, dispatch `ctx_fbacca0b2246` / `task_a9a3087c9da1`. O produto foi feito por outro provedor.
- Data: 2026-11-25.
- Modo: **só leitura**. Nenhuma suíte, API, inferência BGE, download de pesos ou git; nenhum segredo lido; Langfuse desligado; sem commit; produto não editado. A integração parcial da T-021 não foi auditada.
- Lido:
  - `packages/retrieval/evidence/rec002-{review-fix.md,manifest.json,red.txt,correction.txt,final-correction.txt,large-section.txt}`;
  - `packages/retrieval/src/training_retrieval/{coverage.py,service.py}`;
  - `packages/retrieval/tests/test_retrieval.py`.
- Recalculei os sha256 de `coverage.py` (`6ed9a373…`), `service.py` (`1d9b8288…`), `test_retrieval.py` (`54dfba32…`), `bge-v1.json` (`f824b7e6…`), `eligibility.py` (`845cfc90…`) e `reranker.py` (`a16cb8e6…`). Todos batem com os manifestos.
- Preservados sem alteração:
  - a revisão R1 (`revisao-anthropic.md`, **Aprovado parcialmente**, RAG-REC-002 **não aprovado**);
  - as falhas históricas (focal 12/5, `rec002-red.txt` 1 failed);
  - o diff RAGAS **Bloqueado**.

## Veredito

**RAG-REC-002: Aprovado**, nos limites abaixo. Os achados 1 e 2 da R1 estão resolvidos, e o achado 3 está resolvido em parte.

RAG-REC-001, 003 e 004 continuam **aprovados**. A barreira e a calibração não mudaram (hashes iguais), e não houve ajuste de score, corte ou modelo.

A T-018 fica aprovada como slice. Isso não declara a integração E1–E12 (T-021) nem a qualidade RAGAS.

## Reavaliação dos achados R1

| # | Achado R1 | Estado | Base |
| --- | --- | --- | --- |
| 1 | A alocação usava o tamanho dos chunks da janela, não o da subseção | **Resolvido** | Ver detalhamento abaixo da tabela. |
| 2 | Corte do reranker aplicado na cobertura | **Resolvido**, por decisão explícita do coordenador (`msg_c5b6412c04cc`) | A cobertura não chama o BGE, `cutoff=None` e `rerank_score=None` (`service.py:216-232,274-276`). Todas as subseções autorizadas continuam elegíveis. Os testes fazem o reranker levantar erro se for chamado (`test_retrieval.py:291-293,334-336`). A consulta mantém top N e corte inalterados (`:221-235`). |
| 3 | Faltavam testes de escopo e owner | **Parcial** | Ver detalhamento abaixo da tabela. |

**Achado 1.**
- `coverage.canonical_sections` (`coverage.py:33-79`) mede as interseções exatas entre o mapeamento `exact` de cada chunk autorizado e os spans canônicos.
- Exige que o mapeamento coincida com os offsets do chunk (`:59-61`) e que a identidade canônica e de versão confira (`:64-66`).
- Agrupa por versão, receita (`canonical_id`), ocorrência H1 e caminho completo (`:71`). Ignora separadores (`:68`). Une os intervalos (`:23-30`), então cada codepoint conta uma vez apesar do overlap.
- Lê só metadados, com join filtrado por owner e versões (`:46-53`).
- A alocação (`service.py:245-261`) usa o `canonical_size` **global** de todas as subseções que ainda têm chunks disponíveis, **antes** de montar a janela.
- Subseções com cota 0 aparecem explícitas, com `count=0`.
- Provas:
  - `test_retrieval.py:289-326`: 300:100 → 6:2 pelo serviço, com overlap real e um chunk atravessando a fronteira (o red `rec002-red.txt` reproduziu 5:3 antes da correção);
  - `:329-348`: grande 900, fora dos 5 primeiros chunks, recebe 6 de 8, e as 7 subseções aparecem.

**Achado 3.**
- Cobertos:
  - owner estrangeiro com o cursor é recusado (`:325-326`, `:180-181`);
  - o prefixo que cruza para um chunk misto é encontrado pelo span (`:320-324`);
  - os candidatos do run pertencem ao conjunto do owner (`:313-314`).
- Ainda sem teste de consumidor: prefixo presente em duas versões e heading repetido ou não contíguo (`service.py:130-144`, inalterado e correto pela leitura).
- Não é bloqueante.

## Conferências pedidas

- **Prefixo, spans e chunk misto.**
  - Na cobertura com prefixo, os chunks entram por sobreposição com spans do prefixo (`service.py:124-127`), e só a parte dentro do prefixo conta no tamanho (`coverage.py:68-70`).
  - Um chunk misto aparece nas duas alocações, com as partes canônicas de cada uma, e entra no contexto uma única vez (`service.py:251-261`).
  - As checagens de ambiguidade (`:130-144`) continuam valendo.
- **Ordem.**
  - Dentro de cada subseção, a escolha segue a ordem de leitura (`coverage.py:57`).
  - A janela final é ordenada como `rows` (`service.py:257`), e o teste confere `start_offset` crescente em todas as páginas (`test_retrieval.py:320`).
- **Cursor.**
  - O binding v1 não mudou (`service.py:167-177`).
  - Os chunks usados acumulam (`:270`) e nunca são escolhidos de novo (`remaining_ids`, `:247`).
  - O tamanho canônico é estável entre páginas, porque é medido sobre o escopo inteiro autorizado e revalidado (`:239-246`).
  - O laço do teste consome tudo sem repetir (`test_retrieval.py:316-322`).
- **Owner.**
  - Versões e chunks resolvidos pelo owner no Postgres.
  - A barreira `authorize_points` não foi alterada e é aplicada de novo sobre todo o escopo antes da medição (`service.py:239-246`).
  - A medição filtra por owner.
- **Conteúdo ausente ou revogado não vaza.**
  - Chunks não autorizados não entram nos pesos, nas alocações nem no contexto. `canonical_sections` só recebe `refreshed`.
  - Nenhum `section_path` ou tamanho de subseção vem de conteúdo não autorizado, porque os grupos nascem só de chunks autorizados.
  - Um mapeamento não `exact` falha fechado (`EligibilityUnavailable`), sem estimar tamanho.
- **Nenhum ajuste de score para aprovar.**
  - A calibração, o reranker e a barreira têm hashes idênticos aos aprovados.
  - O caminho da consulta foi reestruturado (`service.py:213-243,262-268`), mas pela leitura é equivalente ao aprovado na R1:
    - autorizar;
    - BGE;
    - corte `> cutoff`;
    - top N;
    - revalidar os selecionados;
    - alocação legada só na consulta.

## Limites e achados não bloqueantes

1. **[Baixo/Médio] O contexto da cobertura com prefixo pode trazer texto de fora da seção pedida.** Arquivos: `service.py:124-127,257,272-273`.
   - O contexto entrega o texto inteiro do chunk misto.
   - Exemplo: com prefixo `Pequena`, o contexto inclui o chunk que cruza a fronteira, com texto de `Grande`. O próprio teste confirma isso (`test_retrieval.py:324`).
   - O owner e a autorização estão corretos, mas o gerador pode produzir itens sobre conteúdo fora da seção pedida.
   - Recomendação para a T-021: restringir a geração ou as citações às interseções canônicas da `Allocation` (`canonical_id`/spans), ou recusar citações fora do prefixo.

2. **[Baixo] Subseção pai só com o título também recebe cota.** Arquivo: `coverage.py:67-71`.
   - Spans `heading` contam como conteúdo, e o heading de um H1 tem o caminho `(H1,)`. Num documento real do Docling, o H1 sem texto próprio vira uma "subseção" do tamanho do título e pode receber cota.
   - O peso é pequeno e o chunk é compartilhado com a primeira subseção.
   - A fixture usa só parágrafos, então esse caso não é exercitado.
   - Sugestão: excluir da alocação os grupos que só têm heading, ou cobrir isso com um teste usando headings reais.

3. **[Baixo] Subseção parcialmente esgotada mantém a cota inteira.** Arquivos: `service.py:254-256,259`.
   - A cota usa o tamanho global, mesmo quando sobra só 1 chunk na subseção. `Allocation.count` pode, por exemplo, ser 6 com 1 fonte.
   - A soma das cotas continua igual a `count`, e não há repetição de fonte. Fica para a T-021 decidir se pede menos itens ou aceita vários itens da mesma fonte.

4. **[Observação] Tabelas pesam pelo markdown exportado.** O tamanho dos spans `table` (`export_to_markdown`) inclui pipes e traços, o que pode inflar o peso das tabelas. Isso não afeta o cenário da spec.

5. **[Observação] A consulta não foi reexecutada depois da mudança.**
   - As saídas focais da correção (`rec002-final-correction.txt` às 00:05:30, depois do último `service.py` às 00:05:24; `rec002-large-section.txt` às 00:06:36, depois do teste às 00:06:31) são em modo `-q`, sem os IDs dos nós.
   - Os testes de consulta (híbrido, adulteração da barreira, BGE real) não foram reexecutados depois da reestruturação do `service.py`.
   - A preservação de REC-001/003/004 se apoia na equivalência por leitura e nos hashes inalterados. Uma prova integrada futura deve incluí-los.

## Não executado

- Suítes, focais, inferência e download do BGE, e diff RAGAS: **Não executado** por esta revisão (proibido pelo brief). O RAGAS continua **Bloqueado**.
- Integração E1–E12 da T-021: **Não se aplica**; não auditada nem declarada.

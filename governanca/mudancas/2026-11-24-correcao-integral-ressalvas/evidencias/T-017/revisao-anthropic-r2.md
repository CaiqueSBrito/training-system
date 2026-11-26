# T-017 — Revisão independente Anthropic, rodada 2

- Revisor: anthropic (Claude Opus 5.5), worker Orca `task_b58a3f547097` / `ctx_77f20da5ef74`.
- Autor revisado: sessão principal OpenAI.
- Data: 2026-11-24.
- Modo: **só leitura**. Nenhuma suíte, API real, Postgres ou Qdrant executados; sem git, segredos, Langfuse ou commit; validador fica com o principal.
- Entradas: R1 desta revisão (`revisao-anthropic.md`); arquivos finais `packages/lineage/tests/test_lineage.py`, `packages/ingestion/tests/test_indexing.py`, `packages/ingestion/tests/test_ingestion.py`; `manifesto-integracao.json`, `resultado.md`, `pytest-integrada-r2.txt`, `pytest-cobertura-final.txt`, `pytest-focal-fixtures.txt`, `juiz-real-integrada-r2.json`; `matriz.md` e `tarefas/T-017.md` (escopo).

## Veredito

**Aprovado** o código de teste e a documentação da T-017. Os cinco achados da R1 estão resolvidos, e não abro achado novo que bloqueie.

**Não aprovado** o resultado integrado, que continua **Falhou**. A R2 teve 602 passaram, 1 falhou e 2 desmarcados: `negative-10` ficou `Bloqueado` por HTTP 503 do provedor, com uma tentativa. Esta revisão não converte esse resultado em `Passou`. EVAL-INT-001 global segue pendente, e a causa histórica sem traceback segue `Bloqueado` por decisão humana.

## Reavaliação dos achados da R1

| # | Achado R1 | Estado | Base |
| --- | --- | --- | --- |
| 1 | Imutabilidade sem prova do trigger de TRUNCATE no head | **Resolvido** | `test_lineage.py:98-114`. Cada caso confere no catálogo, no head, que `chunk_immutable_truncate` em `chunk` está habilitado (`tgenabled='O'`) e aponta para `reject_chunk_mutation`. O novo caso `TRUNCATE chunk CASCADE` exige `23514`, o `TRUNCATE` simples exige a recusa de FK `0A000`, e os dados ficam intactos (`get_chunk == record()`). A ligação no catálogo mais o SQLSTATE é uma prova mais forte do que comparar o texto da mensagem, e não depende de como ela está redigida. No CASCADE, os triggers de TRUNCATE das tabelas de histórico também recusam com `23514`, então o SQLSTATE sozinho não identifica qual trigger disparou. É a verificação no catálogo que fecha essa lacuna. |
| 2 | Adaptação de testes G3 antigos e hash não registrados | **Resolvido** | `tarefas/T-017.md:41-45` lista os seis arquivos de teste, cita a autorização e exclui os três casos T-001. `resultado.md` dá o motivo de cada arquivo. O manifesto registra `evals/tests/test_ressalvas_runner.py` `7dbfd608…` e os três T-001 (`b15e416b…`, `1623c0e1…`, `c897e706…`). Recalculei os sha256 dos 57 fontes e dos 5 artefatos do manifesto, e todos batem com o disco atual. |
| 3 | `test_ressalvas_integracao.py` e manifesto C11 ausentes | **Resolvido** | `matriz.md:17,24` e `T-017.md:43` substituem o arquivo planejado pela suíte real com manifesto, sem criar teste artificial. O manifesto traz `run_id` `integrada-local-r2-MCUpx8`, comando, versões, migrations 0001–0003, 603 IDs de nó, rodadas, exclusão de Langfuse com motivo e vínculo à calibração e à baseline. A linha 11 da matriz está como **Falhou**, o que é coerente. |
| 4 | `pytest.raises(Exception)` amplo | **Resolvido** | `test_indexing.py:193-195` agora exige `IngestionStageError` com `category == 'storage'`, e continua conferindo `authorize_candidates(...) == {}`. |
| 5 | Fixtures regeneradas dentro do repositório | **Resolvido** | `test_ingestion.py:272-281` copia `generate.py` para `tmp_path` (o script escreve em `Path(__file__).parent`, `generate.py:5`), compara a saída com os bytes originais e confirma que as originais ficaram intactas. As fixtures do repositório têm mtime 23:20, da R2 com o código anterior, e nada as alterou depois. |

## Conferência do resultado R2

- **A falha é real e está registrada direito.** `pytest-integrada-r2.txt` termina com `1 failed, 602 passed, 2 deselected … 507.36s`.
- **O artefato do juiz confirma o caso.** `juiz-real-integrada-r2.json` registra 20 casos (19 `Passou`, 0 `Falhou`, 1 `Bloqueado`), todos com `attempts == 1`. `negative-10` tem `error_kind=provider`, `status_code=503`, `retryable=true` e nenhuma decisão ou consumo. Os 9 ataques têm `actual_injection=True`, `allowed=False` e não aparecem no consumidor. Os 10 controles restantes foram liberados e consumidos.
- **Não houve retry nem troca de modelo ou prompt.** O `judge_spec` corresponde ao juiz configurado, com prompt `indirect-injection-approved-v3`.
- **A cronologia está declarada.** A R2 rodou antes das três correções de teste. As asserções novas foram provadas só na focal de 7, e o manifesto diz isso explicitamente (`notes[0]`), sem alegar uma nova suíte completa. Isso é aceitável porque as correções só tornam as asserções mais rígidas, sem mudar produto ou fixture de protocolo.

## Observações (sem ação obrigatória)

- **Saída da focal de 7 sem IDs de nó.** `pytest-cobertura-final.txt` só traz `7 passed in 2.75s` (modo `-q`), sem o comando nem os IDs. A soma bate com 5 parametrizações de `test_lineage` + 1 de `test_indexing` + 1 de `test_ingestion`, e o manifesto descreve o escopo, mas a saída em si não prova quais nós rodaram. Em rodadas futuras, gravar com `-v` ou registrar o comando.
- **Hashes só do estado final.** O manifesto guarda os hashes depois das correções (`source_hashes_after_targeted_coverage`), não os da hora da R2, nos três arquivos alterados depois dela. A cronologia está declarada. Fica como limite de rastreabilidade, não como inconsistência.
- **`tools/` fora do comando integrado.** O comando cobre `packages/*` e `evals`, mas não `tools/governanca/tests` (o validador de governança), que está em `testpaths`. Isso não afeta os requisitos de produto da T-017. O principal roda o validador à parte.
- **Linha 9 da matriz (SEC-ATQ-001) como `Passou`.** Os nove ataques passaram na R2 com juiz real, e T-016 registrou 20/20. O controle bloqueado por 503 é um negativo, não um ataque, então a linha é coerente. Isso não muda o estado `Falhou` da linha 11.

## Não executado

- Suíte, focal, APIs, serviços e validador: **Não executado** por esta revisão (proibido pelo brief; o validador fica com o principal).
- Resultado integrado verde: **Não se aplica**, porque não existe. A R2 terminou Falhou por indisponibilidade do provedor.

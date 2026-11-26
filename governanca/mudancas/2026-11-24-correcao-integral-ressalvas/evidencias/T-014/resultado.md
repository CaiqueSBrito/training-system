# T014 — Resultado da slice

## Corrigido e provado

- **ING-REP-001 — Passou.** `Reconciler.reconcile_version(owner_id, version_id)` cria revisão monotônica e fencing persistidos, reproduz o input exato do embedding, repara/regrava pontos com dono/versão/chave/evento/revisão/hash, verifica lotes inteiros de 128 e só publica `ready` após confirmação durável. Reexecução reutiliza IDs sem duplicar pontos e ponto ausente é recriado.
- **ING-LEG-001 — Passou.** Origem antiga sem input/contexto recebe proveniência `insufficient` e decisão `legacy_insufficient`, reporta `blocked` e não ganha embedding inventado. Erro do juiz grava estado `unavailable` sanitizado e a leitura pública retorna vazio.
- **ING-OPS-001 — Passou.** Ponto escrito sem ack permanece indisponível e nova rodada recupera; worker antigo após lease expirada não confirma/publica. Inventário permanece dry-run; exclusão de fixture requer manifesto/digest/run/coleção/banco, idade ≤ 5 min, igualdade do inventário e callbacks explícitos de quiescência/autorização, e opera apenas pontos com payload igual ao manifesto.
- **ING-AUD-001 — Passou.** A rota mínima `training_ingestion.retrieval.eligibility.SafeRetriever.search` usa Qdrant só como fonte de candidatos e valida no PostgreSQL dono, estado ready, decisão atual, revisão, evento e hash antes de devolver registros; indisponibilidade do banco falha fechado. `blocked → allowed` só fica visível depois da reconciliação, sem alterar bytes/IDs de documento e chunks.

## Saídas de verificação

Prova inicial: `uv run pytest packages/ingestion/tests/test_ressalvas_reconciliacao.py packages/ingestion/tests/test_ingestion.py` — **31 passaram, 3 falharam**; as falhas eram um ID de reserva incorreto no assert, resultado `legacy_insufficient` sem relatório estruturado e a contagem da fixture (256 parágrafos mais título produzem 257 chunks). Os três casos foram corrigidos. Prova complementar (`pytest-complemento.txt`): **4 passaram** (3 casos corrigidos e um caso de bloqueio/liberação após a última alteração de remoção em lote); os demais 31 casos da execução focal inicial passaram. A suíte integral não foi repetida.

`pytest.txt` e `pytest-complemento.txt` preservam saídas brutas completas. O caso C16 confirmou lotes 128/128/1, escopo de owner e ausência de `allow_test_collection`. Foram usados Postgres e containers Qdrant descartáveis; dublês de juiz/embedding ficam apenas nos testes. Nenhuma API externa ou segredo foi usado; Langfuse não foi iniciado.

## Limitações e pendências

- Exclusão de órfãos foi exercitada somente com fixture Qdrant descartável; nenhum ponto compartilhado/humano foi inventariado para exclusão ou apagado. O mecanismo não autoriza varrer/excluir dados reais.
- Busca implementa apenas seleção mínima de candidatos densos e gate de elegibilidade; ranking adicional, contexto de resposta e geração não fazem parte de T014.
- Revisão independente Google, integração da slice, pré-requisitos RAGAS e G4 humano permanecem pendentes ou bloqueados pelos gates correspondentes. O veredito histórico T013 foi preservado; esta evidência acrescenta cobertura de ING-LEG e cutover para revisão futura, sem retroativamente declarar aprovação T013.
- Não houve chamadas externas reais porque a rotação da chave Gemini não foi confirmada. Sem commit, alteração de dependências/lock, deploy, DDL em banco compartilhado ou religamento de Langfuse.

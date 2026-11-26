# Matriz das ressalvas — execução e evidências

Fontes abreviadas por pacote (`packages/<pacote>/src/training_<pacote>/`); relatórios e revisões em `evidencias/T-NNN/`. Esta matriz registra o resultado observado, não apenas o plano.

| # | Ressalva | Requisito | Caso | Tarefa | Implementação/prova | Evidência | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Migration intermediária | ING-MIG-001 | C01 | T-011, T-016 | lineage/migration.py | T-011/revisao-google.md; T-016/resultado.md | Passou |
| 2 | CHECKs/triggers/downgrade | ING-MIG-002 | C02 | T-011, T-016 | lineage/tests/test_lineage.py e test_ressalvas_persistencia.py | T-017/pytest-cobertura-final.txt; T-011/revisao-google.md | Passou |
| 3 | Citação entre páginas | ING-PRO-001 | C03 | T-010–T-013 | contracts/ingestao.py; lineage/repository.py; ingestion/core.py | T-011 e T-012/revisao-google.md | Passou |
| 4 | Canônico/offset/conteúdo | ING-CAN-001 | C04 | T-010–T-012 | contracts/ingestao.py; lineage/tables.py | T-010 e T-011/revisao-google.md | Passou |
| 5 | H1/H2/H3 e spans | ING-EST-001 | C05 | T-012 | ingestion/core.py | T-012/revisao-google.md | Passou |
| 6 | URL/base64/CJK Unicode | ING-UNI-001 | C06 | T-012 | ingestion/core.py | T-012/revisao-google.md | Passou |
| 7 | Transação curta e concorrência | ING-TXN-001 | C07 | T-013 | ingestion/protocol.py | T-013/reavaliacao-r2-google.md | Passou |
| 8 | Docling reutilizado seguro | ING-PAR-001 | C08 | T-012 | ingestion/core.py | T-012/revisao-google.md | Passou |
| 9 | Nove R3_ATTACKS reais | SEC-ATQ-001 | C09 | T-015–T-017 | ingestion/tests/test_indexing.py | T-016/juiz-real-ressalvas.json (20/20); T-017/juiz-real-integrada-r2.json (9 ataques passam, 1 controle bloqueado por HTTP 503) | Passou |
| 10 | Diagnóstico sanitizado e causa antiga | OBS-DIA-001 | C10 | T-016 | llm/__init__.py; llm/tests/test_models.py | Diagnóstico futuro passou; causa histórica sem traceback mantida bloqueada pelo humano | Bloqueado |
| 11 | Prova integrada final | EVAL-INT-001 | C11 | T-015, T-017 | Suíte existente + manifesto, sem teste artificial de wiring | T-017/manifesto-integracao.json; pytest-integrada-r2.txt: 602 passam, 1 falha por HTTP 503; baseline RAGAS v1.0.1 passou | Falhou |
| 12 | Trilha inclusive liberados | ING-AUD-001 | C12 | T-010, T-011, T-013 | lineage/tables.py; ingestion/protocol.py | T-013/reavaliacao-r2-google.md | Passou |
| 13 | Reconciliar Qdrant pela linhagem | ING-REP-001 | C13 | T-014 | ingestion/reconciliation.py; ingestion/retrieval/eligibility.py | T-014/revisao-google.md | Passou |
| 14 | Reclassificação e órfãos | ING-LEG-001 | C14 | T-011, T-013, T-014 | lineage/repository.py; ingestion/reconciliation.py | T-014/revisao-google.md e revisao-seguranca-llm-google.md | Passou |
| 15 | DEBUG LiteLLM bloqueado | SEC-LOG-001 | C15 | T-016 | llm/__init__.py; llm/tests/test_models.py | T-016/resultado.md e revisão da slice local | Passou |
| 16 | Sem gancho, índices e lotes | ING-OPS-001 | C16 | T-014, T-016 | ingestion/indexing.py; ingestion/tests/test_indexing.py | T-014/revisao-google.md; T-016/resultado.md | Passou |

A execução integrada é a suíte real existente com manifesto e IDs, não um arquivo novo `test_ressalvas_integracao.py` sem comportamento adicional. A referência planejada foi substituída sob autorização autônoma humana para evitar teste redundante de wiring. Dois testes de Langfuse foram desmarcados por ordem de manter o serviço desligado; instrumentação ADK local foi incluída.

A baseline RAGAS inicial foi autorizada pelo humano, sem comparação histórica fictícia. Calibração `ragas-calib-0fd37cd1aae7` e baseline `ragas-run-b647f4ac987c` passaram no dataset 1.0.1; geração não está no escopo avaliado. O HTTP 503 da execução integrada é evidência atual e não explica a falha antiga sem traceback.

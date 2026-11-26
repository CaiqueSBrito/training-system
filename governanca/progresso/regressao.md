# Regressão

Índice dos casos de regressão. O caso em si é executável: teste em `tests/` do pacote dono ou caso de avaliação em `evals/datasets/regressao/`. Casos nunca são apagados para algo passar.

| ID do caso | Requisito | Área | Onde roda | Origem (tarefa / falha) |
| --- | --- | --- | --- | --- |
| REG-001 | RAG-GEN-002 | contratos | `packages/contracts/tests/test_geracao_bruta.py` (item sem `tipo` rejeitado coerentemente com o schema) | T-001, revisão rodada 1, achado 1 |
| REG-002 | RAG-VAL-001, RAG-JUI-003 | contratos | `packages/contracts/tests/test_geracao_bruta.py` (item mal formado validado item a item, sem derrubar o lote) | T-001, revisão rodada 1, achado 3 |
| REG-003 | RAG-ENT-001 | contratos | `packages/contracts/tests/test_resposta_linhagem.py` (recusa em E1 sem intenção, modo ou veredito de entrada) | T-001, revisão rodada 2, ressalva 2 |
| REG-004 | ING-INJ-001, SEC-ATQ-001 | ingestão | `packages/ingestion/tests/test_indexing.py` (nove contornos alcançam juiz; conteúdo jurídico/técnico legítimo pode ser liberado e consumido; bloqueados não são recuperados) | T-008, revisões r2/r3; T-016 corpus real |
| REG-005 | ING-PRO-001, ING-CAN-001 | proveniência | `packages/contracts/tests/test_ressalvas_contratos.py`, `packages/lineage/tests/test_ressalvas_persistencia.py` (hash, fatia exata, seleção da página correta e proveniência desconhecida sem página inventada) | T-007, página inicial incorreta; T-010/T-011 |
| REG-006 | ING-EST-001, ING-UNI-001 | chunking | `packages/ingestion/tests/test_ressalvas_parse.py` (H1 por ocorrência, H2/H3 sem corte obrigatório, Unicode sem espaços e reconstrução por intervalos) | T-007, chunks pequenos/contidos e recusa de palavra longa; T-012 |
| REG-007 | ING-TXN-001, ING-AUD-001 | protocolo | `packages/ingestion/tests/test_ressalvas_protocolo.py` (operações lentas fora de BEGIN, fencing de worker vencido, retomada após falha e liberação auditada) | T-007/T-008, transação longa e liberação sem rastro; T-013 |
| REG-008 | ING-REP-001, ING-LEG-001 | reconciliação | `packages/ingestion/tests/test_ressalvas_reconciliacao.py` (revisão obsoleta não autoriza candidato, reclassificação preserva história e manifesto impede apagar órfão desconhecido) | T-008, índice incompleto e exclusão permanente; T-014 |
| REG-009 | SEC-LOG-001, OBS-DIA-001 | segurança | `packages/llm/tests/test_models.py` (DEBUG/callback inseguro recusado, configuração real do juiz identificada, exceções sanitizadas e cache interno entre chamadas sem bypass de dados) | T-008, logging; incidente de credencial; T-016 |
| REG-010 | EVAL-INT-001 | avaliação | `evals/tests/test_ressalvas_runner.py` (cobertura determinística reprova, diff reprovado altera exit, scores inválidos rejeitados e recursos descartáveis limpos em falha parcial) | T-015, revisão OpenAI R1 |
| REG-011 | EVAL-INT-001 | calibração | `evals/tests/test_ressalvas_runner.py` (controles negativos sem pista, mesmos bytes do contexto e manifesto vinculado a limiares/receita/versões) | T-015, revisão OpenAI R2 |
| REG-012 | EVAL-INT-001 | dataset | `evals/tests/test_ressalvas_runner.py` (evidência esperada deve existir literalmente na fonte antes de gastar chamadas reais) | T-015, calibração v1.0.0 falhou por transcrição de paráfrase |
| REG-013 | ING-MIG-002 | persistência | `packages/lineage/tests/test_lineage.py` (trigger habilitado correto no head, TRUNCATE CASCADE recusado e dados intactos) | T-017, revisão Anthropic achado 1 |
| REG-014 | ING-FMT-001 | isolamento | `packages/ingestion/tests/test_ingestion.py` (gerador reproduz fixtures em tmp_path sem sobrescrever as fontes) | T-017, revisão Anthropic achado 5 |

Falha histórica r2/run3 sem traceback: causa **Bloqueado**, sem atribuição retroativa. HTTP 429 e HTTP 503 observados nas integrações T-017 têm categoria/status sanitizados nos artefatos próprios e continuam falhando a prova real, sem retry ou resultado fictício.

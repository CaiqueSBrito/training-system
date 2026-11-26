# T-017 — integração local e bloqueios restantes

## Resultado observado

| Execução | Passaram | Falharam | Desmarcados | Resultado |
| --- | --- | --- | --- | --- |
| Integrada R1 | 597 | 6 | 2 | Falhou |
| Integrada R2 após corrigir fixtures | 602 | 1 | 2 | Falhou — provedor HTTP 503 em negative-10 |
| Focal das três fixtures corrigidas | 3 | 0 | 0 | Passou |
| Cobertura adicional após revisão | 7 | 0 | 0 | Passou |

**Não há suíte integrada verde.** O único caso reprovado na R2 foi bloqueado por indisponibilidade do provedor (HTTP 503, retryable=true, attempts=1), não por classificação divergente. Nove ataques e dez controles passaram nessa rodada; a focal anterior de T-016 registrou 20/20. Isso não transforma o resultado integrado Falhou em Passou. Não houve retry, mudança de modelo/prompt/limiar, descarte do caso ou reexecução do corpus até obter verde.

R1 mostrou cinco casos HTTP 429 no mesmo nó do corpus e cinco outros nós reprovados por divergências de contrato/fixture/ambiente. A R2 usou admissão conservadora de uma chamada Gemini a cada seis segundos nos testes opt-in; a aplicação não foi alterada e isso não garante quota ou disponibilidade externa. O HTTP 503 atual não prova a causa da falha histórica r2/run3 sem traceback; esta continua **Bloqueado**, conforme decisão explícita do humano.

## Manifesto e escopo C11

`manifesto-integracao.json` registra run_id, comandos, versões, hashes de código e dos artefatos, revisões 0001–0003 testadas, IDs dos nós da primeira coleta verbosa, resultados das rodadas, vínculo à calibração e à baseline e as limitações da cobertura adicional. Asserções fortalecidas depois da R2 foram verificadas somente na focal de sete, sem alegar uma terceira execução completa. Os dois testes de Langfuse foram desmarcados pelo filtro explícito, pois o serviço permanece desligado por ordem humana. A instrumentação ADK local foi incluída.

O arquivo planejado `test_ressalvas_integracao.py` foi substituído pela execução conjunta dos testes reais existentes, não por novo teste de wiring redundante. O escopo equivalente foi registrado na matriz e na tarefa sob autorização humana contínua de execução autônoma.

## Correções de testes e autorização

A ordem humana de corrigir todas as ressalvas e a autonomia posterior autorizam as adaptações. G3 de T-003–T-008 e T-010–T-017 foi dispensado; estes arquivos não são os três casos congelados da T-001, que permaneceram intactos.

- `packages/lineage/tests/test_lineage.py`: nome real `index_ack`; invariantes por SQLSTATE e dados intactos, registro habilitado da função correta no catálogo e exercício adicional de `TRUNCATE chunk CASCADE` no head. Não se depende apenas da mensagem da exceção.
- `packages/ingestion/tests/test_indexing.py`: parser de teste com versão e IDs de span válidos por documento; metadados do juiz no dublê externo; falha após escrita restringida a `IngestionStageError`/categoria storage e confirmação de candidatos não autorizados; corpus real grava só em tmp_path, sem sobrescrever evidências históricas.
- `packages/llm/tests/test_models.py` e `conftest.py`: orçamento opt-in de chamadas reais, sem retry ou respostas sintéticas; verificação de embedding real continua incluindo dimensão/prefixo e chamadas reais.
- `evals/tests/test_ressalvas_runner.py`: dublê externo de embedding com relevância determinística para o teste local, explicitamente não evidência de qualidade do embedder; ambiente do CLI isolado das variáveis vazias do host. SHA-256 atual `7dbfd6085bdc9624d72b6d64577730693149f044cdc3dedd21550e033c64b427`, registrado também no manifesto.
- `packages/ingestion/tests/test_ingestion.py`: gerador de fixtures executado em tmp_path e comparado às fontes, sem sobrescrevê-las antes de verificar reprodução.

Nenhum arquivo de aplicação, modelo, prompt ou limiar foi alterado nesta etapa final. As mudanças posteriores à revisão inicial de Anthropic foram provadas na focal e enviadas para reavaliação independente.

## RAGAS e evidência por requisito

Calibração real `ragas-calib-0fd37cd1aae7` e baseline real `ragas-run-b647f4ac987c` passaram com dataset 1.0.1, precisão de contexto aproximadamente 1.00, recall 1.00 e cobertura determinística 1.00. Evidências em T-015; somente ingestão e consulta segura, geração/faithfulness Não se aplica com motivo. Primeira baseline autorizada, sem comparação histórica fictícia.

Requisitos de migrations, canônico/proveniência, chunking Unicode, Docling, protocolo/fencing, auditoria, reconciliação, reclassificação, logging e operações têm provas locais e revisões anteriores, além dos testes aprovados nesta integração. `OBS-DIA-001` passou para diagnóstico futuro; a causa histórica permanece sem prova. `SEC-ATQ-001` teve os nove ataques bloqueados pelo juiz real. `EVAL-INT-001` não recebe aprovação global: a parte RAGAS passou, mas a suíte integrada terminou Falhou por HTTP 503 e mantém o gate pendente.

Estado: **bloqueada para conclusão integral**, sem aceitar falha nem inventar causa histórica. Não repetir suítes caras ou enfraquecer testes para contornar indisponibilidade externa. A revisão de código não equivale a um resultado de execução aprovado.

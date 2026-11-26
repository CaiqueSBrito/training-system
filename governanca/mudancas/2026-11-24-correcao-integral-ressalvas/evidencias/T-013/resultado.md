# T-013 — resultado da prova focal

## Status por requisito

- **ING-TXN-001 — Passou (prova focal delimitada).** C07: oito testes ao todo; cobertura de parse/juiz/embedding/Qdrant sem transação aberta, concorrência, isolamento por dono/hash, fencing após lease expirada, recuperação após parse persistido e convergência após upsert não reconhecido. A API pública agora executa I1–I6 com reserva/fencing, jobs duráveis e publicação somente após verificação e ack. Uma chamada que exceda a lease não publica; não há heartbeat em thread nem retry automático.
- **ING-AUD-001 — Passou (prova focal delimitada).** C12: triagem persistida antes do juiz, cadeias auditáveis para liberação/bloqueio/erro, metadados com provider/model/config/prompt hashes, decisão indisponível em erro e texto sentinela ausente. O teste LiteLLM confere os metadados contra configuração e prompt enviados por dublê, sem API.
- **ING-LEG-001 — Bloqueado.** C14 comprovou append-only e revogação de chunk liberado→bloqueado sem alterar `document_version`/`chunk`; falta caso real consumer-visible de origem antiga sem embedding input e teste dos sentidos bloqueado→liberado/falha na reclassificação. O protocolo fecha acesso por revisão/ack obsoletos e cria decisão `legacy_insufficient` sem inventar título ou fonte, mas este ramo não foi provado nesta execução.

## Comandos e saídas

`uv run pytest packages/ingestion/tests/test_ressalvas_protocolo.py -q` — **Passou**, `8 passed in 6.22s`; saída integral em `pytest.txt`.

AST com `uv run python` — **Passou**, cinco arquivos parseados; LSP de Python indisponível. O principal autorizou AST como fallback para a interface exportada.

Primeira tentativa focal antes de corrigir: **Falhou**, 4 falhas e 4 aprovações. O erro externo foi sanitizado em `storage`; inspeção do contrato real do trigger `t011_audit_insert` identificou e corrigiu divergência: juiz/decisão precisam repetir exatamente os `heuristic_rule_ids` da triagem. Uma única execução focal posterior passou, registrada acima; não houve loop de suítes.

## Limites e pendências

- Nenhuma API Gemini/LiteLLM real foi chamada; principal ainda não confirmou rotação da chave. Sem ler ou imprimir `.env`.
- Langfuse permaneceu desligado. RAGAS segue **Bloqueado** por pré-requisitos/dataset e não foi executado.
- A revisão independente Google e `seguranca-llm`, integração das tarefas T014–T017 e G4 humano continuam pendentes. Esta evidência cobre somente T013; não declara as 16 ressalvas concluídas.
- O payload do Qdrant ainda não carrega revisão de decisão/hash da projeção; T014 deve completar essa verificação em lotes, mantendo `authorize_candidates` como barreira autoritativa antes de entregar texto.
- Nenhum caso G3 antigo, contrato, dependência, lock, dado real ou serviço compartilhado foi alterado por esta slice.

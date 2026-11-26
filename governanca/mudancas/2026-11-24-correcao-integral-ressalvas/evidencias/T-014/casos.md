# Casos T014 — escritos antes da implementação

Execução exige Postgres e Qdrant temporários isolados; juiz e embeddings são dublês somente na fronteira externa de teste. Nenhuma chave ou API real é consultada e Langfuse permanece desligado.

| Caso | Requisito | Verificação consumer-visible |
|---|---|---|
| C13.1 | ING-REP-001 | Reconciliação repara ponto, grava revisão/evento/hash de input, nova execução não duplica ID e ponto ausente é recriado. |
| C13.2 | ING-REP-001 / ING-OPS-001 | Interrupção após upsert e antes do ack deixa candidato inacessível; nova geração retoma e publica só depois dos acks. |
| C13.3 | ING-OPS-001 | Lease expirada e novo fencing impedem worker antigo de confirmar ou publicar. |
| C14.1 | ING-AUD-001 / ING-REP-001 | Bloqueio vira liberação auditável; texto só fica visível após projeção completa e hashes/IDs históricos permanecem byte a byte iguais. |
| C14.2 | ING-LEG-001 | Erro do juiz falha fechado e não persiste diagnóstico bruto; origem histórica sem input recebe `legacy_insufficient` explícito e não ganha embedding inventado. |
| C16.1 | ING-REP-001 | Reconciliação de 257 chunks lê Qdrant em lotes completos 128/128/1, filtra por dono e não usa bypass de coleção de teste. |
| C16.2 | ING-OPS-001 | Inventário é dry-run; exclusão exige fixture provada, manifesto/digest/run/coleção/banco frescos, quiescência e autorização e remove apenas ponto isolado listado. |
| C16.3 | ING-AUD-001 | Busca pública valida owner, decisão efetiva, revisão, evento e hash no Postgres; DB indisponível resulta em exceção sanitizada e zero texto. |

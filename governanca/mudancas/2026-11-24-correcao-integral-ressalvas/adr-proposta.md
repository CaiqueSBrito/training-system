# Rascunho de nova ADR — Canônico, decisões imutáveis e índice derivado

Estado: **Proposta**; número definitivo pendente de conferência no despacho futuro (inventário desta preparação termina em 0014). Não edita nem substitui silenciosamente ADR aceita. Após aprovação humana, arquiteto cria nova ADR no próximo número livre em docs/adr.

Contexto: ADR-0008 mantém linhagem independente do Langfuse, ADR-0009 requer proveniência real e ADR-0002 separa índice e linhagem. Registros atuais imutáveis não suportam reclassificação por mutação; página inicial não descreve citações multipágina; Postgres/Qdrant não têm commit conjunto.

Decisão proposta: guardar canônico/spans e entradas de embedding em registros aditivos; decisões de segurança como eventos append-only; estado efetivo derivado de sequência comprometida; reserva/job duráveis com fencing e etapas remotas fora de transação; consumidor consulta autoridade Postgres antes de expor texto; Qdrant é projeção reparável por dono e chave. Fontes insuficientes são explícitas e não reconstruídas por adivinhação.

Alternativas recusadas: alterar chunk/document_version históricos (quebra imutabilidade); page_end como única proveniência (não resolve intervalo/subseções); manter transação durante parse/remoto (contenção e falha distribuída); confiar no payload para liberação (corrida de reclassificação); singleton Docling concorrente sem lock (segurança não demonstrada).

Consequências: armazenamento e migração aditivos, projeção V2 nos consumidores, consulta autoritativa de elegibilidade, backfill conservador e revisão Google. Não exige serviço, biblioteca ou modelo novo. RAGAS continua sujeito a baseline, configuração e limiares aprovados. Langfuse permanece desligado por ordem humana.

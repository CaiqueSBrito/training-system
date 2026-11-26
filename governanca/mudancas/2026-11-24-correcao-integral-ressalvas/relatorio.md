# Relatório de entrega — planejamento e casos

Entregue pacote completo em estado proposta, com data real 2026-11-24, 16 requisitos, matriz 16/16, oito tarefas únicas T-010–T-017, casos novos e plano de implantação. Proposta e gates: proposta.md; contratos concretos e invariantes: contratos.md; protocolo e segurança da leitura: design.md; casos C01–C16/A01–A09: casos.md; migração/backfill/órfãos: implantacao.md; rascunho de ADR aditiva: adr-proposta.md.

Próximo gate real: G1 humano sobre proposta e interfaces; depois G2 por tarefa e G3 dos casos novos, com hashes de rascunho em evidencias/casos-propostos-sha256.txt. A ordem literal "corrija todas as ressalvas" está registrada; nenhuma aprovação de proposta ou casos foi fabricada. A revisão do pacote OpenAI deve ser Google; esta entrega não faz revisão independente de si própria.

Contratos novos: CanonicalTextRecord, CanonicalSpan, ChunkProvenanceRecord, CitationSpanSelection, EmbeddingInputRecord, IngestionReservation, IngestionAuditEvent, EffectiveChunkDecision, ReconciliationJob e OrphanInventoryEntry; operações concretas de reserva/fencing, persistência, auditoria CAS, resolução de citações, autorização por dono e reconciliação. Não foram implementados modelos de produto. Casos executáveis isolados de canônico/spans importam o módulo futuro diretamente e ainda não foram executados nem aprovados; a coleta da suíte poderá falhar até T-010 implementar a interface. Testes antigos intactos.

Lacunas incontornáveis atuais: causa histórica sem traceback (Bloqueado); RAGAS sem runner/evals/instalação, dataset/baseline/limiares/calibração e chave OpenRouter (Bloqueado); fontes/contexto históricos sem prova não podem ser inventados (tratamento insuficiente explícito); consumidor E5 ainda requer ponto de integração real para fechar proteção contra payload stale. Reingestão derivada que rompa a deduplicação existente exige contrato e gate adicionais, não hash falso. Nenhuma exclusão de dados humanos autorizada, nem dos 15 pontos inventariados por prefixo/anti-join; fixture só com manifesto, quiescência e autorização específica.

Não rodados testes/build/lint/suíte cara; sem dependências/lock/commit; Langfuse continua desligado. Correções paralelas preservadas e não presumidas como prontas: interfaces permitem despachar implantação posterior, com pontos de integração explícitos em T-011/T-014/T-016.

Desvio explícito: validador executado na retomada e ao final, duas chamadas em vez da única final solicitada. Resultado final em evidencias/validador.txt; não é evidência de produto nem aceite das 16 linhas.

# T-010 — contratos e ADR implementados

Resultado local: **Passou**, 261 testes de contratos em uma verificação focal (pytest.txt); revisão Google e G4 **Não executado**. Estado da tarefa: provar. Sem testes caros, I/O remoto, dados humanos, credenciais, novos pacotes/lock, commits ou deploy.

## Entrega

Novo módulo real `packages/contracts/src/training_contracts/ingestao.py`, exports aditivos em __init__.py, registro no exportador existente e 13 JSON Schemas novos. Os 19 schemas históricos continuam byte a byte iguais ao git; modelos históricos e casos antigos não foram alterados. Casos originais instalados do .proposed no destino e ampliados antes do produto; ver casos-antes-produto.txt. Não alegada repro por ausência de módulo nem rodada coleta inicial artificial.

Modelos raiz: CanonicalTextRecord, CanonicalSpan, ChunkProvenanceRecord, CitationSpanSelection, EmbeddingInputRecord, IngestionReservation, IngestionAuditEvent, EffectiveChunkDecision, ReconciliationJob, OrphanInventoryEntry; auxiliares exportados: TextRange, ResolvedSpan, JudgeMetadata. Todos estritos quanto a campos extras, congelados com coleções tuple e timestamps UTC. Inteiros/bool de integridade rejeitam coerção de bool/string. Helpers puros validate_canonical_spans, validate_chunk_provenance e resolve_citation_spans recebem fonte real explicitamente e validam partição, texto/offset/hash, identidade, limite H1 e interseções reais de citação; não fazem I/O nem autorização.

ADR-0015 formaliza a decisão aditiva aprovada para implementação em G1/G2. Não altera ADR aceita e não concede G4. Procrastinate permanece a única fila: reserva/generation/lease são consistência e ReconciliationJob é checkpoint de negócio ligado ao job, sem scheduler, retry loop ou agendamento próprios.

## Interfaces para T-011/T-012/T-013/T-014

- Canônico tem hash UTF-8 e texto exato; offsets em codepoints semiabertos. CanonicalSpan.kind inclui separator sem página; provenance unknown nunca admite page preenchida. Documentos sem paginação podem ter page null mesmo com correspondência textual exact. h1_id identifica ocorrência, não título repetido.
- ChunkProvenanceRecord exact exige origem, intervalo e span_ids; insufficient exige canonical_id/start/end null e coleções vazias. overlap_ranges são intervalos absolutos canônicos internos e disjuntos. Helpers exigem partição completa ordenada do canônico e todos os spans intersectados pelo chunk, preservando subseções.
- CitationSpanSelection usa intervalo absoluto explícito e evidence exata; resolve_citation_spans retorna todos os spans intersectados, com start/end recortados, páginas/seções reais e unknown explícito. Autorização do dono é obrigatória antes da chamada pelo repositório/consumidor; o helper não a simula.
- EmbeddingInputRecord preserva title, section_context, text_sha256, format_version, embedding_key e input_sha256. validate_input(text, formatted_input) confere hashes dos bytes exatos produzidos pelo formatter já existente no LLM; não duplica formatter. T-014 deve usar o formatter existente e garantir vínculo dos campos persistidos com essa entrada; falta de contexto verificável bloqueia reparo.
- Auditoria explicita os vínculos omitidos na tabela sintética: triage_event_id e judge_event_id opcionais por etapa. triage: clean/no_suspicion sem regras, ou suspect/suspected com regras; sem metadados do juiz. judge: referência de triagem, JudgeMetadata obrigatório e outcome safe/injection/error coerente com reason judge_release/judge_block/judge_unavailable. decision: referência de triagem e, quando decisão do juiz, referência do evento judge; não duplica judge_metadata. decision.allowed/blocked/unavailable corresponde ao reason_code. supersedes_decision_id somente na etapa decision, nunca autorreferência. Casos de erro do juiz não viram veredito safe.
- JudgeMetadata guarda provider/model/config_version/hash/prompt_version/hash, request_id opaco restrito ou null, result e error_kind enum. ErrorCode: provider, invalid_output, timeout, configuration, storage, conflict, stale_generation, legacy_insufficient, index_divergent. ReasonCode adicional suspected representa a triagem; os demais são os previstos na proposta. Sem reason bruto, prompt ou chave no contrato de auditoria.
- Reserva exige generation positiva, identidade e erro somente em fase failed; checkpoint exige erro sanitizado em retry/blocked e ausente nas demais fases. São registros de domínio, não implementação de scheduler. Unicidade owner/hash e CAS/fencing de verdade ficam no Postgres.
- OrphanInventoryEntry aceita owner_id/version_id null quando não verificáveis, sem inventar identidade. fixture_proven exige IDs e referência de manifesto, mas essa referência não é autorização de exclusão nem prova verificada pelo modelo: T-014 verifica propriedade/run/quiescência e decisão humana específica. Não remover por prefixo/anti-join.

FKs, equivalência de dono/versão/política/input entre eventos referenciados, ordem append-only, CAS por revisão, derivação da decisão efetiva mais recente e integridade transacional são obrigatórias no repositório T-011/T-013. Nenhum modelo isolado promete essas verificações de banco nem permite expor candidato por omissão. Leitura segura/Qdrant permanecem T-014; pytest local não os comprova.

## Referências e limitações

LSP Python indisponível confirmado pelo principal; fallback AST/rg autorizado em msg_09b1f76fb847 antes de alterar exports. Inventário mostra adição sem rename, portanto nenhum caller histórico exige migração ou alias. Callers novos T-011/T-012 usam os exports diretamente. Sem dependência instalada para suprir LSP; não declarar LSP executado com sucesso.

RAGAS **Bloqueado** globalmente por falta de ambiente/runner/dataset/baseline/limiares/calibração/chave aprovada; não foi executado ou substituído. Esta tarefa adiciona contratos/validação determinística, sem trocar modelo, prompt, chunker ou formatter de produto. Causa histórica sem captura não foi diagnosticada. A credencial exposta pelo backend não foi acessada; rotação continua com o principal/humano. Langfuse não foi religado.

Próximo passo: revisão independente Google de T-010 e integração de T-011/T-012 já notificada ao principal, seguindo dependências autorizadas; aceite G4 permanece exclusivamente humano. Validador final em validador.txt.

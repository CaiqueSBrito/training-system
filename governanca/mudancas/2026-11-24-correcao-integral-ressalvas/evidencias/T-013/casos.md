# T-013 — casos instalados antes do protocolo

Arquivo consumidor: `packages/ingestion/tests/test_ressalvas_protocolo.py`.

- `test_C07_no_begin_at_parse_judge_embed_upsert_and_dedup`: PostgreSQL e Qdrant descartáveis; observa ausência de transação em parse, juiz, embedding e upsert; dedup do mesmo dono e isolamento de outro dono.
- `test_C07_two_writers_busy_and_no_prepublication`: writer concorrente recebe busy, reserva permanece parsing e texto não é autorizado.
- `test_C07_stale_slow_parser_cannot_publish`: generation nova conclui; parser lento da generation anterior não publica.
- `test_C07_crash_after_parse_commit_recovers_without_reparse`: retoma canônico/chunks persistidos e compara JSON histórico byte a byte.
- `test_C07_crash_after_upsert_is_unacknowledged_then_converges`: ponto Qdrant não reconhecido não é autorizado; repetição idempotente confirma a projeção.
- `test_C12_judge_error_persists_error_without_raw_reason_then_retries`: erro/timeout sanitizado tem evento `error`, decisão indisponível e retry durável.
- `test_C14_reclassification_preserves_history_and_revokes_ready`: nova decisão bloqueante revoga autorização sem alterar documento/chunks.
- `test_T013_judge_metadata_matches_exact_request_and_changes_with_settings`: dublê LiteLLM consumidor verifica configuração e prompt realmente enviados contra hashes; sem API externa.

SHA-256 do arquivo na prova final: registrado em `casos-sha256.txt`. O caso de fingerprint foi acrescentado depois da autorização expressa para a interface ampliada de `packages/llm`; a primeira implementação da interface já estava em edição, portanto este complemento não é alegado como teste escrito antes do primeiro rascunho. Os casos C07/C12/C14 precederam a implementação do protocolo. Nenhum caso G3 congelado foi alterado.

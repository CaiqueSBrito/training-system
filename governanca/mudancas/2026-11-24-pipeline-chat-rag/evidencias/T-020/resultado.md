# T-020 — evidência

Executor OpenAI, dispatch ctx_8af50d85ac08. Revisão por outro provedor: Não executado.

- Focal inicial: Falhou, 10 erros de setup em 2.14s; `pytest.txt`. A fixture tentou mudar document_version_id de reserva já criada e o trigger de fencing corretamente rejeitou a mutação. Nenhuma prova funcional de E12 foi alcançada nesta execução.
- Correção: fixture agora insere sua reserva inicial com ID estável e usa as APIs reais commit_parsed/assessment/index_ack/publish_ready para produzir contexto autorizado; não muda a identidade da reserva. Prova complementar: Passou, 10 testes em 2.90s (`pytest-complemento.txt`), autorizada em msg_fa47bdf02790 após correção concreta, sem afrouxar triggers/fencing/asserts.
- Banco: descartável por teste, criação template0 e exclusão apenas desses bancos aleatórios `test_t020_*`. Nenhuma migração na app compartilhada, nenhum acesso LLM/Qdrant necessário à prova de persistência.
- LSP: Bloqueado, pyright/pylsp não instalados; AST e referências locais inspecionados sem instalar ferramenta.
- RAGAS: Não se aplica à slice determinística de persistência, sem alteração de recuperação/prompt/modelo/limiar; integração/avaliação E1–E12 pertence à T021.
- Sem build/lint/formatter, deps/lock, commit/push/deploy ou alteração nos três arquivos G3 T001. Migrações anteriores intocadas por este worker.

## Resultado funcional focal

- RAG-SAI-002: Passou no repositório; resposta inteira gravada com turn/plan/run/candidates/item/citation/verdict antes do retorno. Rollback externo e falha na última inserção (verdict_id duplicado) removem o grafo parcial sem remover o turno anterior. Entrega somente após commit é responsabilidade do chamador T021, ainda Não executado nesta slice.
- OBS-LIN-001: Passou; consulta de owner correto devolve ItemLineage + ResolvedSpan exato da segunda ocorrência da evidência (start6/end9/page3, em chunk que começa na página2), outro owner recebe None; FK de órfão e trigger de chunk de outro owner rejeitam inserts SQL diretos. Metadata corresponde integralmente ao schema migrado. Decisão revogada rejeita persistência de resposta.
- RAG-ENT-001: Passou; E1 persistida sem intenção/modo/E2, erro rastreável, rate limit com duas conexões concorrentes resulta em exatamente um True e um False para limit1; limite reaparece em nova conexão, owner diferente isolado e expiração usa relógio DB.
- Continuação: Passou; scope/cursor/used_chunk_ids/quantity preservados por owner+session; rollback não avança cursor e mesmo scope não permite perder progresso. API específica em interfaces.md.
- Aviso de teste: 1 warning Pydantic na fixture de erro construída com model_copy e status string; save_turn revalida contra enum antes do banco. Não há falha nem warning de segredo.
- AST: Passou (parsing dos quatro arquivos da slice); LSP permanece Bloqueado. Migração usa snapshot SQL estático, não metadata mutável em runtime.
- Risco de integração de testes anteriores: `test_ressalvas_persistencia.py` contém asserts históricos de head igual a 0003 e de downgrade 0002 após migrate(head). Nova head é legitimamente 0004; esses casos não foram executados/editados nesta slice, manutenção de expectativa deve ser coordenada sem confundir com G3 T001.

Revisão independente: Não executado. Aceite G4: Não executado. Sem aprovação humana inferida dos testes.

- Validador final: Passou, governança OK (6 mudanças, 21 tarefas, 28 requisitos na spec viva); evidência `evidencias/T-020/validador.txt`.

## Correção da revisão (2026-11-25)

Revisão Anthropic anterior não aprovada preservada em revisao-anthropic.md; correções dos dois bloqueantes e novas interfaces estão em correcao-revisao.md e interfaces.md. Focal afetada Passou: 19 testes em 5.21s, incluindo os 10 anteriores, em pytest-correcao-revisao.txt; revisão independente da correção Não executado.

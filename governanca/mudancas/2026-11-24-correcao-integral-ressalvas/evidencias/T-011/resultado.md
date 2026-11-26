# T-011 — persistência canônica, auditoria e reservas

Dispatch efetivo ctx_34fd30a2a133; executor OpenAI/rag-backend. Dependência T-010 concreta e aprovada pela revisão Google, interfaces T-012 confirmadas pelo principal. G1/G2 humano aprovo_proposta_contratos_e_execucao_T010_T017, dispensa G3 dispenso_G3_T010_T017; nenhum aceite G4 inferido. Somente propriedade T-011 alterada, sem commit/dependência/lock/contratos/DDL compartilhado, sem chamadas externas nem leitura de saídas sensíveis antigas. Langfuse não foi iniciado.

## Provas exatas e histórico preservado

- Primeira e única focal completa dos casos novos: LITELLM_LOG=ERROR uv run pytest -v --tb=short packages/lineage/tests/test_ressalvas_persistencia.py — **Falhou**, 35 passed / 6 failed em 6,35 s (pytest.txt). Preservada sem sobrescrever. Não houve build/lint/testes intermediários; geração do snapshot literal DDL fez parte da implementação sem conectar banco.
- Diagnóstico: inferência ambígua de FROM/JOIN nas leituras de mapas/entradas (cinco caminhos), e classificação de sequência obsoleta recebia erro genérico de cadeia/hash (um caminho). Corrigidos por FROM/ON explícitos e separação da categoria de revisão. Casos não foram alterados para acomodar falha. Registro em correcoes-pos-focal.md.
- Complemento autorizado pelo principal, mensagem msg_735056d6d974, apenas dos seis caminhos: LITELLM_LOG=ERROR uv run pytest -v --tb=short packages/lineage/tests/test_ressalvas_persistencia.py -k 'citation_intersections or partial_error or legacy_insufficient or reservation_fencing_ready' — **Passou**, 6 passed / 35 deselected em 1,22 s (pytest-complemento.txt). Nenhum dos 35 já aprovados foi repetido. Evidência composta cobre os 41 casos; não é declaração de suíte inteira reexecutada ou prova integrada.
- Postgres real em bancos test_t011_<uuid> exclusivos; upgrades, consultas e remoção apenas desses bancos. Nenhum mock substitui persistência, nenhum serviço compartilhado recebeu DDL e nenhuma API externa foi chamada. Fixtures sintéticas e metadados de juiz existem apenas nos testes, não são alegação de veredito online.

## Requisitos — escopo T-011

| Requisito | Status | Evidência e limite |
| --- | --- | --- |
| ING-MIG-001 | Passou | C01 vazio/0001/0002 → 0003, head idempotente e documentos/chunks antigos iguais; snapshot aditivo sem reescrever 0001/0002 ou defaults de backfill. |
| ING-MIG-002 | Passou | C02 CHECK page/range e FK/razão inválida por SQLSTATE/constraint, catálogo e compare_metadata sem divergência, UPDATE/DELETE/TRUNCATE das cinco tabelas novas históricas; downgrade vazio mantém 0002/proteções e história nova recusa antes de DROP. |
| ING-CAN-001 | Passou | C04 UTF-8/emoji/NFD; hash, texto, parser, intervalo, spans, H1 e entrada divergentes recusados sem fonte parcial; reprodução via formatter existente e texto persistido. |
| ING-PRO-001 | Passou | C03 complementado: interseções reais de páginas 2/3, separador sem página, null/unknown e duas ocorrências pelo offset; owner alheio negado. Parser real fim-a-fim e integração são T-012/T-013/T-017, não esta focal. |
| ING-AUD-001 | Passou | C12 cadeia imutável inclusive liberação, modelo/config/prompt por versão/hash, regra bloqueante com juiz safe preservado; ausência/erro/cadeia parcial não autoriza; dois writers CAS deixam só uma cadeia nova; vínculo cruzado reverte a unidade. |
| ING-LEG-001 | Passou | Parte de persistência C14: insuficiência explícita sem coordenada/fonte inventada, políticas blocked→allowed→error e allowed→blocked sem alterar hashes/flags/IDs; confirmação stale não autoriza consumidor. Inventário/remoção e projeção remota ficam para T-014, sem declaração de que já foram executados. |

Persistência auxiliar C07: **Passou** na focal/complemento — busy, dono distinto, takeover com version_id estável/generation crescente, token antigo não renova/publica, checkpoints/recibos/ready e consumo pelo dono/revisão correta; decisão nova bloqueia leitura imediatamente. Protocolo completo com CPU/remotos fora da transação e fila é T-013, não avaliado como concluído nesta tarefa.

## Entrega e invariantes

Nove tabelas novas: cinco históricas (canonical_text, canonical_span, chunk_provenance, embedding_input, ingestion_audit_event) e quatro operacionais (ingestion_reservation, effective_chunk_decision, reconciliation_job, index_ack). Guarda de partição canônica adiada ao commit, vínculos relacionais, trilha sequencial/CAS e fencing, publicação e leituras negando estado incompleto. Procrastinate continua a única fila; estas tabelas guardam estado de negócio, não agendam execução.

Assinaturas concretas para T-013/T-014 e uso de contratos T-012 em interfaces.md e README. Nativamente exigem owner, evaluated_input para hash de avaliação e formatter puro existente para validar entrada; nenhum modelo local duplica T-010. Recibos devem ser gravados só depois de confirmação remota pela camada executora; esta prova de armazenamento não finge ter chamado Qdrant.

Arquivos de produto alterados/criados: packages/lineage/src/training_lineage/tables.py, packages/lineage/src/training_lineage/repository.py, packages/lineage/src/training_lineage/migrations/versions/0003_canonical_audit_protocol.py, packages/lineage/tests/test_ressalvas_persistencia.py, packages/lineage/README.md. Governança: tarefa T-011.md e somente evidências T-011/{casos.md,pytest.txt,correcoes-pos-focal.md,pytest-complemento.txt,interfaces.md,resultado.md,validador.txt}. Nenhuma edição em migration.py, casos anteriores, ingestion/core.py, contratos, outra tarefa, ADR, dependências ou lock.

## Pendente

- **Não executado**: integração T-013/T-014, revisão independente Google, prova integrada T-017 e G4 humano. Tarefa em revisar, não concluída; não declara as 16 ressalvas resolvidas.
- **Não executado**: casos antigos congelados; por leitura há asserts head=0002 e lista antiga exata de tabelas em test_lineage.py, incompatíveis com schema aditivo. Reportado ao principal, sem mudança silenciosa ou teste afrouxado.
- Fonte antiga insuficiente permanece bloqueada, sem reconstrução por overlap ou título/contexto inventados; reclassificar não muda o mapa histórico. Tratamento de derivação/fonte e reconciliação exige o plano autorizado, não hash falso.
- APIs externas/credenciais não necessárias para esta tarefa; suspensão por rotação e causa histórica r2/run3 não foram investigadas nem declaradas resolvidas aqui.

Validador de governança: **Passou**, execução única final; saída integral em validador.txt. Não houve commit nem G4.

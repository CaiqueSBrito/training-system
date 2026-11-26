# ADR-0015 — Canônico verificável, decisões imutáveis e índice derivado

Estado: **Aprovada para implementação** pelo humano em G1/G2 (2026-11-24, `aprovo_proposta_contratos_e_execucao_T010_T017`); revisão independente Google e aceite final G4 da mudança permanecem pendentes. Formaliza a decisão aditiva da mudança `2026-11-24-correcao-integral-ressalvas`, sem editar ADR aceita nem atribuir aprovação a saída de modelo.

## Contexto

ADR-0008 coloca a linhagem no Postgres da aplicação; ADR-0009 exige proveniência real do Docling; ADR-0002 separa índice e linhagem; ADR-0012 determina Procrastinate como fila. A página inicial de um chunk não resolve citações entre páginas/subseções. Registros de chunk/document_version são imutáveis, mas políticas novas precisam reclassificar conteúdo. Postgres e Qdrant não compartilham commit; uma projeção atrasada não pode liberar conteúdo rejeitado.

## Decisão

1. Persistir texto canônico imutável identificado por receita/versão e SHA-256 UTF-8. Intervalos semiabertos usam codepoints sobre esse texto exato. Spans particionam o canônico, inclusive separadores sem página, preservando todas as páginas/seções verificáveis. Uma ocorrência H1 tem identidade própria; H2/H3 não obrigam corte. Página/layout ausentes ficam explicitamente desconhecidos, sem interpolação inventada.
2. Adicionar contratos estritos imutáveis em `training_contracts.ingestao`, usando tuples nas coleções. Manter intactos modelos e JSON Schemas históricos. Validações locais não fingem acesso a banco: helpers puros recebem texto/spans/chunk reais; FKs, isolamento do dono, unicidade, CAS e triggers são do repositório e migrations futuras.
3. Trilha de segurança append-only com triage, judge quando chamado e decision, inclusive liberação. Juiz guarda modelo/config/prompt versionados por identificador/hash, resultado e categoria de erro sanitizados; decisão referencia os eventos efetivos. Não duplicar resposta bruta do juiz. Reclassificar criando eventos novos, jamais alterando flags históricas. Falta de decisão completa/atual mantém conteúdo indisponível.
4. Reserva de ingestão guarda chave owner/hash, generation/fencing e estado de consistência. Parse, juiz, embedding e operações Qdrant ocorrem fora de transações longas. **Procrastinate continua sendo a única fila**, responsável por agendamento, tentativas e execução. `IngestionReservation` não é scheduler; `ReconciliationJob` é checkpoint de negócio correlacionado ao job Procrastinate, não uma segunda fila ou loop de polling.
5. Índice é projeção reparável por owner/versão/chave/revisão. Persistir título/contexto/hash/formato da entrada de embedding para reprodução exata pelo formatter existente; não introduzir outro formatter nem preencher título ausente fictício. Fonte ou contexto históricos não verificáveis ficam insuficientes. Reingestão derivada não altera UNIQUE(owner,sha) por hash falso.
6. Antes de devolver conteúdo de candidatos ao modelo, consultar readiness e decisão efetiva autoritativas na linhagem. Payload do Qdrant é cache; nem um upsert stale nem uma decisão de outro dono concede acesso. Reparos e remoções de projeções vinculadas exigem escopo aprovado e idempotência; órfãos desconhecidos só entram em inventário read-only.
7. Inventário não autoriza exclusão. Fixture comprovada exige referência a manifesto de propriedade/run; execução de remoção exige também escopo/digest atual, quiescência e autorização humana específica. Prefixo test-/integration- e anti-join não comprovam descarte. Nenhuma exclusão de dados humanos é permitida.

## Alternativas recusadas

- Atualizar chunk/document_version para reclassificar: destrói história imutável.
- Usar apenas page_end: não resolve evidência/subseção por intervalo.
- Inventar página para elemento multipágina sem correspondência por substring: falsifica proveniência.
- Manter BEGIN durante parse/remoto: contenção sem atomicidade distribuída.
- Construir scheduler próprio sobre reserva/job: duplica Procrastinate e contraria ADR-0012.
- Confiar só em payload ou coleção para permitir recuperação: decisão pode mudar enquanto índice está atrasado.

## Consequências

Migração e projeções são aditivas e terão prova real em T-011–T-017. Contratos não concedem autorização nem executam I/O. Docling será reutilizado com lock por instância, sem singleton concorrente inseguro. Casos novos tiveram G3 dispensado pelo humano (`dispenso_G3_T010_T017`), mantendo testes antes do produto e casos antigos intactos. Nenhuma dependência, serviço, modelo ou rubrica novos entram por esta ADR. Langfuse continua desligado. RAGAS continua Bloqueado enquanto faltarem baseline/dataset/limiares/calibração/ambiente/chave autorizados; testes de contratos não o substituem.

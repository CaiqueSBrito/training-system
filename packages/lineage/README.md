# training-lineage

Linhagem da ingestão no Postgres da aplicação, separado do Langfuse (ADR-0008;
ING-LIN-001). SQLAlchemy 2 Core, psycopg 3 e Alembic, sem criação implícita de tabelas.

Da raiz do workspace:

```sh
uv sync --all-packages
uv run alembic -c packages/lineage/alembic.ini upgrade head
uv run pytest packages/lineage
```

A configuração lê `.env` no diretório de execução, ou um caminho explícito passado
a `create_lineage_engine(env_file)` / `database_url(env_file)`. Aceita atribuições
`KEY=value`, aspas simples/duplas e comentários, sem executar shell nem expandir
variáveis. As variáveis de ambiente prevalecem sobre o arquivo.
São obrigatórias `APP_POSTGRES_DB`, `APP_POSTGRES_USER`, `APP_POSTGRES_PASSWORD`;
`APP_POSTGRES_PORT` usa 5432 e `APP_POSTGRES_HOST` usa 127.0.0.1 quando ausentes.
Não lê credenciais do Langfuse. O engine oculta parâmetros nas exceções SQL e usa
UTC na sessão; timestamps sem fuso recebidos pelo repositório são tratados como UTC.
Timestamps com fuso preservam o instante e voltam em UTC.

```python
from training_lineage import LineageRepository, create_lineage_engine, migrate

engine = create_lineage_engine()
migrate(engine)  # executado pelo processo responsável pela implantação
# document e chunks são DocumentVersionRecord e ChunkRecord da ingestão.
with engine.begin() as connection:
    repository = LineageRepository(connection)
    repository.insert_document_version(document)
    for chunk in chunks:
        repository.insert_chunk(chunk)

with engine.connect() as connection:
    repository = LineageRepository(connection)
    saved = repository.get_document_version(document.document_version_id)
    ordered = repository.list_chunks(document.document_version_id)
engine.dispose()
```

O chamador controla a transação: nenhum método do repositório faz commit.
Entradas são revalidadas, inclusive instâncias modificadas via `model_copy`;
leituras retornam os contratos, com validação. `get_document_version`, `get_chunk`
e `find_document_version(owner_id, sha256)` retornam `None` quando ausentes.
`list_chunks` retorna lista vazia para versão sem chunks. IDs e pares dono/hash
duplicados falham com `IntegrityError`; não há sobrescrita silenciosa.

## Esquema

| Tabela | Colunas e garantias |
| --- | --- |
| `document_version` | `document_version_id TEXT PK`, `document_id TEXT`, `owner_id TEXT`, `sha256 TEXT`, `parser_version TEXT`, `created_at TIMESTAMPTZ`; todos obrigatórios; identificadores não vazios; hash hexadecimal minúsculo de 64 caracteres; `UNIQUE(owner_id, sha256)` |
| `chunk` | `chunk_id TEXT PK`, `document_version_id TEXT FK → document_version` (`RESTRICT`), `chunker_version TEXT`, `page BIGINT NULL`, `section_path TEXT[]`, `start_offset BIGINT`, `end_offset BIGINT`, `text TEXT`, `injection_flagged BOOLEAN`, `injection_reason TEXT NULL`; demais colunas obrigatórias |

Checks de chunk exigem página positiva, offsets crescentes e não negativos,
texto visível, identificadores não vazios, seções sem elementos nulos e motivo de
injeção visível se e somente se marcado. As duas tabelas são imutáveis:
triggers rejeitam UPDATE (inclusive sem
mudança de valor), DELETE e TRUNCATE, mesmo por SQL direto. A aplicação não deve
operar com permissões de DDL que permitam remover essas proteções.

A consulta ordena por página crescente (NULL por último), offset inicial, offset
final e ID para desempate; documentos sem páginas usam offsets. `section_path`
não é ordenado alfabeticamente, preservando a ordem de leitura da fonte. O índice
`chunk_version_reading_order` segue versão e essas colunas de ordenação. Chunks
marcados também são retornados para auditoria; a ingestão/recuperação deve aplicar
a exclusão exigida por ING-INJ-001.

As migrations `0001` e `0002` são distribuídas dentro do módulo, portanto `migrate(engine)`
funciona no pacote instalado. O Alembic mantém `alembic_version`.
`migrate(engine, "0001")` já subia até a revisão intermediária; agora também
desce de `0002` até `0001`, preservando as linhas. A direção é inferida pela
ancestralidade Alembic da revisão atual e do alvo explícito exato. `head` e
`base` são os únicos atalhos aceitos; abreviações, deslocamentos relativos,
intervalos, revisões desconhecidas e estados com múltiplas revisões são recusados
antes de qualquer DDL. Falhas abortam a transação. O downgrade a `0001` remove
somente a proteção de document_version da `0002`; a proteção de chunk permanece.
A reversão explícita `migrate(engine, "base")` ou `alembic ... downgrade base`
remove as tabelas e as funções de proteção: usar somente quando a remoção for autorizada.

Os testes usam as credenciais locais para criar bancos vazios `test_lineage_<uuid>`
a partir de `template0`, executar migrations/repositório/SQL direto e remover apenas
esses bancos ao terminar. Exigem `CREATEDB`, não usam mocks nem alteram o banco da
aplicação; indisponibilidade do Postgres causa falha, sem skip silencioso.
Além de compare_metadata, os testes exercitam individualmente os CHECKs por
INSERT direto e verificam UPDATE/DELETE/TRUNCATE das duas tabelas, remoção dos
triggers/funções no downgrade e roundtrip de revisão intermediária com dados.

## T-011 / ADR-0015 — persistência V2

A revisão aditiva `0003` acrescenta `canonical_text`, `canonical_span`,
`chunk_provenance`, `embedding_input` e `ingestion_audit_event` append-only.
Triggers recusam UPDATE, DELETE e TRUNCATE; nenhuma flag, hash ou ID histórico é
atualizado. A fonte canônica é persistida antes do chunking/consumo e verificada por
SHA-256 UTF-8; spans particionam integralmente codepoints, inclusive separadores.
O banco confere parser, intervalos, conteúdo, H1, referências de spans e hash do
texto de embedding. A recipe/formatação não é duplicada: o consumidor fornece o
formatter existente (`training_llm.document_input`) às operações indicadas.

`effective_chunk_decision`, `ingestion_reservation`, `reconciliation_job` e
`index_ack` são projeções/checkpoints operacionais mutáveis, com FK, CAS e fencing;
não são história, fila ou scheduler. A história de decisões fica nos eventos.
`ReconciliationJob.job_id` correlaciona o job Procrastinate da camada executora;
este pacote não agenda, faz polling, retry, parse ou chamadas de modelo/Qdrant.
Reserva precede documento, logo seu version_id ainda não tem FK nesse instante;
a associação owner/hash/versão é conferida quando o documento existe e no commit.

As assinaturas concretas abaixo são métodos de `LineageRepository(connection)`.
A Connection e a transação curta pertencem ao chamador. Parse, formatter remoto,
juiz, embedding e índice nunca devem rodar dentro dela; o formatter injetado aqui
é a função pura existente, não uma chamada remota.

```python
reserve(owner_id, source_sha256, attempt_id, *, lease_seconds=300)
# -> ("acquired" | "busy" | "ready", IngestionReservation)
get_reservation(owner_id, reservation_id)  # -> record | None
renew(reservation_id, generation, *, owner_id, lease_seconds=300)
transition_reservation(reservation_id, generation, *, owner_id, phase, error_code=None)
commit_parsed(reservation_id, generation, *, owner_id, document, canonical,
              spans, chunks, provenance, embedding_inputs, job, format_input)
persist_canonical(owner_id, record, spans, chunks, provenance,
                  embedding_inputs, *, format_input)
has_persisted_artifacts(owner_id, version_id)  # -> bool
get_canonical(owner_id, canonical_id)  # -> CanonicalTextRecord | None
get_canonical_for_version(owner_id, version_id)  # recusa receita ambígua
list_canonical_spans(owner_id, canonical_id)  # -> tuple[CanonicalSpan, ...]
load_chunk_provenance(owner_id, chunk_ids)  # -> dict[chunk_id, ChunkProvenanceRecord]
load_embedding_inputs(owner_id, chunk_ids, *, embedding_key=None)
reproduce_embedding_input(owner_id, chunk_id, embedding_key, *, format_input)  # -> str
resolve_citation(owner_id, selection)  # -> tuple[ResolvedSpan, ...]
mark_legacy_insufficient(owner_id, chunk_id, *, created_at)
get_assessment_revisions(owner_id, chunk_ids)  # -> dict[chunk_id, int], zero sem evento
append_assessment(events, expected_revision, *, evaluated_input,
                  reservation_id=None, generation=None)  # -> EffectiveChunkDecision | None
list_assessments(owner_id, chunk_id)  # -> tuple[IngestionAuditEvent, ...]
get_effective_decisions(owner_id, chunk_ids)  # -> dict[chunk_id, EffectiveChunkDecision]
create_reconciliation_job(record, *, reservation_id, generation)
get_reconciliation_job(owner_id, job_id)  # -> ReconciliationJob | None
checkpoint_job(record, *, expected_attempt, expected_cursor, reservation_id, generation)
list_reconciliation_chunks(owner_id, version_id, *, cursor=None, limit=128)
record_index_ack(owner_id, chunk_id, embedding_key, *, reservation_id,
                 generation, decision_revision)
publish_ready(reservation_id, generation, *, owner_id, embedding_key)
authorize_candidates(owner_id, candidate_ids, observed_revision)  # -> dict[chunk_id, ChunkRecord]
```

`commit_parsed` recebe os contratos concretos T-010/T-012, incluindo a versão do
Documento com o ID reservado, canônico, spans, mapas e entradas exatas; grava tudo
com o checkpoint de negócio na mesma unidade atômica. O primeiro `persist_canonical`
exige pai já gravado pelo chamador ou por `commit_parsed`. Entradas válidas repetidas
são idempotentes por identidade; divergência não sobrescreve história. Após takeover,
`reserve` conserva version_id, incrementa generation e retorna fase persisted se os
artefatos estão completos: retomar da fonte persistida, sem reparsing/concatenação
de overlaps nem novos timestamps artificiais para registros antigos.

Toda lease usa o relógio do Postgres; generation vencida ou expirada não renova,
confirma índice ou publica. `transition_reservation` não publica ready: isso só
ocorre após checkpoints completos e confirmação de cada decisão atual. Reservas
ready cuja decisão/recibo ficou stale deixam de autorizar consumo e podem ser
reclamadas para reparo, com nova geração. Ao retomar job antigo, `checkpoint_job`
faz CAS por attempt/cursor e vincula a nova geração; revisões desejadas mais novas
invalidam jobs antigos. Cursor de reconciliação é chunk_id ordenado, escopado ao
mesmo dono/versão; não é um cursor do Qdrant nem mecanismo de fila.

`append_assessment.expected_revision` é a última sequence global do chunk,
inclusive triagem/juiz parciais; zero significa sem história. `evaluated_input`
é o corpo exato avaliado, transitório, usado só para conferir input_sha256, não
armazenado como diagnóstico. Referências fortes conferem chunk/dono/versão,
attempt/política/heurística/hash, estágio e supersedes; o evento judge mantém
metadados aprovados de modelo/config/prompt por versão/hash, sem reason bruto.
`rule_block` pode divergir de judge.safe, preservando ambos sem falsificar motivo.
Uma cadeia parcial ou erro torna a decisão anterior indisponível imediatamente.

`get_effective_decisions` serve à avaliação/reconciliação antes da publicação e
**não** autoriza recuperar conteúdo. Somente `authorize_candidates` devolve texto
V2: exige dono, revisão observada atual, fonte/mapa/entrada exatos, reserva ready,
job mais novo completo e recibos da geração/decisão atuais. Ausência/erro nega;
falha do banco propaga, nunca libera por fallback. Locks de leitura mantêm a
avaliação estável na transação; o chamador não deve ignorar a autorização ou usar
os getters históricos sem dono como uma API de recuperação. Leitura em lotes de
IDs é limitada a 128; reparo tem paginação ordenada própria. `resolve_citation`
autoriza metadados de proveniência ao dono e valida evidência/interseção; não é
uma autorização alternativa para carregar chunk bloqueado ao modelo.

Versões legadas não ganham defaults ou fonte fabricada. `mark_legacy_insufficient`
registra coordenadas nulas/arrays vazios e mantém citações exatas indisponíveis.
Mapeamento já persistido é histórico: não pode ser reescrito por uma tentativa de
reparo; nova política usa eventos, sem mudar injection_flagged. A reconstrução de
fonte derivada exige os contratos/gates previstos no plano, não hash salgado.

O snapshot de DDL da 0003 é literal: não importa metadata mutável em runtime nem
reescreve 0001/0002. Downgrade primeiro verifica todas as novas tabelas; se houver
história ou checkpoints, recusa antes de qualquer DROP. Exercitar somente fixture
vazia autorizada; deploy/DDL no banco compartilhado continua proibido nesta etapa.
Os casos novos estão em `tests/test_ressalvas_persistencia.py`. Casos antigos que
assumem head=0002 e apenas duas tabelas permanecem intactos; essa suposição deixa
de valer com uma migração aditiva e requer decisão do principal antes da prova
integrada, não afrouxamento silencioso pelo executor.

# Recuperação E5–E7

`training_retrieval` implementa recuperação; não é alias dos contratos nem do SafeRetriever.
Os registros `ChunkRecord`, `NumberedContext`, `RetrievalRunRecord`, `SearchPlan` e
`Classification` são os contratos canônicos de `training_contracts`.

```python
from training_retrieval import RetrievalService, RetrievalScope, LocalReranker

service = RetrievalService(engine, index, model_client,
    LocalReranker('packages/retrieval/calibration/bge-v1.json'), chat_turn_id=turn_id)
result = service.retrieve(owner_id, plan, classification,
    RetrievalScope((document_version_id,), section_prefix=('Capítulo 3',)), count=8)
```

O chamador fornece identidade confiável, versões explícitas e componentes exatos de seção;
a classificação do modelo não se transforma em SQL/filtro livre. A resolução consulta somente
metadados no Postgres, nega versões alheias e prefixos ausentes/ambíguos. Várias versões
explicitamente escolhidas sem seção constituem um escopo composto válido, na ordem fornecida.
O filtro server-side contém owner, versões, chave de embedding, flag e IDs do prefixo resolvido.
As duas buscas usam o mesmo filtro, inclusive nos dois prefetches dense/BM25 português;
Qdrant calcula RRF. Cada uma das 1–3 consultas recupera até 50 propostas; união deduplicada
limitada a 50 (configurável 30–50, maior score RRF por chunk, desempate por ID).

O score RRF é relativo à posição, não probabilidade nem similaridade densa. O BGE retorna
logits transformados explicitamente por sigmoid (0..1), sem alegar probabilidade calibrada.
Para múltiplas consultas aplica-se o maior score por chunk. Ordenação consulta decrescente,
top 5 configurável; corte estrito `score > cutoff`. O corte é obrigatório, carregado de
artefato versionado que vincula modelo/revisão/dataset/domínio/scores e hash. Ausência,
corrupção ou calibração reprovada impede inicialização; não existe corte default.

Os pesos `BAAI/bge-reranker-v2-m3`, revisão
`953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`, são lazy e únicos por processo, em CPU,
batches de 2, comprimento máximo 512 tokens e até 4 threads. Inicialização e inferência
serializadas evitam cópias concorrentes em RAM. O download inicial precisa de rede; falha
é erro, sem score sintético, modelo alternativo, retry ou fallback lexical. Um processo
por máquina é a recomendação operacional neste computador limitado em memória.

Cobertura enumera o escopo autorizado inteiro, sem filtro temático, BGE ou top-N de
consulta. O orçamento `count` (1..20) limita a janela de contexto; `run.cutoff=None`
e `rerank_score=None` registram que o corte não se aplica. Conteúdo insuficiente neste
modo significa ausência de conteúdo canônico autorizado disponível, nunca baixa relevância
para uma consulta. Essa regra segue arquitetura §2.1 e orientação do coordenador
`msg_c5b6412c04cc`; a consulta continua com o corte BGE calibrado, sem alteração.

As subseções são as interseções entre `ChunkProvenanceRecord` exato e `CanonicalSpan`
(heading/paragraph/table; separadores são apenas formatação). Usa caminho H1/H2/H3
completo e identidade da ocorrência H1, sem assumir que `ChunkRecord.section_path`
represente todos os spans do chunk. Tamanho é a união dos intervalos canônicos autorizados,
em codepoints, por versão/receita/H1/caminho, deduplicando overlaps. Não depende do
número de chunks, do top-N, da janela ou de scores. Um chunk misto pode contribuir para
duas subseções, cada uma só com sua interseção real. Prefixos de cobertura são resolvidos
por spans, incluindo esse chunk mesmo quando seu metadado inicial é de outra subseção.

Antes de selecionar contexto, largest remainder inteiro divide `count` pelos tamanhos
globais das subseções com fontes ainda não consumidas (3:1 e8=>6:2; empate em ordem
canônica). Cada quota escolhe até essa quantidade de chunks da subseção, deduplicados e
restaurados em ordem de leitura; uma fonte pode sustentar vários itens. Quota0 não consome
chunks. Todas as subseções continuam explícitas em `allocations`, inclusive as de quota0
ou já esgotadas (count0). Ao esgotar uma subseção, os próximos itens são distribuídos
entre as que ainda possuem fonte, sem mudar seus tamanhos canônicos originais. Mudança de
autorização é revalidada e pode reduzir o tamanho autorizado; fonte revogada nunca conta.

`Allocation` mantém `document_version_id`, `section_path`, `count`, `chunk_ids`, e adiciona
`canonical_size`, `canonical_id`, `h1_id` (None no modo consulta). O gerador T021 deve
seguir as quotas e os IDs de fonte por subseção, distinguindo ocorrências H1 quando preciso.
`continuation` v1 mantém binding dono/versões/prefixo/modo e IDs já usados, sem trocar o
binding nem resetar cursores persistidos. O chamador T020 guarda e recupera o cursor no
Postgres sob owner/session; não é token autenticado para entrada direta de usuários.
Nenhuma subseção é perdida por score ou por estar além dos primeiros cinco chunks.

`authorize_points` é a barreira compartilhada com o SafeRetriever existente: PG autoriza
revisões atuais/publicação integral (incluindo acks), depois confere payload contra texto,
owner/versão/seção, evento/revisão, ack específico da chave e hashes da entrada histórica
reproduzida pelo formatter canônico. Texto retornado sempre vem do registro PG autorizado.
Revalida após inferência; T021 ainda precisa revalidar antes de entregar após o LLM.
Nenhum texto de payload alheio/revogado é enviado ao cross-encoder. Revogação posterior a
esta leitura exige a verificação transacional final do orquestrador.

Erros: `ScopeError` para resolução inacessível/ambígua; `ContinuationError` para cursor
incompatível; `EligibilityUnavailable` para PG indisponível; `RetrievalUnavailable` para
Qdrant/embedding/cross-encoder, sem detalhes potencialmente sensíveis do fornecedor.
`context=None`, `chunks=[]` e nenhum candidato selecionado representam conteúdo insuficiente,
sem fabricar um NumberedContext vazio (proibido pelo contrato). Run registra todos os
candidatos autorizados, scores, corte e seleção. Não registra textos recusados.

Calibração inicial: `training_retrieval.reranker.calibrate(path)` executa uma passagem real
em oito pares portugueses (4 positivos, 4 negativos), separados do dataset de avaliação
RAGAS 1.0.1. Exige separação estrita e fixa ponto médio entre maior negativo e menor
positivo; não estima qualidade de produção. Artefato inclui referências, labels e scores.
A prova focal testa armazenamento descartável com dublês de embedding/juiz restritos ao
teste e BGE real; não é prova de embedding Gemini real.

Fontes primárias: [BGE](https://huggingface.co/BAAI/bge-reranker-v2-m3),
[Qdrant hybrid queries](https://qdrant.tech/documentation/search/hybrid-queries/).

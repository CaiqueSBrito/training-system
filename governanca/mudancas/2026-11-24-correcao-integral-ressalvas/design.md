# Design proposto

## Fonte, estrutura e citações

Persistir o texto produzido pelo adaptador antes de chunking, com hash e receita canônica. A receita define ordem, títulos e `\n\n` entre blocos; separadores são spans explícitos sem página atribuída. Capturar todos os itens de proveniência disponíveis no Docling, não apenas `prov[0]`. Quando o elemento multipágina não expõe correspondência entre substring e página, **não** distribuir caracteres proporcionalmente: refinar a extração por segmentos verificáveis; se ainda impossível, marcar proveniência unknown para o intervalo afetado. Uma citação pode retornar múltiplas páginas exatas ou informação insuficiente, nunca uma certeza falsa.

Chunk é fatia contígua do canônico. H1 é limite duro por identidade da seção, inclusive títulos repetidos; H2/H3 são metadados e candidatos a corte perto do alvo, sem flush obrigatório. Caminhos são extraídos dos spans intersectados; `section_path` singular antigo não representa todas as subseções. Preservar tabela como unidade segundo ADR-0009; tabela maior que teto conserva recusa explícita existente e não se confunde com parágrafo sem espaços.

Fragmentar parágrafo longo por codepoints quando não há espaço; o estimador atual ceil(bytes UTF-8/4) permanece declarado e versionado, sem fingir tokenizer Gemini. Calcular orçamento com separadores e sobreposição. Não reencodar com errors=ignore, normalizar texto, remover controles ou substituir codepoints no texto canônico. Para limites configurados que nem comportem um codepoint, recusar configuração antes da ingestão. Sobreposição de um parágrafo mantém regra existente quando cabe; parágrafo superdimensionado exige sobreposição de fragmento explicitamente versionada e aprovada no G1/G3, com intervalos que permitem reconstrução única sem duplicação. Não reduzir alvo/teto silenciosamente.

## Conversor

Uma instância de adaptador possui conversor lazy reutilizável e um lock que cobre construção e conversão completas. Um worker pode ter sua instância; processos distintos nunca compartilham objeto Docling. Não usar singleton global sem sincronização. A opção conservadora serializa parse por instância; paralelismo vem de processos já existentes, sem dependência nova. Testar inicialização concorrente e contaminação entre documentos diferentes, usando barreiras de teste e fixtures reais.

## Protocolo durável sem transação remota

1. Ler arquivo e SHA fora de transação. Transação curta faz INSERT/claim UNIQUE(owner,sha), atribui version_id estável e generation monotônica; conflito retorna busy ou versão ready. Antes de reaproveitar versão antiga, avaliar elegibilidade e reparo.
2. Commit da reserva. Parse/chunking fora de transação. Heartbeats são transações próprias curtas; não manter conexão com BEGIN durante CPU, juiz, embedding ou Qdrant.
3. Transação curta valida generation e persiste canônico/spans, versão/chunks imutáveis e entradas de embedding; cria trabalho durável de avaliação/indexação. Em falha, rollback apenas desta unidade; reserva mantém erro/retomada. Retry antes de persistência pode refazer parse; depois usa artefato persistido.
4. Triagem determinística e juiz fora de transação; anexar eventos com CAS e correlação. Registrar suspeito mesmo se liberado; erro do juiz deixa indisponível e cria retomada controlada. Heurística de alta precisão pode determinar bloqueio, mas a trilha registra o resultado real do juiz quando chamado, inclusive divergência.
5. Job durável lê decisões e entrada exata, chama embedding/Qdrant fora de transação; confirma operação com wait e verifica em lote; transação curta grava avanço/ack só para generation/revisão vigentes. Se processo cair após upsert antes de ack, repetir é idempotente.
6. Publicar ready só com artefatos e decisões completos e confirmação da revisão indexada. Worker vencido não faz publicação. Qdrant não suporta CAS equivalente ao Postgres: serializar job por chave e revalidar geração antes/depois reduz a corrida, mas a segurança final exige checagem autoritativa no consumidor. Payload é cache, não autoridade de liberação.

Não há promessa de atomicidade distribuída. Ponto pré-publicação, stale, rejeitado ou de outro dono jamais é elegível a E5: buscar IDs com filtro de dono, validar readiness/decisão atual na linhagem antes de carregar/devolver texto ao modelo. Uma nova decisão bloqueante invalida imediatamente a projeção de leitura; trabalho de remoção do derivado é retomável. Falha do Postgres impede autorizar candidatos. Um processo antigo que escreva payload stale não pode reautorizar conteúdo.

## Reconciliação, isolamento e lotes

Reconciliador implementado de verdade após gates, separado da verificação simples do backend paralelo. A fonte é Postgres: documento/chunk, título e contexto de embedding persistidos, entrada/hash/formato/chave e decisão efetiva. Se entrada histórica não for reconstruível com prova, marcar blocked/legacy_insufficient; não gerar vetor com título vazio inventado. Usar o mesmo formatter já existente de `embed_document`, cuja receita é identificada por hash/versão, e validar coleção exata, metadados, dimensão e BM25 português.

Pontos permitidos ausentes/divergentes recebem upsert determinístico; rejeitados ficam inelegíveis de imediato e sua projeção vetorial é retirada por job escopado. Essa retirada é correção de índice derivado autorizada na tarefa futura, preservando fonte/história; não é autorização para limpar órfãos desconhecidos. Nunca usar delete por versão sem filtro de dono. Payload mínimo inclui identidade e revisão; evitar copiar auditoria sensível.

Índices payload: owner_id, document_version_id, chunk_id e embedding_key como keyword, revision como integer se filtrada. Verificação faz retrieve/scroll paginado com IDs em lotes limitados (padrão proposto 128, configurável), sem vetores quando desnecessários, e confere todos os pontos, payload, ausência de rejeitados e dono. Filtragem de dono no Qdrant e conferência do payload/linhagem são obrigatórias; retrieve por ID sem suporte a filtro exige consulta filtrada equivalente, não confiar apenas no UUID. Nunca comparar só counts ou só o primeiro lote.

Remover `allow_test_collection` do construtor e de qualquer caminho do produto. Testes usam instância Qdrant descartável isolada em que coleção tem o nome exato da embedding_key, sem bypass nem nova dependência. Alterações de casos G3 existentes necessitam gate próprio e não são feitas nesta preparação.

## Limite desta entrega

Não aplica migration, não altera modelos, core.py, schema lineage ou indexador. Correções paralelas em llm/migration.py/indexing.py conservam dono e autorização próprios; T-011/T-014 só integram após entrega e rebase do principal. Concluir código não equivale cumprir G4: RAGAS e causa histórica têm estados separados no relatório.

# training-ingestion — I1–I6

Recebe caminho local e dono, parseia PDF/DOCX/Markdown com Docling local e grava
`DocumentVersionRecord`/`ChunkRecord` no Postgres pelo repositório de linhagem.
Verifica injeção, gera embeddings e indexa no Qdrant antes de persistir a versão.

```python
from training_lineage import create_lineage_engine
from training_ingestion import ingest_document, ChunkConfig

result = ingest_document("material.pdf", "owner-id", engine=create_lineage_engine(),
    config=ChunkConfig(target_tokens=800, max_tokens=1500))
```

O schema deve estar migrado pelo `training_lineage.migrate`. A ingestão usa uma
transação atômica e uma trava consultiva transacional por dono/hash. Reenvios
retornam a versão existente e seus chunks sem parsear. Outro dono ganha outra
versão. A validação de extensão acontece antes da leitura ou gravação; arquivos
vazios e acima de `max_file_bytes` (padrão 100 MiB) também são recusados.

O parse usa os bytes já hasheados; nunca relê o caminho. Cada `DoclingParser`
possui um conversor lazy, reutilizado com um lock que cobre construção, conversão
completa e extração dos contratos. Instâncias distintas são independentes. PDF
usa modelos locais e inferência de hierarquia de títulos; não há enriquecimento
remoto. Erros públicos de parse não encadeiam exceções que possam expor o arquivo.

## Proveniência e chunking

`parse(data, filename, document_version_id)` retorna `ParsedDocument`, agregado de
`CanonicalTextRecord`, `tuple[CanonicalSpan, ...]` e `title`. Não há `Block` antigo.
`.text` é o texto do registro canônico. O adaptador mantém exatamente o texto dos
itens Docling na ordem de leitura, com tabelas exportadas em Markdown e `\n\n`
entre elementos. Cada separador tem span próprio, página nula e origem unknown.
O hash é SHA-256 dos bytes UTF-8, sem normalização posterior; offsets medem
codepoints Python em intervalos semiabertos. A receita canônica v2 identifica
ordem, texto sem strip, tabela e separadores; a versão do parser é independente.

Todos os `prov` são examinados. Em texto PDF, charspans válidos e com uma única
página por intervalo fornecem páginas exatas. Lacunas, intervalos inválidos e
sobreposição de páginas ficam unknown, sem distribuição proporcional. Em tabela
multipágina o Markdown não permite mapear substrings às células: página nula e
unknown; tabela de página única conserva essa página factual. MD/DOCX não têm
paginação fixa e usam página nula. `ChunkRecord.page` e `section_path` são campos
históricos da primeira origem; consultas novas devem usar o mapa inteiro e os
spans, inclusive todos os caminhos H2/H3, via contratos de resolução de citações.

`Chunker.split_with_provenance(parsed, document_version_id)` retorna
`(list[ChunkRecord], tuple[ChunkProvenanceRecord, ...])`. `split` retorna a lista
de chunks. A versão precisa coincidir com o registro canônico. Cada chunk é uma
fatia contínua exata, e seu mapa lista todos os spans intersectados. H1 corta por
identidade da ocorrência, mesmo com título repetido; H2/H3 pequenos não forçam
flush. Fronteiras de blocos e fragmentos são usadas perto do alvo. Tabelas ficam
inteiras e acima do teto produzem `tabela_excede_teto`.

A política versionada repete o último parágrafo ou fragmento apenas quando há
mais de uma unidade no chunk anterior, quando o novo chunk avança e quando cabem
o overlap e o conteúdo novo. `overlap_tokens` limita a repetição (padrão metade
do teto; zero desativa). Cada duplicação é declarada em `overlap_ranges`; descontar
esses intervalos reconstrói exatamente o SHA original. Separadores entram em
todos os orçamentos. Tabelas e títulos não são repetidos por overlap. Parágrafos
longos são fragmentados perto de metade do alvo, limitados por metade do teto; os cortes preferem espaços e usam codepoints quando eles faltam.
URL, base64, CJK, emoji e marcas combinantes não são recusados por palavra longa.
Não se corta UTF-8 nem se usa `errors=ignore`.

Contagem: estimador versionado `ceil(bytes_UTF8 / 4)`, sem tokenizer Gemini. Alvo
800 e teto 1500 são configuráveis e medidos nesse estimador. O teto mínimo de dois tokens comporta
um codepoint UTF-8 de quatro bytes junto ao separador LF duplo. Teto menor, zero,
tipos inválidos e overlap >= teto são recusados na configuração. Receita, estimador, alvo, teto e overlap compõem `chunker_version`.
O SHA-256 de identidade inclui versão do documento/canônico, hash da fonte,
receita do chunker, offsets e conteúdo. Mesma fonte e configuração mantêm os IDs.

A integração durável T013 deve reservar o version_id antes do parse, executar
parse/chunking fora de transação e persistir `parsed.canonical`, `parsed.spans`,
chunks e mapas juntos em transação curta. T012 fornece os artefatos reais; o
protocolo transacional de Ingestor e a persistência são escopos de T013/T011.

## Fixtures e testes

Fixtures sintéticas pequenas e versionadas: PDF vetorial com duas páginas,
títulos e tabelas; DOCX OOXML com estilos e tabela; Markdown com títulos e tabela.
Nenhum dado de pessoa é usado. O gerador usa somente biblioteca padrão, com datas
fixas no ZIP e PDF sem timestamps, para reprodução byte a byte:

```sh
uv run python packages/ingestion/tests/fixtures/generate.py
uv sync --all-packages
uv run pytest packages/ingestion
uv run validar-governanca
```

Os testes usam Docling real e um banco temporário exclusivo criado no Postgres
local configurado pelo `.env` da aplicação. Criam/migram/descartam somente esse
banco temporário; não limpam tabelas da aplicação. Ausência de banco ou de modelos
é falha de execução, nunca skip apresentado como prova.

## I4–I6 (T-008)

A entrada completa é `training_ingestion.ingest_document(path, owner_id,
engine=create_lineage_engine())`. Ela lê `.env` sem executar shell, valida a chave
`gemini-embedding-2@3072@fmt-v1` e a configuração da coleção na inicialização e
executa I1–I6. `Ingestor` exige `processor` I4–I6. O caminho parcial é interno
(`core._PartialIngestor`), reservado a testes das etapas I1–I3;
a entrada completa recusa reutilizar versões parciais ou divergentes verificando
cada ponto contra a linhagem, pois não existe coluna de estado no contrato atual.

A heurística examina título, section_path e texto de cada chunk. Somente delimitadores inequívocos de modelo (`<|im_start|>`, `<|im_end|>`,
`<start_of_turn>`, `<end_of_turn>`, `[INST]`, `<<SYS>>`, `<|system|>`,
`<|assistant|>`), tags Unicode U+E0000–E007F e controles bidi U+202A–E/U+2066–9
são sinais de alta precisão e mantêm a marcação mesmo quando o juiz discorda.
Menções a juiz/checker/classifier, labels user/root/model, `<system_error>` e Cf
genéricos (como soft hyphen) são apenas suspeitas: o juiz pode liberá-las. Escrita mista
latina/cirílica/grega, preenchedores invisíveis e ordens PT/EN também são suspeitos. Somente suspeitos passam
pelo juiz LiteLLM (`INGESTION_JUDGE_MODEL`, ou `INPUT_JUDGE_MODEL`). Falha do juiz
interrompe a transação. Confirmados ficam na linhagem, com motivo, sem ponto no
Qdrant; os demais recebem uma chamada individual ao Gemini. O título é o primeiro título de nível 1 extraído pelo Docling,
com nome base do arquivo como fallback e o caminho de seção usa ` > `.

O helper público `bm25_document(text)` é compartilhado pela indexação e pela
futura recuperação. A coleção deve se chamar exatamente pela chave configurada,
sem exceção para testes. Os testes de indexação usam um servidor Qdrant real
descartável (mesma imagem instalada), sem volumes, com porta aleatória em loopback,
e nunca limpam a coleção compartilhada. BM25 é calculado no servidor por `Document(model="qdrant/bm25",
options={"language": "portuguese"})`, com IDF na coleção. O idioma é registrado
nos metadados e validado junto do tamanho/cosseno. O SHA-256 do chunk é convertido
por UUIDv5 para o ID do Qdrant e preservado integralmente no payload.

A inicialização cria/verifica índices de payload keyword para document_version_id,
owner_id e chunk_id e bool para injection_flagged, estado efetivo hoje existente.
Tipo divergente é recusado. Reuso verifica lotes de até 128 IDs, incluindo chunks
marcados: valida presença/ausência, ID, payload completo, dono/chave e estrutura
dense 3072 finita/não nula e BM25 finito. Não existe coluna de estado de finalização
no contrato atual; nenhum novo campo foi inventado nesta slice.

A versão só é gravada definitivamente no commit do Postgres, após indexação.
Falhas comuns compensam os pontos da versão no Qdrant; não existe transação
atômica entre os dois bancos. Encerramento abrupto ou falha da compensação exige
reconciliação de pontos órfãos com a linhagem antes de disponibilizá-los na futura
recuperação. Chunks da linhagem são imutáveis e nunca atualizados após criação.

Sem `GEMINI_API_KEY`, a entrada completa falha explicitamente. Nos testes, somente
chamadas reais ao provedor são puladas como `sem GEMINI_API_KEY`; dublês limitados
a arquivos de teste verificam o contrato do SDK e os fluxos de erro, e não provam
a qualidade/disponibilidade do modelo.


O modelo do juiz deve ser configurado, sem padrão em código. O prefixo `gemini/`
usa exclusivamente `GEMINI_API_KEY`; `openrouter/` usa `OPENROUTER_API_KEY`;
outros prefixos são recusados. O juiz usa `json_schema` estrito e `reason` tem
limite de 512 caracteres. O motivo armazenado continua sendo dado não confiável:
não deve ser executado, interpretado como instrução ou incluído em logs.
Falhas de chamada ou validação são expostas como `Falha na verificação de injeção`,
sem causa encadeada nem `input_value`. `litellm.suppress_debug_info = True` reduz diagnósticos adicionais do provedor,
mas não desativa logs DEBUG (ver os limites abaixo).


## Limites e diagnóstico após revisão r2

A triagem amplia variantes PT/EN, negação de seguir instruções, tarefas novas,
repetição de texto anterior, obediência exclusiva, homoglifos pontuais e soletração
espaçada. A normalização serve só à triagem; o original segue intacto ao juiz.
Não há promessa de prevenção universal. Conteúdo acadêmico normal não é excluído
automaticamente por mencionar juiz, prompts ou ferramentas. A razão gravada é o
controle/delimitador que forçou a marcação ou o motivo não confiável do juiz que
confirmou a injeção; uma liberação pelo juiz não vira motivo de bloqueio.

`InjectionJudgeError.kind` distingue `provider` (chamada falhou), `invalid_output`
(saída inválida) e `unsafe_logging` (recusa antes do SDK), sem incluir dados,
chaves, resposta ou prompt na exceção exposta. Quando o provedor fornece um status
HTTP inteiro válido, status_code é preservado e retryable indica somente os códigos
408/425/429/500/502/503/504; sem status comprovável, ambos ficam nulos.
Em unsafe_logging, setting contém somente o nome constante do controle recusado,
nunca seus valores, configurações ou ambiente.
A saída antiga do teste instável não contém traceback; não comprova 429, 5xx nem
timeout. Nenhum retry foi acrescentado. `LITELLM_LOG=DEBUG` é inseguro em produção
e agora causa recusa técnica antes do import/chamada sensível, assim como OPENAI_LOG
debug, root/SDK/transportes em DEBUG, set_verbose, logs brutos, traceback de cliente
e callbacks de logging. Há nova checagem antes de cada chamada, inclusive embeddings.
O callback interno literal cache registrado pelo LiteLLM ao importar Gemini é
inerte quando litellm.cache é nulo; somente esse caso é permitido. Cache ativo e
callbacks arbitrários continuam recusados, sem relaxar nenhuma proteção DEBUG.
Configuração segura do LiteLLM usa turn_off_message_logging, redação de exceções e
chaves, suppress_debug_info e no-log por requisição. Filtros suprimem registros dos
loggers SDK suportados nos handlers existentes, inclusive ERROR. Isso não promete
controlar logs arbitrários do chamador nem reconfiguração concorrente do processo;
o host deve manter estável a configuração durante a chamada.

O teste real ponta a ponta remove em `finally` somente pontos com o
`document_version_id` criado pela própria execução, mesmo após assert falhar,
e verifica a ausência desses pontos. Não remove órfãos anteriores ou dados humanos.

T012 substitui o flush obrigatório em H2/H3 e a recusa de palavra longa. Os
casos novos offline estão em `test_ressalvas_parse.py`, com arquivos equivalentes
MD/DOCX/PDF construídos em memória e prova real de concorrência/reuso do Docling.
Os testes de contrato ingestion antigos foram migrados conforme autorização do
coordenador; casos congelados T001 não foram alterados. RAGAS continua Bloqueado
sem dataset, baseline e limiares; unitários não comprovam qualidade de recuperação.
Versões já marcadas são imutáveis: a correção não reclassifica nem altera registros
anteriores, que exigem uma futura decisão explícita de reconciliação/reprocessamento.

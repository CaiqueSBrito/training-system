# Delta — Ingestão de documentos

Spec afetada: `governanca/specs/ingestao/spec.md`.

## ADICIONA

### ING-FMT-001 — Formatos aceitos
O sistema DEVE ingerir arquivos PDF, DOCX e Markdown e, SE receber outro formato, recusá-lo com motivo sem gravar nada. (G1 2026-11-24-ingestao; ADR-0009)

- Cenário: Dado um arquivo `.xlsx`, Quando enviado à ingestão, Então é recusado com motivo `formato_nao_suportado` e nenhuma versão de documento é criada.

### ING-CHK-002 — Tamanho e sobreposição dos chunks
O sistema DEVE gerar chunks com alvo e teto de tamanho configuráveis, com padrões de ~800 e ~1500 tokens, e sobreposição de 1 parágrafo entre chunks vizinhos da mesma seção. (G1 2026-11-24-ingestao; §3.1)

- Cenário: Dada uma seção longa sem tabelas, Quando dividida, Então nenhum chunk passa do teto e o último parágrafo de cada chunk é o primeiro do seguinte.

### ING-INJ-002 — Detecção de injeção em duas camadas
QUANDO um chunk for ingerido, o sistema DEVE aplicar a heurística determinística de injeção e submeter ao juiz LLM só os chunks que ela marcar como suspeitos. (G1 2026-11-24-ingestao)

- Cenário: Dados 100 chunks dos quais 3 são suspeitos pela heurística, Quando ingeridos, Então há exatamente 3 chamadas ao juiz de injeção.

### ING-LIN-001 — Linhagem da ingestão no Postgres
QUANDO uma versão de documento e seus chunks forem processados, o sistema DEVE gravá-los no Postgres da aplicação seguindo os registros `DocumentVersionRecord` e `ChunkRecord` de `packages/contracts`. (ADR-0008; §7)

- Cenário: Dado um documento ingerido, Quando a linhagem é lida do Postgres, Então os registros validam contra os contratos e cada chunk aponta para a sua versão de documento.

## MODIFICA

## REMOVE

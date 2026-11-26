# Revisão independente (Google) — T-012 (`2026-11-24-correcao-integral-ressalvas`)

- **Tarefa**: T-012 (`2026-11-24-correcao-integral-ressalvas`) — Corrigir parse estrutural e fragmentação Unicode
- **Requisitos avaliados no escopo de parse/chunking**: `ING-PRO-001`, `ING-CAN-001`, `ING-EST-001`, `ING-UNI-001`, `ING-PAR-001` (Casos `C03`, `C04`, `C05`, `C06`, `C08`)
- **Provedor efetivo do autor (mantido)**: `openai` (`rag-backend`, dispatch `ctx_def9752fed18`, task `task_628057460054`)
- **Provedor efetivo da revisão**: `google` (Antigravity / Gemini 4 Argon; dispatch `ctx_e6cba7e08ddd`, task `task_16073a6ada56`)
- **Veredito para T-012**: **aprovado** (no escopo estrito de `DoclingParser`, `ParsedDocument`, `ChunkConfig`, `Chunker`, migração de `document_version_id` antes do parse em `_PartialIngestor`, atualização autorizada dos testes de ingestão/indexação e casos `C03`–`C06`/`C08` em memória; **não** aprova o protocolo transacional durável de `T-013`, a persistência relacional de `T-011`, o lote de 16 ressalvas nem o Gate G4 humano).

---

## 1. Escopo e conformidade de governança

1. **Gates e autorizações respeitados (`governanca/mudancas/2026-11-24-correcao-integral-ressalvas/tarefas/T-012.md:11-13,29-37,57-63`, `evidencias/T-012/implementacao.md:7-10`)**:
   - G1 e G2 aprovados pelo humano (`aprovo_proposta_contratos_e_execucao_T010_T017`); G3 dispensado (`dispenso_G3_T010_T017`).
   - Casos G3 congelados de `T-001` permanecem intocados (`test_classificacao.py` `b15e416b...`, `test_plano.py` `c897e706...`, `test_itens.py` `1623c0e1...`).
   - Atualização de `packages/ingestion/tests/test_ingestion.py` e da fábrica de parser em `packages/ingestion/tests/test_indexing.py:133,141-145` (que tinham G3 dispensado em `T-003`–`T-008`) foi expressamente autorizada pelo coordenador (`msg_1438e2937a1c`), assim como a remoção do export do dataclass antigo `Block` em `packages/ingestion/src/training_ingestion/__init__.py:2-10` após o corte limpo para `ParsedDocument` (`msg_ba850c19ee0e`).
2. **Rastreabilidade honesta de evidências (`evidencias/T-012/pytest-focal.txt`, `evidencias/T-012/pytest-correcao-focal.txt`, `evidencias/T-012/validador.txt`)**:
   - A falha inicial focal (`4 failed, 45 passed in 18.64s`) foi integralmente preservada em `pytest-focal.txt` sem sobrescrita.
   - As duas causas da falha inicial (ausência de `Override PartName="/word/styles.xml"` na fixture DOCX sintética em memória em `test_ressalvas_parse.py:146` e configuração `max_tokens=1` incapaz de comportar codepoint de 4 bytes + separador `\n\n` sem produzir chunk só branco proibido por `ChunkRecord`) foram tratadas com validação explícita `max_tokens >= 2` em `ChunkConfig.__post_init__` (`core.py:60-61`), sem afrouxar contratos nem substituir Docling por dublê.
   - A repetição focal planejada (`pytest-correcao-focal.txt`: `23 passed, 8 deselected in 6.81s`) e o validador (`validador.txt`: `governança OK: 5 mudança(s), 17 tarefa(s), 28 requisito(s) na spec viva`) confirmam o fechamento determinístico do escopo de `T-012`.
   - Conforme instrução do despacho, nenhum teste, suíte, build, lint ou chamada de API foi reexecutado nesta revisão e nenhuma credencial foi lida.

---

## 2. Análise técnica por invariante (`arquivo:linha`)

### 2.1. Proveniência por `charspan` completo sem páginas estimadas (`ING-PRO-001` / `C03`)

- **Inspeção de todos os itens `prov` (`packages/ingestion/src/training_ingestion/core.py:114-141`)**:
  - `DoclingParser._page_segments` não usa apenas `prov[0]` nem distribui caracteres proporcionalmente.
  - Para formatos não-PDF (`not is_pdf`) ou sem `item.prov`, retorna `[(0, len(value), None)]` (`:121-122`), que recebe `provenance='unknown'` e `page=None` (`:152-155`).
  - Para tabelas em PDF (`is_table`, `:123-125`), extrai `pages = {p.page_no for p in item.prov}` e só atribui página quando `len(pages) == 1`; tabelas multipágina em Markdown recebem `page=None` (`provenance='unknown'`), evitando inventar coordenadas de células inexistentes no Markdown exportado.
  - Para texto em PDF (`:126-141`), valida cada `prov.charspan` contra `0 <= start < end <= len(value)` (qualquer `charspan` inválido invalida o mapa do item para `None` / `unknown`, `:128-130`), particiona `[0, len(value))` pelos cortes elementares `cuts` (`:132-136`), atribui a página única quando `len(pages) == 1` ou `None` em lacunas e sobreposições ambíguas (`:135-136`), e funde segmentos contíguos de mesma página (`:137-140`).
- **Verificação em testes (`packages/ingestion/tests/test_ressalvas_parse.py:99-120,164-175`)**:
  - `test_C03_all_docling_charspans_and_unknown_not_proportional` exercita `DoclingDocument` com `charspan` cruzando páginas 2 e 3 (`[(0,7,2), (7,11,3)]`), resolução via `resolve_citation_spans` de `T-010` para trecho só na página 3 (`{3}`), só na 2 (`{2}`), cruzado (`{2, 3}`), sobreposição ambígua (`unknown`), lacuna intermediária (`[(0,4,2), (4,7,None), (7,11,3)]`) e DOCX sem página (`page is None`).

### 2.2. Texto canônico UTF-8, offsets em codepoints, separadores e hashes (`ING-CAN-001` / `C04`)

- **Construção e validação do canônico (`core.py:30-43,76,143-195`)**:
  - `DoclingParser._adapt` preserva o texto exato de cada item Docling sem `strip` (`:160,164,183`), inserindo separadores explícitos `'\n\n'` com span próprio `kind='separator'`, `page=None`, `section_path=()`, `h1_id=None` e `provenance='unknown'` (`:179-182`).
  - `canonical_id` é derivado deterministicamente de `[document_version_id, self.canonical_version, self.version]` (`:148-149`), e `CanonicalTextRecord` recebe `text_sha256=sha256(text.encode()).hexdigest()` em UTF-8 (`:191-194`).
  - `ParsedDocument.__post_init__` invoca `validate_canonical_spans(self.canonical, self.spans)` (`:37-38`), garantindo partição contígua e exata `[0, len(text))` em codepoints já na saída do parser.
- **Validação cruzada em cada chunk emitido (`core.py:267-288`)**:
  - `emit` constrói `ChunkRecord` com `start_offset=start`, `end_offset=end`, `text=document.text[start:end]` e identidade SHA-256 determinística (`:271-278`), além de `ChunkProvenanceRecord` com todos os `span_ids` intersectados e `overlap_ranges` (`:279-284`), chamando `validate_chunk_provenance(document.canonical, document.spans, chunk, mapping)` (`:285`) antes de retornar.

### 2.3. H1 por ocorrência, agrupamento de H2/H3 sem metadados falsos e atomicidade de tabelas (`ING-EST-001` / `C05`)

- **Identidade de ocorrência H1 e hierarquia H2/H3 (`core.py:169-178,258-264`)**:
  - Cada título de nível 1 (`TitleItem` ou `SectionHeaderItem` com nível normalizado 1, incluindo compensação de `.md`/`.markdown` em `:170-171`) incrementa `h1_number += 1` e gera `h1_id = f'h1:{h1_number}'` (`:174-176`). Títulos H1 repetidos (ex.: dois `# A`) recebem `h1:1` e `h1:2` distintos.
  - Em `split_with_provenance` (`:258-264`), as fronteiras duras de seção (`boundaries`) são cortadas exclusivamente por mudança de `span.h1_id`, nunca por igualdade de string do título nem por transição em H2/H3.
  - Subseções H2/H3 permanecem agrupáveis no mesmo chunk enquanto couberem no orçamento (`:289-305`), e todos os seus spans e `section_path` (`tuple(t for _, t in titles)`, `:186`) ficam registrados em `ChunkProvenanceRecord.span_ids` (`:269,282`), sem fabricar metadados e mantendo `ChunkRecord.page` e `ChunkRecord.section_path` como referência histórica do primeiro span não-separador (`:270,277`).
- **Tabelas indivisíveis (`core.py:229-230,254-256`)**:
  - Qualquer span `kind == 'table'` com `count_tokens(...) > self.config.max_tokens` levanta `IngestionError('tabela_excede_teto: tabela não é cortada')` antes de emitir chunks (`:254-256`); tabelas dentro do teto entram como unidade atômica em `_units` (`:229-230`) e não são repetidas como overlap (`:299`, que restringe overlap a `{'paragraph', 'fragment'}`).
- **Verificação em testes (`test_ressalvas_parse.py:66-92,164-175,234-241`, `test_ingestion.py:200-208`)**:
  - `test_C05_paths_h1_occurrences_table_and_separators` e `test_C05_real_repeated_H1_H2_H3` comprovam que dois `# A` distintos nunca se misturam (`len(chunks) == 2`, `{'h1:1', 'h1:2'}`) enquanto `## Small` e `### Tiny` sob o primeiro `# A` permanecem agrupados com todos os `section_path` preservados nos spans do mapa.

### 2.4. Fragmentação Unicode por codepoints, teto e overlap declarado (`ING-UNI-001` / `C06`)

- **Corte binário seguro em codepoints e preferência por espaço (`core.py:45-71,203-245`)**:
  - `ChunkConfig` valida inteiros estritos (`type(...) is not int`, rejeitando `bool`), `0 < target_tokens <= max_tokens`, `0 <= overlap_tokens < max_tokens` e `max_tokens >= 2` (`:51-61`).
  - `Chunker._byte_end` (`:203-213`) faz busca binária sobre índices de codepoints Python testando `len(text[start:middle].encode('utf-8')) <= budget`, garantindo que nenhum codepoint multibyte (CJK, emoji fora do BMP, sequência combinante NFD) seja partido ao meio e sem usar `errors='ignore'` ou normalização Unicode.
  - Quando o span excede o orçamento, `_units` (`:232-244`) fatia por `_byte_end` e, se houver espaço em branco dentro da fatia (`re.finditer(r'\s+', document.text[cursor:stop])`), recua para o fim do último espaço (`:239-242`); caso contrário (URLs longas, base64, CJK contínuo, emojis contínuos), corta diretamente na fronteira de codepoint e rotula a unidade como `'fragment'`.
  - Os separadores `'separator'` são anexados à unidade anterior quando cabem em `max_tokens` (`:223-227`), entrando integralmente no cálculo de orçamento e teto.
- **Overlap versionado e reconstrução sem perda (`core.py:279-284,293-303`, `test_ressalvas_parse.py:37-64,224-231`)**:
  - Após emitir `pending`, o próximo chunk só retém `[previous]` (`pending[-1]`) se `len(pending) > 1`, `previous[2] in {'paragraph', 'fragment'}`, `count_tokens(previous) <= overlap_limit` e `count_tokens(previous..unit) <= max_tokens` (`:297-302`).
  - O intervalo exato repetido é declarado em `ChunkProvenanceRecord.overlap_ranges` como `(TextRange(start=start, end=overlap_end),)` onde `overlap_end = min(end, result[-1].end_offset)` (`:279,283`).
  - `assert_reconstruction` (`test_ressalvas_parse.py:37-51`) valida em `test_C06_codepoints_lossless_and_declared_overlap` (para URL de 7000 chars, base64 de 6800 chars, CJK de 6000 chars, 3000 emojis fora do BMP, 3000 sequências NFD `e\u0301` e texto com espaços, sob `ChunkConfig(40, 60)`, `ChunkConfig(1, 2)` e `ChunkConfig(70, 100)`) e em `test_C06_overlap_zero_and_tiny_budget_separators` que descontar `overlap_ranges` reconstrói exatamente o texto canônico e seu `text_sha256`, respeitando `count_tokens(c.text) <= config.max_tokens` e avanço estrito de offsets.

### 2.5. Conversor Docling lazy protegido por lock por instância (`ING-PAR-001` / `C08`)

- **Isolamento por instância e exclusão mútua (`core.py:73-112`)**:
  - `DoclingParser.__init__` instancia `self._lock = Lock()` e `self._converter = None` por objeto (`:78-80`), sem singleton global.
  - Em `parse` (`:97-111`), o bloco `with self._lock:` cobre a inicialização lazy (`if self._converter is None: self._converter = self._build_converter()`), a execução de `self._converter.convert(DocumentStream(...))`, a checagem de `ConversionStatus.SUCCESS` e toda a extração em `self._adapt(...)` (`:100-106`).
  - Exceções internas do Docling são capturadas e relançadas como `raise IngestionError(f"falha_no_parse: {type(exc).__name__}") from None` (`:109-111`), evitando vazar conteúdo do documento em tracebacks.
- **Verificação concorrente (`test_ressalvas_parse.py:178-222`)**:
  - `test_C08_real_threads_lazy_reuse_isolated` executa 2 threads sincronizadas por `Barrier(2)` sobre `md`, `docx` e `pdf` reais tanto com instância compartilhada (`shared=True`) quanto com duas instâncias distintas (`shared=False`), comprovando: (a) exatamente 1 construção `_build_converter` por instância (`set(counts.values()) == {1}`); (b) nunca mais de 1 `convert` ativo simultaneamente na mesma instância (`assert active[parser] == 1`); e (c) ausência total de contaminação entre `Unique0` e `Unique1`.

### 2.6. Fronteira entre T-012 e T-011/T-013 (`core.py:349-373`)

- Em `_PartialIngestor._ingest_transaction` (`core.py:362-365`), `record = DocumentVersionRecord(...)` agora é instanciado antes de `self.parser.parse(data, filename, record.document_version_id)` e `self.chunker.split(parsed, record.document_version_id)`, satisfazendo a assinatura que vincula `CanonicalTextRecord.document_version_id` à versão do documento.
- Conforme documentado em `evidencias/T-012/implementacao.md:13-18,38` e nas instruções do despacho, o protocolo de reserva durável fora de transação aberta, fencing/CAS e persistência transacional curta de `(canonical, spans, chunks, mappings)` pertencem a `T-013` (integrado ao repositório de `T-011`) e **não** fazem parte da aprovação de `T-012`.

---

## 3. Achados e limites

- **Achados bloqueantes ou de retrabalho em T-012**: **Nenhum (0)**.
- **Limites explícitos desta aprovação**:
  1. Esta revisão aprova **exclusivamente T-012** (`DoclingParser`, `ParsedDocument`, `ChunkConfig`, `Chunker`, remoção do export `Block` em `__init__.py`, documentação de parse/chunking em `README.md`, novos testes `test_ressalvas_parse.py` e migração autorizada de `test_ingestion.py` e `test_indexing.py`).
  2. Não aprova a persistência de canônico/spans/auditoria em banco (`T-011`), o protocolo transacional curto com reserva/fencing (`T-013`), a reconciliação e leitura autoritativa Qdrant/Postgres (`T-014`/`T-016`), a avaliação dos 9 ataques com juiz real (`T-015`) nem a prova integrada final e diff RAGAS (`T-017`).
  3. Não declara encerradas as 16 ressalvas da mudança e não substitui o Gate G4 humano.

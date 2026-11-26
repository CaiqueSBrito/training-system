# T-012 — parse, proveniência, Unicode e reuso

Executor: OpenAI/Codex; task `task_628057460054`, dispatch `ctx_def9752fed18`, terminal `term_610ae041-9565-4033-b250-10880559b3e5`. Implementação não equivale à revisão Google nem ao G4 humano.

## Autorização e sequência

G1/G2: `aprovo_proposta_contratos_e_execucao_T010_T017`. G3: `dispenso_G3_T010_T017`. Contratos T010 presentes e importados diretamente, sem modelo alternativo ou fallback. Arquivo novo de testes e migrações dos consumidores de teste foram escritos antes de substituir o código de produto; nenhum teste/build/lint intermediário. Validador de retomada executado conforme AGENTS/pipeline, antes do trabalho, além do validador final exigido pelo dispatch.

O coordenador ampliou expressamente o escopo em `msg_1438e2937a1c` a `test_ingestion.py` e construções de parser em `test_indexing.py`: G3 antigo T003–T008 foi dispensado, sem hashes congelados; os casos congelados T001 não foram alterados. Regressões úteis permanecem: recusa de palavra longa virou reconstrução e teto, flush H2 virou mapa multisseção completo; testes de offsets, identidade, overlap, tabelas, páginas e segurança foram preservados/migrados. `__init__.py` teve somente a remoção do import/export de Block para não quebrar o import do pacote depois do corte limpo; coordenador notificado em `msg_ba850c19ee0e`.

## Interfaces concretas para T013

- `DoclingParser.parse(data: bytes, filename: str, document_version_id: str) -> ParsedDocument`.
- `ParsedDocument(canonical: CanonicalTextRecord, spans: tuple[CanonicalSpan, ...], title: str | None)`; `.text` expõe `canonical.text`; construtor valida partição inteira.
- `Chunker.split_with_provenance(parsed, document_version_id) -> tuple[list[ChunkRecord], tuple[ChunkProvenanceRecord, ...]]`.
- `Chunker.split(...) -> list[ChunkRecord]`, consumindo o mesmo caminho completo.
- T013 deve reservar versão antes do parse, parsear fora de transação e gravar `canonical`, `spans`, `chunks`, `mappings` juntos em transação curta usando o repositório T011. O caller atual no core apenas recebeu version_id antes do parse; protocolo durável e transações longas anteriores são responsabilidade T013, ainda não corrigidos nesta tarefa.

O Block/dataclass antigo foi removido sem alias/shim. O agregado novo contém os contratos concretos T010. Sem alteração de indexing.py, lineage, llm, contracts, dependências/lock ou dados reais.

## Comportamento e casos

C03: todos os charspans do Docling são examinados; mapa parcial gera intervalos unknown para lacunas, sobreposição ambígua ou charspan inválido. Nunca página proporcional. Tabela Markdown é atribuída a página única factual ou unknown se multipágina. DOCX/MD não recebem paginação inventada. Seleção de citações usa a função concreta T010 e offsets absolutos, demonstrando duas ocorrências iguais e página 2→3.

C04: canônico exato UTF-8/hash/codepoints, sem strip de texto de itens; separador LF duplo explicitamente particionado sem página. Chunk é fatia contínua e mapeamento é validado pelos contratos concretos. Não usa reparse como fonte para citação persistida.

C05: ocorrência H1 tem identidade própria; H2/H3 pequenos cabem no mesmo chunk e todos os spans/caminhos são referenciados. Tabela inteira ou recusa específica antes de gerar chunks. Fronteiras estruturais usadas perto do alvo; separadores entram no teto e na reconstrução.

C06: cortes binários por codepoint respeitam bytes completos; preferem espaços quando disponíveis. URL, base64, CJK, emoji e NFD sem perda. Política de overlap versionada repete só última unidade de parágrafo/fragmento quando o chunk anterior tem mais de uma e o próximo avança com teto/orçamento. Intervalos duplicados são explícitos; nenhum chunk fica contido no seguinte. Alvo/teto default 800/1500. O teto mínimo de dois tokens comporta codepoint UTF-8 de quatro bytes mais LF duplo, pois ChunkRecord recusa texto só branco. Configurações incapazes disso são rejeitadas antes do parse. Testes incluem teto=2 e separadores, além de overlap desativado.

C08: MD/DOCX/PDF equivalentes em memória; duas threads chamam mesma instância e duas instâncias, repetidamente, com conteúdo distinto. Instrumentação externa envolve o construtor/conversão reais para contar uma construção e excluir concorrência por objeto, sem substituir resultado por dublê. Lock cobre lazy init, convert e adaptação; resultado fica local a cada chamada.

Receitas: `docling-order-v2:item-text-exact:table-markdown:separator-LF2:codepoints`, parser `local-v2:all-charspans`, chunker `structure-v3:canonical-v2:utf8-4-v1:...:last-paragraph-or-fragment-v2:...:h1-occurrence`. Estimador continua UTF8/4, não tokenizer Gemini.

## Limites reais e pendências

- RAGAS: **Bloqueado** — sem dataset, runner/evals, baseline e limiares aprovados; testes determinísticos não provam qualidade de recuperação.
- Persistência/transações/citações consultadas do banco: **Não executado** nesta tarefa, integração T011/T013. Banco real, migrations e indexação não foram exercitados ou alterados.
- Testes completos de indexação/provedor: **Não executado**; só construção do parser de teste foi migrada. Nenhuma chamada real, leitura de chave ou de traceback sensível; Langfuse não foi religado.
- Casos antigos com fixture de banco: **Não executado** nesta focal. Suíte congelada T001 intacta.
- Revisão independente Google e G4: **Não executado**, dependem de despacho/aceite humano. Não declarar todas as ressalvas encerradas.

## Falha focal e plano de correção

Primeira prova: **Falhou**, 45 passed / 4 failed em 18,64 s (`pytest-focal.txt`), sem repetir suíte buscando verde. DOCX sintético faltava o content type OOXML de styles.xml (o modelo real recebia Part genérica); corrigido o arquivo de fixture em memória, sem substituir Docling. Configuração de teto=1 permitia codepoint mas não codepoint+LF duplo, gerando chunk só branco proibido pelo contrato T010. Novo teste exige rejeição desse teto na construção da configuração; o caso Unicode/separadores usa o teto mínimo válido=2, sem afrouxar hash, cobertura, conteúdo e orçamento.

Plano focal explícito: repetir somente os casos DOCX/formatos equivalentes C03/C05/C08 e casos C04/C06 cujo menor teto foi corrigido; incluir todos os seis alfabetos na matriz de limites. Os casos antigos e os demais novos já passaram e não serão repetidos. Validador final após evidências.

## Resultado da prova

- Primeira execução consolidada: **Falhou**, 45 passaram / 4 falharam, 19 warnings, 18,64 s (`pytest-focal.txt`). Contém 14 casos anteriores selecionados sem banco (parametrização amplia a contagem) e todo o arquivo novo. As quatro falhas foram identificadas e corrigidas conforme plano acima; a evidência original não foi sobrescrita.
- Repetição de correção: **Passou**, 23 passaram / 8 não selecionados, 3 warnings, 6,81 s (`pytest-correcao-focal.txt`). Incluiu matriz C06 inteira com novo mínimo válido, configuração rejeitada C04, equivalência real de formatos e concorrência/reuso DOCX. Os demais casos passaram na primeira execução e não foram repetidos.
- Casos C03/C04/C05/C06/C08 do escopo parse/chunking: **Passou** combinando primeira execução dos casos não afetados com repetição dos afetados. Isso não equivale a uma execução integral verde da aplicação nem a RAGAS ou revisão independente.
- Sem consulta de segredos, rede de modelos (`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`), Postgres, Qdrant ou Langfuse durante a focal.

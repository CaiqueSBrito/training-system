# T-017 — Revisão independente Anthropic dos ajustes finais de teste

- Revisor: anthropic (Claude Opus 5.5), worker Orca `task_02fee58bd60b` / `ctx_107569057e19`.
- Autor revisado: sessão principal OpenAI (orquestração local da T-017).
- Data: 2026-11-24.
- Modo: **só leitura** de produto e testes. Nenhuma suíte, API real, Postgres ou Qdrant executados por esta revisão; nenhum segredo, `.env` ou artefato bruto antigo lido; Langfuse não usado; sem commit.
- Entradas: `tarefas/T-017.md`; `evidencias/T-017/pytest-integrada-r1.txt` (só falhas e resumo); trechos de `evidencias/T-011`, `T-012`, `T-013`, `T-015`; os cinco arquivos alterados após a R1 (mtime 23:14–23:15, depois da R1 das 23:13).

## Veredito

**Aprovado com ressalvas** para os ajustes de teste. Nenhum dos achados bloqueia a integração, mas os achados 1 a 3 têm de ser registrados (ou corrigidos) antes do G4.

Isto **não** aprova a prova integrada. O resultado da execução final (com intervalo de 6 s entre chamadas) estava **pendente** quando esta revisão foi feita, e esta revisão não o trata como sucesso. EVAL-INT-001 e a causa histórica continuam como estão registradas (`Bloqueado` por decisão humana).

## Verificações e resultado

| Verificação | Resultado | Base |
| --- | --- | --- |
| Só testes mudaram após a R1, sem produto, modelo, prompt ou limiar | Confirmado | `find -newermt 23:00`: só `conftest.py`, os quatro testes, fixtures regeneradas pela suíte e arquivos de governança. `evals/runner_ressalvas.py` sha256 `4ebf5e8c…` igual ao de `T-015/artefatos-sha256.txt`. `packages/llm/src` sem mtime posterior. |
| Testes congelados T-001 intactos | Confirmado | `test_classificacao.py` `b15e416b…`, `test_plano.py` `c897e706…`, `test_itens.py` `1623c0e1…` iguais aos de `arquivo/2026-11-24-spec-contratos-chat/tarefas/T-001.md:13-15`; `git status` sem modificação em `packages/contracts/tests` rastreados. `T-010` `test_ressalvas_contratos.py` e `T-013` `test_ressalvas_protocolo.py` batem com os hashes registrados. |
| O pacing não falsifica a API nem repete chamadas após 429 | Confirmado | `conftest.py:22-35` só serializa e espaça (`sleep`) antes de chamar o método original de `GeminiModels._embed` e `judge_injection`. Não captura exceção, não repete chamada e não troca resposta. O juiz continua com `num_retries: 0` (`training_llm/__init__.py:229`). O fixture é opt-in, e todos os testes que constroem `GeminiModels` com credencial real o pedem (`test_models.py:11`, `test_indexing.py:220,336,478,562`). |
| Asserts não afrouxados | Confirmado, com exceções | Ver achados 1 e 4. `test_indexing.py:752-755` mantém `attempts == 1` e `failures == []`, então um 429 que vire `Bloqueado` continua falhando o teste. |
| Dublês só em testes e sem alegar embedding real | Confirmado | `test_indexing.py:149-164` (comentário "Dublê da API externo restrito a teste"), `:582-600` (docstring "vetor unitário determinístico") e `test_ressalvas_runner.py:504-519` (comentário "Dublê externo de relevância"). Nenhum dublê em código de aplicação. |
| Fixtures válidas para o protocolo | Confirmado | Parser de teste com spans de `test_ressalvas_parse.document` prefixados por `document_version_id` (`test_indexing.py:141-148`, `:610-617`). `injection_judge_spec` do dublê tem o formato do real: provider `google`, sha256 de 64 hex, versões (`:153-158`). Esse campo faltava na R1 e causou a falha `storage`. |
| Gravação de artefatos de teste só em `tmp_path` | Confirmado nos arquivos alterados | `test_indexing.py:168,628,750`, `test_ressalvas_runner.py:761,776,811`. O JSON por caso do juiz real agora vai para `tmp_path`, e `evidencias/T-016/juiz-real-ressalvas.json` (22:06) não foi sobrescrito. Há uma exceção já existente fora dos arquivos alterados (achado 5). |
| Isolamento de env do CLI | Confirmado | `test_ressalvas_runner.py:812-813` remove `OPENROUTER_API_KEY`/`EVAL_JUDGE_MODEL` do host antes de usar o `.env` de teste. Isso é necessário porque `read_settings` aplica `os.environ` por cima do arquivo (`training_llm/__init__.py:24`). `execute_isolated_evaluation_or_calibration` continua com monkeypatch (`:821-824`), então nenhuma chamada externa sai. |
| Esquema `index_ack` | Correto | A R1 mostrou o teste esperando `index_projection_ack`, nome que não existe. O teste agora espera `index_ack` (`test_lineage.py:189-192`), que é o nome real em `tables.py:144`, `0003_canonical_audit_protocol.py:185,209` e `T-011/resultado.md:27`. A igualdade exata do conjunto e `head == "0003"` (`:194`) continuam. Isso corrige o teste, não o afrouxa. |

## Achados

1. **[Baixo/Médio, cobertura] `packages/lineage/tests/test_lineage.py:109-112`: o teste de imutabilidade perdeu a verificação da mensagem, e no TRUNCATE deixou de exercitar o trigger.**
   - Antes, os quatro comandos exigiam `match="chunk is immutable"`. Agora UPDATE e DELETE conferem só o SQLSTATE `23514`, que qualquer outro trigger ou CHECK também produziria. TRUNCATE passou a esperar `0A000`, que é a recusa por FK do Postgres (`chunk_provenance` referencia `chunk`). Essa recusa acontece antes de `chunk_immutable_truncate` (`0001_ingestion.py:59`) disparar.
   - O assert novo `get_chunk("c1") == record()` (`:113-114`) fortalece a prova de que os dados não mudaram, e a adaptação ao esquema aditivo da 0003 é legítima. Mesmo assim, no head nenhum teste prova que o trigger de TRUNCATE de `chunk` ainda existe. `test_ING_LIN_001_intermediate_roundtrip_and_trigger_lifecycle` (`:250-262`) só testa `TRUNCATE ... CASCADE` na revisão 0002.
   - Recomendação: manter `match="chunk is immutable"` para UPDATE e DELETE, junto com o SQLSTATE, e acrescentar `TRUNCATE chunk CASCADE` no head esperando a recusa do trigger (`23514` com a mensagem). Se a correção ficar para depois, registrar a lacuna como ressalva em ING-LIN-001.

2. **[Médio, governança] Testes G3 antigos editados fora do escopo declarado da T-017, sem registro explícito ainda.**
   - `T-017.md` ("Escopo") só permite editar `packages/ingestion/tests/test_ressalvas_integracao.py` e proíbe "testes G3 antigos". `casos.md:55` (C11) exige "Registrar explicitamente se testes antigos G3 congelados exigem adaptação; não editá-los sem novo gate". `T-011/interfaces.md:33` deixou para o principal decidir sobre `test_lineage.py` na T-017.
   - Os arquivos editados (`test_lineage.py`, `test_models.py`, `test_indexing.py`) são de T-003–T-008, cujo G3 foi dispensado sem hashes congelados (`T-012/implementacao.md:9`), e a autonomia humana contínua cobre o trabalho. Falta a T-017 registrar, por arquivo, o que mudou e por quê (as quatro causas da R1), citando essa autonomia como base.
   - O `test_ressalvas_runner.py` atual (`7dbfd608…`) também diverge do hash em `T-015/artefatos-sha256.txt` (`01dbeb03…`). O novo hash precisa ser registrado na T-017. O manifesto da baseline não vincula esse arquivo, então a baseline não é afetada.

3. **[Médio, evidência] `packages/ingestion/tests/test_ressalvas_integracao.py` não existe.**
   - O plano e a matriz (`matriz.md:19`) apontam esse arquivo como local do C11. Hoje a prova integrada é a suíte completa. C11 pede manifesto com revisão do código, versões e configurações, migrations, IDs de caso, um `run_id` e resultados por requisito, e a saída do pytest sozinha não traz isso.
   - O principal não deve marcar C11 ou EVAL-INT-001 como `Passou` só pelo resumo do pytest. Deve registrar como o manifesto foi obtido, ou manter o status pendente ou bloqueado.

4. **[Baixo, força de teste] `packages/ingestion/tests/test_indexing.py:192`: `pytest.raises( Exception )` é amplo demais.**
   - O teste injeta `RuntimeError('Falha após upsert')` em `record_index_ack`. Qualquer outra exceção, por exemplo de fixture ou de configuração, também satisfaz o assert. A propriedade relevante continua verificada depois, por `authorize_candidates(...) == {}` (`:202`).
   - Sugestão: restringir a `(IngestionStageError, RuntimeError)` ou usar `match`. Não dá para saber se isso já existia antes, porque os arquivos não estão no git.

5. **[Baixo, já existente, fora dos arquivos alterados] `packages/ingestion/tests/test_ingestion.py:272-277` regenera `tests/fixtures/study.{pdf,docx,md}` dentro do repositório.**
   - Isso explica o mtime 23:05 das fixtures na R1. O assert de bytes iguais protege contra mudança silenciosa. Mas os arquivos não são rastreados pelo git, e se `generate.py` divergir, a fixture original é sobrescrita antes da falha e não há como recuperá-la.
   - Sugestão para uma tarefa futura: gerar em `tmp_path` e comparar.

## Observações (sem ação obrigatória)

- **Dublê de embedding em `execute_scope`** (`test_ressalvas_runner.py:504-519`). É um dublê oráculo: monta vetores a partir de `expected_evidence_substrings` e de `user_input`. Prova a ligação Docling real → Postgres e Qdrant descartáveis → recuperação → registros → avaliação, não a qualidade da recuperação. O comentário diz isso. O `Passou` (`:605`) não pode ser usado como evidência de RAGAS ou de recuperação de EVAL-INT-001. Essa evidência continua sendo a baseline `ragas-run-b647f4ac987c`.
- **Juiz real com embedding de teste.** `test_ING_INJ_001_each_r3_case_with_configured_real_judge` usa juiz real e embedding unitário determinístico (`:596-600`). A evidência desse teste vale para ING-INJ/SEC-ATQ (juiz e decisão), não para ING-EMB. ING-EMB real fica com `test_ING_EMB_001_ten_real_requests` e com o teste ponta a ponta `:220`.
- **Skips sem credencial.** Os testes reais pulam quando falta `GEMINI_API_KEY` (`test_models.py:14`, `test_indexing.py:226,340,482,578`). Na prova integrada, qualquer `SKIPPED` tem de virar `Bloqueado`, nunca `Passou`. A R1 não teve skips, e as 2 desmarcações vieram do filtro do comando.
- **Intervalo de 6 s.** O intervalo é contado entre inícios de chamada (`conftest.py:27-30`) e o estado é global ao processo, o que está correto para a suíte sequencial. Com `pytest-xdist` ele não valeria entre processos, mas esse plugin não está instalado (R1: `langsmith, anyio, Faker`). A docstring corretamente não alega a cota da conta.
- **Causa da R1.** A classificação da R1 bate com os artefatos: 5 casos `Bloqueado` por provedor no juiz real, duas divergências de fixture ou esquema, o gate falhando com o embedding pseudoaleatório e a variável de host vazia sobrepondo o dotenv. Os 429 vêm da mensagem `IngestionStageError`/`status_code` capturada no teste, não de traceback bruto. Esta revisão não relê o artefato antigo para confirmar o código HTTP.

## Não executado

- Suíte, testes focais, APIs reais, Postgres e Qdrant: **Não executado** (fora do escopo; o brief proíbe rerodar).
- Resultado da integração final com intervalo de chamadas: **Não executado** por esta revisão; estava pendente na sessão principal.
- Diff linha a linha do estado anterior: **Não se aplica**, porque os arquivos não estão rastreados no git. A comparação usou a saída da R1, os hashes registrados e os mtimes.

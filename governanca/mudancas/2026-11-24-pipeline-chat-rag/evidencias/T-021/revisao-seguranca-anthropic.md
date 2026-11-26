# T-021: parecer de segurança LLM (papel `seguranca-llm`, Anthropic)

- Revisor: anthropic (Claude Opus 5.5), worker Orca `task_165cab01e82f` / `ctx_c109fa06b6ea`. Só leitura. O autor é a OpenAI (`ctx_c55621c67ed1`).
- Snapshot: o mesmo de `revisao-anthropic.md` (sha256 listados lá).
- Não li `.env` nem artefato de chave. Da captura opt-in li só as chaves de vínculo (`call_id`, `generation_item_index`, `attempt`, `status`, `context_ref_mapping`), não o conteúdo.

## Veredito

**Aprovado com ressalvas.** Não há bloqueante de segurança. As ressalvas S1 a S3 não permitem vazamento entre owners nem entrega sem juiz.

## Conferência OWASP LLM 2025

| Risco | Evidência | Resultado |
| --- | --- | --- |
| LLM01 injeção direta | `chat.py:253-280`. E1 normaliza e limita sem LLM. O E2 é fail-closed: exceção ou orçamento esgotado dão `falha_do_juiz_de_entrada`, e veredito malicioso dá `prompt_malicioso`. E4, E5 e os seguintes só rodam depois do E2 seguro. O E3 paralelo é cancelado. | Passou |
| LLM01 injeção indireta | Só chunks autorizados por `authorize_points`, reautorizados depois do rerank (`service.py:233,266`) e de novo no E12 (`lineage/chat.py:119-121`). A correção de `protocol` exige payload igual a decisão igual a ack (`eligibility.py:78-84`), e o ack obsoleto é recusado com lock (`repository.py:535-540`). | Passou |
| LLM05 saída mal tratada | `render_item` compartilhado no E11 e na checagem do E12 (`rendering.py:11-24`, `lineage/chat.py:86-88`). Escapa `front/back/stem/options/explanation/evidence`, remove Cf/Cs/Cc e revalida o schema. O original canônico vai para a linhagem. | Passou |
| LLM07 vazamento de system prompt | Prompts offline versionados por sha256 (`diagnostics.json`), sem segredo no prompt. | Passou (nada mudou na T-021) |
| Dados pessoais e segredos | Logs só com categoria e HTTP status (`chat.py:139,147,235`). `diagnostics` sem texto. Erro da CLI genérico (`cli.py:77-80`). A chave Gemini vai só para o construtor do cliente (`cli.py:49`). O harness real grava um `.env` temporário com `O_EXCL` e modo 0600 (`run_chat_real.py:65`). LF continua off, e o tracing OTel é local, sem exportação. | Passou |
| Isolamento de owner | O escopo canônico exige versões do owner (`lineage/chat.py:241-244`). Continuação por (owner, session) com binding (`lineage/chat.py:264-276`). Teste `test_two_owners_same_session_*`. | Passou |
| Excesso de consumo | Orçamento de chamadas `RequestAdmission`, sem retry (`chat.py:473-488`; `cli.py:50` usa `attempts=1`). Embeddings entram no mesmo orçamento (`chat.py:491-496`). Taxa por owner com lock no banco. | Passou |
| Captura opt-in | `cli.py:68-74`: `os.open(...,0o600)` com `fchmod`. Desligada por padrão. Cada item fica ligado ao `call_id` real (`chat.py:161-167,395,440`). A execução real mostra modo `-rw-------` e o vínculo confere. | Passou |

## Ressalvas

- **S1 (baixo/médio): minimização de dados.**
  - O reparo E8 (`chat.py:438`) e o juiz E10 (`chat.py:422`) recebem o texto integral dos chunks. Com isso entra texto fora do `section_prefix` e de outras alocações. Ver o achado 2 do técnico.
  - O conteúdo é do próprio owner e já passou pela triagem de injeção, então o risco é baixo. Mesmo assim, aumenta a superfície de injeção indireta e contraria o "escopo canônico".
  - Corrigir com `_context_for` por grupo.
- **S2 (baixo): o prompt bruto é persistido sem limite na recusa por tamanho.**
  - `chat.py:184` grava `prompt=state.prompt` (o original) mesmo em `tamanho_excedido` (`chat.py:256-257`).
  - Pela CLI o argv limita o tamanho. Na futura API um corpo enorme iria inteiro para `chat_turn.record`, o que abre espaço para inchaço ou DoS de armazenamento.
  - Sugestão: na recusa E1, gravar só um prefixo limitado mais o sha256 e o tamanho, ou impor um limite de corpo na fronteira HTTP.
- **S3 (baixo): a captura segue symlink.**
  - `cli.py:71` não usa `O_NOFOLLOW`. Se o caminho for um link, `O_TRUNC` e `fchmod` caem no alvo.
  - O caminho é do operador, por isso o risco é baixo. Sugestão: `O_NOFOLLOW` (ou `O_EXCL`) e recusar caminho existente.

## Fora do escopo

- O conteúdo dos prompts e os delimitadores (`agents.py`) já foram aprovados na T-019. Não reauditei.
- A T-022 (avaliação aditiva) não entra aqui.
- Este parecer não aprova G4 nem produção.

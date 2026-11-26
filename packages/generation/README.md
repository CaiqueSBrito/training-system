# Chat E1–E12

`ChatPipeline` integra os agentes reais da T019, recuperação autorizada da T018 e persistência transacional da T020. A CLI é para operador local confiável: `--owner` declara a identidade, não autentica uma pessoa. Não existe usuário padrão/admin, adaptação HTTP ou ferramenta SQL/bash exposta ao LLM.

## Configuração e entrada

Configurar `.env` local com APP_POSTGRES_*, QDRANT_URL, GEMINI_API_KEY, OPENROUTER_API_KEY, GENERATION_MODEL, INPUT_JUDGE_MODEL, OUTPUT_JUDGE_MODEL e PROMPT_SOURCE=offline. Segredos nunca entram em prompts, diagnósticos ou linha de comando. A CLI usa o esquema existente, sem migrar o banco da aplicação. O manifesto calibrado real do BGE precisa existir; não há corte ou modelo alternativo.

Entrypoint instalado: `.venv/bin/training-chat --help`. Alternativa: `.venv/bin/python -m training_generation.cli --help`.

Argumentos obrigatórios: `--owner`, `--session`, uma ou mais `--version`, `--message`, `--calibration`. O prefixo hierárquico pode ser informado repetindo `--section`. `--count` padrão5, limite20, também sujeito a CHAT_MAX_ITEMS. `--env-file` padrão.env. `--diagnostics` grava somente IDs, status/erros categóricos, modelos, versões/hashes e latências. A resposta ChatResponse JSON sai em stdout; progresso e diagnóstico seguro vão a stderr.

A admissão padrão da CLI espaça inícios em6s e limita12 chamadas por processo (`--request-interval`, `--max-model-calls`). Engloba juízes, agentes e embeddings das consultas. Exceções não são retentadas. O único reparo global é reservado a envelope inválido ou itens reprovados E9/E10. Gemini SDK de embedding e ADK são configurados com attempts1; os serviços T019 não fazem loops próprios de reparo.

## Uso Python

```python
pipeline = ChatPipeline(engine, generation_service, qdrant_index,
                        model_client, reranker, tracer_provider=provider)
response = await pipeline.run(owner_id, session_id, message,
                              document_version_ids=version_ids,
                              section_prefix=section_prefix, count=5)
```

Todos os objetos são implementações reais dos pacotes existentes. IDs e escopo vêm do chamador confiável; não do modelo. O serviço normaliza NFKC/remove invisíveis, confere tamanho bruto e normalizado, consome slot durável por owner e não chama LLM para E1 recusada ou mensagem vazia após normalização. E2/E3 rodam em tasks paralelas com cancelamento e await de limpeza; E4 espera veredito seguro e classificação válida. Fora de escopo não usa RAG.

Em `gerar_mais`, manter owner/session/versões/seção. A pipeline carrega plano, modo, tipo de item e cursor da linhagem desse owner/session; não reinicia as primeiras fontes. Só `gerar_mais` herda o cursor: um pedido novo (consulta ou cobertura, qualquer tipo de item) abre uma progressão própria, sem cursor herdado e sem excluir fontes de pedidos anteriores, e passa a ser a continuação da sessão. Um estado de continuação inválido nunca é herdado: `gerar_mais` falha antes do E4, e um pedido novo o substitui. O lock de sessão é curto, sem transação durante LLM. Antes do commit, um fence pelo turno anterior impede entrega de resultado que perdeu uma corrida de sessão. Novas fontes e quantidade acumulada são salvas junto ao turno.

Cobertura consome as Allocation reais da T018: cada cota gera seu lote sobre interseções canônicas daquela subseção/ocorrência H1, mantendo as cotas globais do recuperador. Chunks mistos são recortados para o gerador e as citações são reconferidas contra a alocação e o prefixo. Não usa reranking temático na cobertura. Consultas mantêm híbrido/RRF, BGE calibrado e top5 padrão.

E9/E10 validam todos os itens do lote, com IDs estáveis no reparo e preservação dos bons. O reparo e o E10 recebem o mesmo contexto filtrado por seção e alocação usado na geração do item; ref local desconhecida vira `citacao_inexistente`, nunca é lida na numeração global. Cada tentativa E8 (bruto, call_id, resumo do contexto com hash) fica em `chat_generation_attempt`, lida por `get_generation_attempts(owner, item_id)`. Evidência normalizada só é aceita com uma ocorrência canônica inequívoca; se houver repetição ambígua, o item é reprovado/reparado ou omitido. A fatia original é gravada em CitationRecord/CitationSpanSelection. Páginas discordantes resultam em Source.page=None; get_item_lineage devolve todos os spans exatos.

`training_contracts.rendering.render_item` é a transformação pura compartilhada entre E11 e save_turn. ItemRecord.content e CitationRecord conservam conteúdo original; a resposta persistida/entregue contém exatamente render_item(content), sem aplicar escape duas vezes. Conteúdo original da linhagem não deve ser renderizado cru. Citações inválidas de itens omitidos ficam no registro bruto/validado, sem CitationRecord ou coordenadas fictícias.

E12 revalida owner, decisões e publicação sob locks e confirma o grafo inteiro antes de retornar. Falha de storage/commit devolve erro sem itens; tenta registrar o erro em outra transação curta, sem prometer sucesso se o próprio PG estiver indisponível. Recusas, fora de escopo e insuficiência também são gravados. O trace_id é o ID real do span OTel local ativo; Langfuse permanece desligado, sem publicação remota simulada. Spans E5/E6/E7 internos medem operações reais; cobertura registra E6 Não se aplica.

## Captura e prova real

`--evaluation-capture` é opt-in e contém conteúdo original: usar somente com material autorizado/controlado. O arquivo precisa ser novo: caminho existente ou link é recusado antes de qualquer chamada, e a escrita usa O_EXCL|O_NOFOLLOW com modo 0600. Cada item entregue aponta para call_id/attempt/generation_item_index e para o NumberedContext exato recebido na chamada inicial ou de reparo, com mapeamento de refs locais/globais. Nenhum texto é acrescentado ao arquivo de diagnostics.

Prova real executada uma única vez em2026-11-25:

```sh
.venv/bin/python packages/generation/tests/run_chat_real.py --artifacts governanca/mudancas/2026-11-24-pipeline-chat-rag/evidencias/T-021/real-cli --keep-sandbox
```

Esse procedimento cria PG/Qdrant exclusivos, ingere documento novo pelo caminho público, chama o executável instalado com modelos/embedding/BGE/juiz reais e confere a resposta contra o commit no PG. O resultado daquela execução foi Passou, um flashcard com fontes, sete chamadas admitidas na CLI e uma na ingestão; não foi repetido para obter aprovação. O sandbox foi coletado/verificado pela T022 e limpo depois, com manifesto original preservado e evento cleanup.json separado.

Os artefatos reais, comandos focais, falha inicial preservada, correção e limites estão em `governanca/mudancas/2026-11-24-pipeline-chat-rag/evidencias/T-021/resultado.md`. Uma execução positiva não calibra rubrica nem prova qualidade global; avaliação de geração/faithfulness é um corte novo da T022, separado da baseline histórica sem E8. Revisão independente/segurança e aceite final permanecem necessários.

Prompt recusado por tamanho: o turno guarda só os primeiros `max_input_chars` caracteres; `chat_input_audit` guarda tamanho e sha256 do bruto (migração 0005).

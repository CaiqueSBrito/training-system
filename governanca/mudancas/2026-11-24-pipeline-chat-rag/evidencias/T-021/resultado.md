# T021 — integração E1–E12 e CLI

Executor efetivo OpenAI, worker `task_3654b00024c5`, dispatch `ctx_c55621c67ed1`, data2026-11-25. Revisão independente e revisão de segurança: **Não executado** por este worker; encaminhar ao provedor Anthropic. G4: **Não executado**, sem autoaprovação. Registro de tarefa/progresso pertence ao principal.

## Entrega e escopo

- ChatPipeline async em packages/generation/src/training_generation/chat.py; CLI real em cli.py e script training-chat instalado; README descreve configuração/uso/limites.
- Correção obrigatória protocol.py: upsert inicial leva decision_event_id, decision_revision e input_sha256 autoritativos. Usa exatamente Reconciler._verify_batch antes de ack e na conferência final; não duplica critérios nem afrouxa SafeRetriever. Ingestão pública nova agora é consultável sem reconciliação manual. Worker obsoleto/falha não publica ack/ready.
- Ampliação autorizada msg_62fdaebf52b5: transformação E11 pura em contracts/rendering.py, consumida por generation.validation.sanitize_item e lineage.chat.save_turn. Modelos/Pydantic/casos G3 não foram alterados. Conteúdo/citação/proveniência originais separados da transformação segura entregue. Omitidos podem preservar conteúdo com citação inválida sem fabricar CitationRecord.
- Ampliação autorizada msg_5e80914c8168: service.py recebeu somente spans E5_retrieval/E6_rerank/E7_context e provider opcional; algoritmos/cutoff/paginação/barreira/retorno preservados. Workspace dependency training-observability declarada em retrieval; geração declarou lineage/retrieval/ingestion e script CLI. Principal foi o único dono de uv.lock/sync; nenhuma biblioteca externa nova adicionada por esta slice.

## Provas exatas

| Execução | Status | Resultado / artefato |
| --- | --- | --- |
| Focal consumidor/validação/linhagem/protocolo inicial | Falhou | 69 passaram, 11 falharam, 1 warning, 38.00s; focal-inicial.txt |
| Correção concreta do construtor SearchPlanRecord | Passou | 11 passaram, 13 desmarcados, 12.12s; focal-correcao-plan.txt |
| Captura E8 opt-in e vínculo item/chamada/contexto | Passou | 1 passou, 24 desmarcados, 3.56s; focal-captura.txt |
| Invariantes reforçados finais | Passou | 8 passaram, 39 desmarcados, 9.29s; focal-invariantes-finais.txt |
| Reparo E9/E10 e reprovação persistente | Passou | 3 passaram, 27 desmarcados, 5.22s; focal-reparo-e9-e10.txt |
| CLI real, uma execução controlada | Passou | exit0, ChatResponse ok com1item; real-cli/manifest.json, response.json, diagnostics.json, e8-capture.json |

A falha inicial foi causada por argumento tracer_provider indevidamente passado ao contrato SearchPlanRecord durante edição da instrumentação. O contrato corretamente rejeitou o campo extra; a causa foi removida no chamador, sem mudar schema ou asserts. Não há declaração de uma nova suíte integral verde: os11 casos afetados foram reexecutados após a correção, os69 que passaram não foram repetidos. Provas posteriores são complementos estreitos para captura, commit/revogação/multipágina e E9/E10; nenhum rerun de API real para obter sucesso. O warning Pydantic da fixture de erro da T020 permanece histórico e não é falha.

Comando inicial: `.venv/bin/python -m pytest packages/generation/tests/test_chat_integration.py packages/generation/tests/test_geracao_validacao.py packages/lineage/tests/test_chat.py packages/ingestion/tests/test_ressalvas_protocolo.py -q --tb=short`. Complementos usaram -k para os IDs correspondentes, conforme os nomes dos arquivos de saída; nenhum build/lint/formatter ou suíte pesada intermediária.

## Cobertura por comportamento

- E1: tamanho bruto/normalizado (incluindo expansão NFKC e invisíveis), vazio pós-normalização com status erro sem inventar motivo enum, zero LLM, E1 sem intenção/modo/veredito E2. Rate slot durável e concorrente coberto pelos testes reais T020 incluídos.
- E2/E3: reprovação e timeout E2 cancelam E3 lento e aguardam limpeza; nenhum E4 antes de juiz seguro/classificação. Fora de escopo só executa E1/E2/E3/E12.
- E4–E7: plano canônico1–3, busca real em PG/Qdrant com dublês somente de modelos na prova determinística. Consulta híbrida/BGE real também exercitada no CLI. Insuficiência não chama geração. Coverage/gerar_mais preservam plano, modo, alocação, cursor, owner/session e progresso; citações fora da seção/cota são recusadas.
- E8–E10: flashcard e questionário com linhagem; lote E10, IDs estáveis, item bom preservado, reparo somente falhos, falha persistente com motivo estável, sem terceira geração. JSON inválido consome o mesmo orçamento de reparo; não abre outro loop.
- E11/E12: HTML/script/&/aspas escapados uma vez; origem intacta e resposta adulterada rejeitada. Evidência normalizada repetida falha por ambiguidade. Quote que cruza páginas verdadeiras2/3/separador sem página retorna pageNone e spans exatos; nunca usa página inicial como verdade.
- Falha de commit: teste reforçado confirma grafo completo visível na conexão antes do callback de commit falhar; nenhum item é entregue nem fica persistido. Revogação durante E8 é rejeitada por PermissionError na barreira E12, não por falha incidental anterior. Avanço concorrente de sessão também rejeita entrega stale sem sobrescrever progresso.
- Dois owners legítimos, mesma session string, documentos e cursores distintos; leitura de item/continuação de owner alheio não vaza. Cursor com binding malformado é recusado antes de modelos e não reinicia quantidade.
- InMemorySpanExporter real de teste confere parent/trace de E5/E6/E7 e ausência de inferência E6 em coverage. Agentes reais usam ADK run_async e prompts delimitados da T019; a execução real E3/E4/E8 não usa dublês.

## Execução real e avaliação

Run `t021-real-c513f0da772748a3970dfdf2959e25ad`, chat_turn_id `2028b786410b4399b3bd3c98c6450e2c`, trace_id `b6399d4a56bd5353db9202c61944380e`. Documento novo ingerido/publicado generation1 sem reconciliação manual. A resposta persistida foi lida e comparada integralmente com ChatResponse emitida pelo executável.

Chamadas reais configuradas: Gemini gemini-3.1-flash-lite para E2/E3/E4/E8; E10 OpenRouter anthropic/claude-haiku-5.5; embedding gemini-embedding-2@3072@fmt-v1; BGE BAAI/bge-reranker-v2-m3 fixado na revisão953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e com manifesto de calibração real65e0ac016c6fe7baecf5e7c043d3c18aae073ecf8da3ab5c173f1012057b2d7b. Modelos e versões/hashes exatos dos seis prompts constam de diagnostics.json. Foram7 chamadas admitidas na CLI e1 na ingestão, intervalo6s, orçamento CLI12/ingestão4, attempts1 no SDK e sem retentativa manual; não houve429/503 nessa execução.

Latências reais em segundos: E1 .023, E2 18.483, E3 11.714 (tasks concorrentes, admissão espaçada), E4 4.616, E5 8.111, E6 6.321 (inclui carregamento inicial de pesos), E7 .016, E8 1.307, E9 .012, E10 3.923, E11 .00035, E12 .033. E5_E7 total14.448 é uma medição distinta, sem triplicar a duração nos spans internos. Não são benchmark/calibração de produção.

O artefato E8 opt-in0600 contém contexto exatamente entregue a cada generate e cada item original vinculado por call_id/attempt/generation_item_index; refs locais/globais explícitos. Nenhum texto ou segredo entra em diagnostics/logs padrão. Manifesto files usa caminhos relativos e SHA256, preservado intacto após coleta. T022 confirmou em msg_be5fa605cf26, só leitura, item original/conteúdo/contexto/owner/sourcehash contra PG; suas cópias protegidas estão em evidencias/T-022. Após coleta, somente o banco/container identificados no manifesto foram removidos, com checagem de owner e sourcehash; cleanup.json registra o evento sem alterar o manifesto original. O arquivo temporário .env com credenciais foi removido; nenhuma migração/write na app compartilhada.

RAGAS compatível histórico antes/depois e faithfulness de geração: **Não executado por T021**, coordenado na T022 com dataset/escopo/versionamento próprios. O runner histórico proíbe faithfulness por ser escopo sem E8; não foi alterado nem comparado indevidamente. Calibração do juiz de saída contra referências separadas: **Não executado por este worker**, pertence ao corte de avaliação; aprovação pontual de um item real não calibra a rubrica nem comprova qualidade global. Não há alegação de baseline anterior de geração.

## Limites e encaminhamento

- OBS-TRC-001 remoto: **Bloqueado**, Langfuse mantido desligado por instrução. Spans/IDs locais foram provados; não houve publicação remota ou link remoto inventado. OBS-TRC-002 no modo desligado: **Passou**, resposta real e warning seguro de exportação não configurada. Reativação remota continua fora do escopo.
- Revisão independente de código/segurança: **Não executado**; necessária sobre escopo ampliado, orquestração, render compartilhado e publicação inicial. Não há autoaprovação.
- G4: **Não executado**. T017 e falhas históricas não foram declaradas resolvidas.
- LSP Python: **Bloqueado**, pyright/pylsp ausentes; AST e referências locais inspecionadas. Sem instalar ferramenta nova.
- Casos G3 T001: hashes conferidos contra os três registrados, sem alteração por esta tarefa. Nenhum modelo/schema congelado alterado.
- Sem commit, push, deploy, reativação de Langfuse ou exclusão de dados compartilhados. Governança/tarefa/progresso devem ser atualizados pelo principal com estes resultados exatos.

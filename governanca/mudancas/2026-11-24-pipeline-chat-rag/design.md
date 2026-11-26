# Design e propriedade compartilhada

## T-018 — Implementar recuperação híbrida cobertura e reranking

retrieve(owner_id, plan, classification, scope, count) -> RetrievalResult(context NumberedContext, chunks list ChunkRecord, run RetrievalRunRecord, allocations, continuation). Scope contém IDs de versões e prefixo de seção resolvidos no Postgres, nunca filtro livre do modelo. Todas leituras fail-closed com authorize_candidates e decisão/revisão/hash do payload. Existing SafeRetriever permanece consumidor real da mesma barreira, sem dois critérios divergentes.

Propriedade: packages/retrieval/; packages/ingestion/src/training_ingestion/retrieval/eligibility.py (somente extração compartilhada da barreira autoritativa, preservando consumidores).

## T-019 — Implementar agentes ADK e geração protegida

GenerationService async input_verdict(prompt)->InputVerdict, classify(prompt)->Classification, plan(prompt,classification)->SearchPlan, generate(prompt,classification,plan,context,count,repair_items=None)->RawGenerationOutput, judge(items,context)->OutputJudgeResult; metadata() retorna modelos/promptversions reais. Serviço ADK run_async, juiz OpenRouter via LiteLLM. Validação item-a-item, sanitização preserva proveniência pré-escape. Não implementar orquestrador/chat.py nesta slice.

Propriedade: packages/generation/agents.py; packages/generation/validation.py; packages/generation/settings.py; packages/generation/prompts/; packages/generation/tests/.

## T-020 — Persistir linhagem e limites do chat

ChatLineageRepository(connection).save_turn(turn,plan,run,items,citations,verdicts,response,provenance=None) transação do chamador, sem commits implícitos; get_item_lineage(owner_id,item_id), get_continuation(owner_id,session_id), acquire_chat_rate_slot(owner_id,limit,window_seconds) com relógio DB e concorrência. Persistir respostas recusadas/erro também, provenance usa spans canônicos e intervalos T010, nada de página inventada.

Propriedade: packages/lineage/src/training_lineage/chat.py; packages/lineage/src/training_lineage/tables.py; packages/lineage/src/training_lineage/migrations/versions/0004_chat.py; packages/lineage/tests/test_chat.py.

## T-021 — Integrar chat E1 a E12 e CLI real

Orquestração async de todas etapas, run_async ADK, E2/E3 paralelos e cancelar pendentes com await cancel seguro, E4 somente após juiz seguro, entrega só após commit de linhagem. CLI real com owner/session/escopo explícitos e JSON ChatResponse, erros não são conteúdo gerado. gerar_mais usa continuação persistida, sem repetir primeiros cards. Integrar observability existente em cada etapa sem reativar serviço.

Propriedade: packages/generation/chat.py; packages/generation/cli.py; packages/generation/tests/test_chat_integration.py.

Configuração: nomes de modelos por env, Gemini inicial gemini-3.1-flash-lite, juiz saída openrouter/anthropic/claude-haiku-5.5 não Gemini, embedding existente3072/chaveinalterada; crossencoder localBAAI/bge-reranker-v2-m3 sem fallback. LLM não recebe ferramenta para SQL/bash. Nenhuma consulta livre do usuário vira SQL; filtros parametrizados e owner obrigatório.

Persistência e recuperação verificam permissão do chamador nos documentos e cada candidato na linhagem atual antes de usar contexto, antes da entrega revalidar decisões porque reclassificação pode ocorrer durante LLM. Source.page é null em citações multipágina ambíguas; a consulta de linhagem deve devolver spans exatos, nunca usar page inicial como verdade.

Coverage percorre todas subseções autorizadas em ordem, distribuição largest-remainder proporcional ao tamanho (3:1 com8=>6:2), cursor durável para gerar_mais; scope ambíguo falha explicitamente, não escolhe documento arbitrário.

T019 não toca pyproject/lock sem coordenação: cria código/testes e informa dependências; T018 é dono do lock para adicionar somente sentence-transformers e pacotes workspace novos quando manifestos prontos. T020 não depende do lock novo para sua prova. T021 será despachada após interfaces reais entregues, não stubada agora.

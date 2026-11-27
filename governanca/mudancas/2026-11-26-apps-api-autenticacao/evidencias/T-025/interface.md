# T-025 — interface proposta para G3

Estado: Não executado (implementação das rotas aguarda G3 humano). Este documento define os casos; não altera contratos, etapas, prompts, modelos, parâmetros ou migrações.

## HTTP e escopo

`POST /chat` recebe exatamente `ChatRequest` (`session_id`, `mensagem`). `owner_id` vem somente de `current_owner_id`, forma textual do UUID autenticado. Campo extra no corpo é recusado pelo contrato (422); os casos também aceitam ignorá-lo, conforme AUT-OWN-001, desde que nenhum dado do outro dono seja criado. Query `owner_id` pode ser recusada ou ignorada, jamais usada. Sem sessão válida: 401; sem CSRF válido da própria sessão: 403, antes de iniciar pipeline, criar turno ou chamar modelo.

Como ChatRequest não contém seleção de documentos, o adaptador consulta no Postgres as versões atuais disponíveis do dono autenticado e fornece seus IDs explícitos a `ChatPipeline.run(owner, body.session_id, body.mensagem, document_version_ids=...)`. Não inventar versão nem aceitar seleção pertencente a outro dono. Prefixo vazio e quantidade padrão da pipeline existente; seleção de documentos/seção pelo cliente exige evolução separada do contrato. Escopo vazio segue a resposta segura do domínio, sem fallback com dados alheios.

## SSE

Resposta HTTP 200: `Content-Type: text/event-stream; charset=utf-8`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`. Sem compressão. UTF-8, cada frame termina em duas quebras de linha. Comentários SSE de keepalive são permitidos; não são eventos de domínio.

```
event: progress
data: {"stage":"E1","sequence":1,"label":"entendendo","seconds":0.001}

event: final
data: {"status":"recusa","mensagem":"Mensagem excede o limite.","itens":[],"omissoes":[],"fontes":[],"motivo_recusa":"tamanho_excedido","chat_turn_id":"...","trace_id":"..."}

```

`progress` contém apenas `stage` (E1–E12), `sequence` (inteiro contíguo a partir de 1), `label` e `seconds` (duração finita não negativa). E1–E4: entendendo; E5–E7: buscando; E8: gerando; E9–E12: verificando. Nenhum texto, token, fonte ou item intermediário é transmitido. Uma notificação por etapa efetivamente executada, em ordem lógica; repetições de geração/validação por reparo acumulam duração na etapa, sem duplicar a notificação. Etapas não executadas não são inventadas. Uma etapa tentada que falhou também conta como executada.

Caminho simples ok: E1,E2,E3,E4,E5,E6,E7,E8,E9,E10,E11,E12,final. Recusa de tamanho no E1: E1,E12,final. Erro no E8: E1,E2,E3,E4,E5,E6,E7,E8,E12,final. `final` contém diretamente a serialização de `ChatResponse`, sem envelope paralelo, exatamente uma vez, depois do commit independente da linhagem. Recusa leva motivo; erro é sanitizado e não leva itens. Exceção antes da validação/autenticação usa status HTTP; não inicia stream.

## Callback, paralelismo e montagem

Já existe `ChatPipeline(..., progress=callback)` com registros síncronos `{stage,seconds}` ao terminar spans. Preservar esse comportamento para a CLI. Na fase posterior ao G3, o ajuste mínimo em generation deverá expor E5, E6 e E7 a partir dos spans reais observados por `_StageLatencies`, em vez de emitir apenas o agregado E5_E7. Encaminhar por execução/trace, nunca por callback global compartilhado. Não alterar a lógica de RetrievalService nem a decisão das etapas.

O callback da API enfileira registros com transferência thread-safe para o loop ASGI (recuperação roda em thread). O adaptador agrega repetições, ordena E2/E3 mesmo se E3 terminar primeiro e só libera uma etapa quando suas predecessoras executadas estiverem resolvidas. Em fronteiras de geração com reparo, pode reter progresso até saber que não haverá retorno às etapas anteriores; nada exige streaming de tokens ou emissão imediata. No encerramento libera pendentes e E12, depois final. Não ordenar ou suprimir operações do domínio para ordenar notificações.

Ponto proposto de composição: `app.state.chat_pipeline_factory(*, progress) -> ChatPipeline`, invocado por requisição. Em produção, a composição usa os componentes reais da CLI: engine síncrono de linhagem, GenerationService/GenerationSettings, IndexingPipeline/índice Qdrant e cliente de embeddings, LocalReranker com manifesto calibrado, RequestAdmission/AdmittedQueries e tracing local. Recursos pesados são inicializados/fechados no lifespan; pipeline e orçamento são próprios do turno. Operação de reranker permanece fora do loop, como no domínio atual. Configuração ausente resulta em indisponibilidade explícita, nunca resposta fixa. Nenhuma chamada real de LLM nesta fase.

Os testes definem factory apenas em `apps/api/tests/api_chat_support.py`, com ChatPipeline, ingestão, recuperação, validação e repositórios reais, Postgres descartável por caso e Qdrant descartável por módulo (imagem local, sem pull). Somente modelos, embeddings e reranker são dublês determinísticos, restritos ao arquivo de teste. Nenhum monkeypatch de rota, autenticação, pipeline ou persistência. Os casos de GET semeiam a linhagem pela pipeline real para isolar ausência da rota de consulta da ausência do POST.

## Desconexão e transações

ASGI `http.disconnect` cancela a task da pipeline. O produtor deve receber `asyncio.CancelledError`, aguardar seu tratamento/persistência sob proteção contra cancelamento externo e então liberar recursos. A pipeline existente grava status erro sem itens e relança o cancelamento. Não converter desconexão em final entregue fictício; o socket já não permite garantir entrega. Efeitos de uma thread já em execução não são interrompidos à força: aguardar drenagem segura, sem publicar resultado educacional tardio. Não manter tasks órfãs.

N2 da revisão de segurança: autenticação e CSRF terminam sua transação curta **antes** da criação da task de trabalho longo e do início do stream. Copiar apenas owner textual; não reter ORM ou sessão transacional no produtor. `require_csrf` já confirma sua leitura, mas qualquer leitura posterior de autenticação deve também ser confirmada; finalizador de dependência após o stream não resolve essa ressalva. Pools de auth e de linhagem permanecem separados. O teste envelhece last_seen_at, observa seu novo valor por conexão independente dentro da primeira chamada de modelo e adquire `FOR UPDATE NOWAIT` no token; heartbeat invisível ou lock pendente reprova.

## Consultas de linhagem

- `GET /lineage/items/{item_id}`: autenticação obrigatória; JSON `{lineage: ItemLineage, spans: {citation_id: [ResolvedSpan,...]}}`, serializando diretamente `ChatLineageRepository.get_item_lineage(owner,item_id)`. Inclui documento, versão, página quando exata (null em Markdown), seção, evidência e trace_id, sem fabricar proveniência.
- `GET /lineage/turns/{chat_turn_id}`: autenticação obrigatória; JSON `{turn: ChatTurnRecord, response: ChatResponse, items: [{lineage: ItemLineage, spans: ...},...]}`. Busca turno com filtro pelo owner antes de carregar filhos; itens entregues em ordem da resposta, usando o mesmo resolvedor de linhagem. Turnos sem itens retornam items vazio.
- Outro dono e ID inexistente: mesmo 404 e mesmo corpo de erro, sem revelar existência. Query owner_id não troca identidade. Nenhuma consulta necessita de CSRF por ser GET.

## Matriz dos casos

| Requisito | Casos em test_api_chat.py |
| --- | --- |
| API-CHT-001 | `order_headers_single_final_and_committed_graph` (ok/recusa/erro; E3 termina antes de E2; headers; final único; fontes e commit); `csrf_rejected_without_turn` (ausente/inválido/outra sessão); `auth_transaction_released_before_models` (N2); `disconnect_cancels_and_persists_error` (ASGI real, cancelamento, erro sem item) |
| AUT-OWN-001 | `data_routes_require_valid_session` (POST e ambos GET; sem cookie/cookie inválido); `body_owner_never_selected`; `query_owner_cannot_select_another_owner`; filtro do dono e spoof de query nos dois casos de linhagem |
| API-LIN-001 | `owner_sources_and_equal_404` (item/turno; domínio real semeia fontes; dono recebe proveniência completa; outro dono e inexistente têm mesmo 404) |

G3 continua pendente: hashes identificam a proposta de casos, não constituem aprovação humana. Fase 2 e prova real autenticada não executadas nesta entrega.

## Execução inicial

Estado: Bloqueado na primeira rodada para a prova de falha por rotas ausentes: 18 erros de setup por `API_ATTEMPT_KEY_SECRET` ausente, antes de atingir as rotas. Saída integral preservada em `g3-falhando-r1.txt`; não constitui a evidência vermelha pretendida. A fixture congelada não foi alterada; foi adicionada fixture autouse no teste novo, com segredo aleatório efêmero só de teste. Uma segunda rodada única foi autorizada pelo coordenador na mensagem Orca `msg_deae44a10305`, sem gravar segredo ou tocar .env.

## Resultado da rodada autorizada

Estado: Falhou — esperado nesta fase anterior ao produto. `uv run pytest apps/api/tests/test_api_chat.py -v --tb=short`: exit 1, **18 failed in 19.42s**, todos por HTTP 404 nas rotas ausentes, sem erro de setup. A preparação dos dois testes de linhagem executou com sucesso o domínio real e sua persistência antes da asserção HTTP; isso não é prova das rotas nem aprovação G3. O teste ASGI de desconexão falhou na asserção de HTTP 200 versus 404, antes de aguardar qualquer cancelamento.

Arquivos de casos propostos: `apps/api/tests/test_api_chat.py` (18 combinações) e `apps/api/tests/api_chat_support.py` (fixtures e dublês externos). Hashes finais em `g3-casos.txt`. Nenhum caso congelado, código de produto, contrato, migração, dependência ou .env alterado; nenhuma chamada real de LLM, commit, push ou deploy. Próximo passo: aprovação humana dos casos G3 e despacho da fase 2.

## Decisão humana R4 — texto puro e projeção pública

Autorização humana registrada nas tarefas: **“autorizo”** (A4/I2/A11), aplicada
após o snapshot f34b4d8. Esta seção atualiza o formato público acima.

Todos os campos textuais de resposta HTTP são **texto puro**: `POST /chat` (SSE),
`GET /lineage/items/{id}` e `GET /lineage/turns/{id}` não aplicam escape HTML nem
interpretam entidades. `<script>`, `<img onerror=...>`, aspas, `&` e entidades
literais como `&amp;` permanecem literais. O frontend deve inserir esses valores
**sempre com `textContent`**, nunca interpretá-los como HTML. Isso inclui item,
citação, fonte, título/seção, mensagens, motivos e detalhes de omissões.

E11 preserva a remoção anterior de controles/Unicode (categorias Cf e Cs e Cc,
exceto `\n` e `\t`) e a revalidação do schema. Ele deixa de escapar HTML nos
campos do item e na evidência entregue. O original da linhagem permanece literal;
a sanitização de controles é a única transformação de apresentação do E11.
O JSON conserva sua própria codificação de transporte; `\u003c`, se presente,
representa o mesmo caractere após parse e não é uma regra de escape HTML.

No JSON de `GET /lineage/turns/{id}`, `turn.input_verdict` passa a ser `null`,
quando ausente, ou **somente** `{"categoria": ...}` (categoria pode ser `null`).
Não expõe `motivo` nem `malicioso`. O registro persistido e o contrato interno
`ChatTurnRecord` conservam o veredito completo; só a projeção HTTP é reduzida.
Os demais campos do turno, `response` e `items` seguem iguais.

A sessão usa exclusivamente o cookie **`__Host-training_session`**, com
`Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/` e sem `Domain`. O nome antigo não é
aceito como sessão. HTTPS é obrigatório, inclusive para uma prova HTTP futura;
`cookie_secure=False` é rejeitado na configuração, pois violaria o prefixo Host.
A prova HTTP antiga é evidência do snapshot anterior, não prova deste cookie.

## R5 — limpeza de apresentação na linhagem

`GET /lineage/items/{id}` e `GET /lineage/turns/{id}` aplicam a função compartilhada
`training_contracts.rendering.clean_text` aos textos de apresentação: campos do
item, evidências, prompt, detalhes de omissões, mensagens/categoria e listas de
`section_path` (também em fontes/spans/response aninhados). Remove Cf/Cs e Cc,
exceto newline/tab, conservando HTML e entidades literais conforme ADR-0018.
A transformação não modifica o persistido, IDs ou offsets; a fonte/proveniência
original permanece íntegra para auditoria. A projeção de I2 continua só categoria.

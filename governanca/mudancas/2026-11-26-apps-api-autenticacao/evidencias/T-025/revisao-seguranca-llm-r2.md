Revisor: subagente `security-reviewer` (papel `seguranca-llm`), somente leitura, commit 512c159, 2026-11-26.

# Reavaliação de segurança da T-025 (R3/R4) e do A11 da T-024 — commit 512c159

## Veredito: **aprovado**

Os itens A1, A2, A3, A5, A6, A7, A8 e I1 estão corrigidos. As decisões humanas A4, I2 e A11 foram aplicadas como decididas. Não achei as regressões que você pediu para procurar:
- task órfã no executor próprio;
- cancelamento que não chega ao worker;
- limite por dono contornável dentro do processo;
- 413 contornável por corpo chunked;
- final de erro que vaza detalhe.

Ficaram três observações baixas e duas informativas, nenhuma bloqueante (seção final).

Esta revisão foi só leitura: `relatorio-r3.txt`, `relatorio-r4.txt`, `alteracoes-casos-r4.json`, os sete arquivos da API pedidos, `rendering.py` e o E11 do `chat.py`. Não rodei testes e não li o `.env`.

## Achados anteriores

**A1 (pipeline no event loop): corrigido**
- O preflight e as versões rodam no executor próprio (`chat_routes.py:64-67`).
- O turno inteiro roda em `ChatJob.run`, numa thread `training-chat-turn` com loop privado (`chat_execution.py:132-165`).
- O banco síncrono, o `_evidence_range`, o `_persist` e o `confirmed` não tocam mais o loop ASGI. Lá ficam só a fila e o `wrap_future`.
- O engine do chat foi limitado: pool de 4, sem conexões extras, espera de 2 s, `statement_timeout` de 5 s, `lock_timeout` de 1 s (`chat_runtime.py:25-27`).

**A2 (concorrência, prazo e admissão): corrigido**
- A admissão acontece antes do SSE: 1 turno ativo por dono (429) e 4 no total (503) (`chat_execution.py:39-46`, `chat_routes.py:53`).
- O prazo vale nos dois lados: `wait_for` no worker (`chat_execution.py:146-148`) e deadline no stream (`chat_routes.py:87,106-116`).
- O `asyncio.to_thread` do domínio usa um executor privado de 2 threads (`:136`). Ao final, `shutdown_default_executor` drena essas threads (`:162`), então nenhuma thread sobra depois do turno.
- O cliente genai agora tem timeout (`chat_runtime.py:33-34`).
- Os limites de concorrência valem por processo, como o README documenta (`README:158`).

**A3 (corpo sem limite): corrigido**
- `ChatBodyLimit` (`chat_body.py:13-42`) recusa `Content-Length` acima de 256 KiB antes de ler o corpo.
- Para corpo chunked, ele conta os bytes antes de acumular e recusa ao passar do limite (`:28-30`). Não há parse antes disso.
- Depois de entregar o corpo, o `receive` original é repassado (`:37-41`), então a detecção de desconexão continua funcionando.
- O `session_id` vazio ou longo demais recebe 422 antes do stream (`chat_routes.py:47-48`).

**A5 (stream cortado sem final): corrigido**
- O 422 sai antes do stream.
- As exceções do worker viram um final de erro com mensagem fixa (`chat_execution.py:155-157`, `:127-130`), e o mesmo vale no lado ASGI (`chat_routes.py:128-130`).
- Os logs registram só `type(exc).__name__`.

**A6 (fechamento dependente do coletor de lixo): corrigido**
- `ManagedGenerator.aclose` fecha o gerador e libera a vaga com proteção contra cancelamento (`chat_routes.py:156-168`), via `BackgroundTask` (`:172`).
- Revisei os caminhos da vaga: ela só é liberada depois de `await asyncio.shield(task)` (`:146-153`). `ManagedGenerator.aclose` só roda quando o gerador já terminou ou está parado num `yield`. Não achei caminho que libere a vaga com o worker ainda rodando.

**A7 (final de erro sem commit): corrigido**
- `confirmed` (`chat_execution.py:71-77`) confere o commit por conexão separada, filtrando dono e turno.
- Sem confirmação, o final de erro sai com `chat_turn_id` e `trace_id` nulos (`:127-130`).

**A8 (recusas gravadas sem limite): corrigido**
- O limite de taxa é aplicado no Postgres antes do SSE, com `pg_try_advisory_xact_lock` e timeouts locais (`chat_execution.py:52-68`).
- Requisições concorrentes do mesmo dono recebem 429, ou seja, falha fechada.

**I1 (cache dos GETs): corrigido**
- Os dois GETs enviam `no-store` (`chat_routes.py:198,207`).
- O 404 não leva o cabeçalho, mas também não leva dado.

## Decisões humanas

**A4 (texto puro em todas as rotas): aplicado**
- `rendering.py:10-22` remove só os caracteres Cf, Cs e os de controle (Cc), exceto newline e tab. Não faz escape nem decodifica entidades.
- O E11 continua usando `sanitize_item` (`chat.py:589-595`).
- O framing usa `ensure_ascii=True` (`chat_routes.py:28`), então não dá para injetar quadros SSE.
- A regra do `textContent` está documentada (`README:186`).

**I2 (só a categoria do veredito na linhagem): aplicado**
- O GET de turno devolve `input_verdict` como `{categoria}` ou `null` (`chat_routes.py:187-191`). `motivo` e `malicioso` não saem; o registro persistido continua íntegro.

**A11 (cookie `__Host-`): aplicado**
- `SESSION_COOKIE='__Host-training_session'` (`auth.py:22`), com `Path=/`, HttpOnly e SameSite=Lax (`auth.py:159-165`). Não há Domain, porque `cookie_domain` fica no padrão `None`.
- O fastapi-users repete `secure`, `path` e `domain` também no cookie de logout (`fastapi_users/.../cookie.py:54-60`), então o navegador aceita a remoção.
- `cookie_secure=False` é recusado (`config.py:55-56`).
- Não há fallback para o nome antigo.

## Observações remanescentes (não bloqueiam)

**R1 — Baixo (depende do deploy): o limite de 413 pode ser contornado com `--root-path`**
- **Onde:** `chat_body.py:14` compara `scope['path'] != '/chat'` literalmente.
- **Como acontece:** o uvicorn 0.54 monta `path = root_path + path` (`h11_impl.py:206,217`). O roteamento do Starlette remove o prefixo (`_utils.py:137-150`), mas o middleware não. Com `--root-path /api`, o middleware vê `/api/chat`, não aplica o limite, e a rota ainda casa.
- **Mitigação existente:** o README já pede `client_max_body_size 256k` no proxy (`README:138`).
- **Correção:** comparar `starlette._utils.get_route_path(scope)` (ou `scope['path'][len(scope.get('root_path','')):]`) com `/chat`.

**R2 — Informativo: com servidor ASGI 2.4, a vaga pode vazar**
- **Onde:** o Starlette 1.10, com `spec_version >= 2.4`, transforma `OSError` em `ClientDisconnect` antes de chamar o background (`starlette/responses.py:272-276`).
- **Risco:** se o gerador nunca tiver iterado, a vaga não é liberada (`chat_routes.py:156-172`), e o dono fica com 429 até reiniciar o processo.
- **Por que não acontece hoje:** o uvicorn instalado anuncia 2.3 em todos os protocolos.
- **Correção, se trocarem de servidor:** chamar `gen.aclose()` num `try/finally` em volta de `StreamingResponse.__call__`, por exemplo numa subclasse.

**R3 — Baixo: a vaga fica presa enquanto a drenagem não termina**
- **Onde:** `shutdown_default_executor` (`chat_execution.py:162`) e a espera protegida (`chat_routes.py:127,146-153`) não têm limite de tempo.
- **Risco:** [INFERÊNCIA] uma chamada do Qdrant ou do reranker travada numa thread `training-chat-io` segura a vaga do dono e uma vaga global sem limite (o `QdrantClient` é criado sem `timeout` explícito em `indexing.py:229`). Quatro threads assim deixam o chat indisponível (503) para todos.
- O worker documentou esse comportamento como intencional.
- **Correção:** passar um `timeout` explícito ao `QdrantClient` e registrar quando a drenagem demorar além do prazo.

**R4 — Informativo: o caminho de cancelamento pode anunciar o turno errado**
- **Onde:** `error_response(cancelled=True)` procura o turno de erro "mais recente da sessão" comparando `created_at` como texto (`chat_execution.py:102-107`). Os formatos não batem: `Z` de um lado, `+00:00` e microssegundos omitidos do outro.
- **Risco:** com vários processos, ou com um erro anterior no mesmo segundo, pode anunciar outro turno de erro do mesmo dono e da mesma sessão. Não vaza nada entre donos.
- **Correção:** o domínio expor o `chat_turn_id` do cancelamento, ou comparar por coluna `timestamptz`.

**R5 — Informativo: controles invisíveis ainda chegam pela linhagem**
- **Onde:**
  - `ItemLineage.content` e `evidence` (os originais), `turn.prompt` (bruto, sem a normalização do E1), `omissoes[].detalhe` e `fontes[].section_path` não passam pela limpeza de Cf/Cc.
  - Só os itens entregues no SSE são limpos (`chat.py:594`).
- **Risco:** com `textContent` não há XSS. Mas caracteres de direção, como U+202E, podem mudar visualmente o texto exibido na linhagem.
- **Correção:** aplicar a mesma limpeza na saída da linhagem, ou isolar a direção do texto no cliente (`unicode-bidi: isolate`).
# training-api

Autenticação própria com FastAPI, fastapi-users, Argon2id e sessões opacas no
Postgres da aplicação (ADR-0017), chat por SSE e consultas de linhagem do dono autenticado (T-025). Recuperação por e-mail e edição genérica de usuários ficam fora
desta versão.

Configure `APP_POSTGRES_*` e `API_*` a partir de `.env.example`. Aplique as migrações
com `training_lineage.migrate(engine, 'head')` usando um engine da linhagem antes
de iniciar a API; o servidor não migra o banco implicitamente. A revisão 0006 é
aditiva e seu downgrade recusa apagar qualquer conta existente.

Execute da raiz do workspace:

```sh
uv run --package training-api uvicorn training_api.app:app_from_env --factory
```

O cookie `training_session` usa HttpOnly, Secure e SameSite=Lax. HTTPS é necessário
com a configuração padrão; `API_COOKIE_SECURE=false` destina-se apenas ao ambiente
local HTTP. Login recebe formulário OAuth2 (`username`, `password`) e devolve
`csrf_token`; logout e troca de senha exigem esse valor em `X-CSRF-Token` junto ao
cookie. A troca de senha revoga todas as sessões da conta, inclusive a atual.

O engine assíncrono da autenticação tem pool próprio (5 conexões, até 5 adicionais),
separado dos engines síncronos da linhagem. Tentativas ficam no Postgres com chaves
SHA-256; a aplicação usa o endereço ASGI de origem. Atrás de proxy, configure no
Uvicorn os proxies confiáveis com `--proxy-headers --forwarded-allow-ips`; cabeçalhos
encaminhados não são interpretados pela aplicação.

As dependências públicas `training_api.auth.current_user`, `current_owner_id` e
`training_api.csrf.require_csrf` são reutilizáveis pelas rotas de dados da T-025.

Endurecimento da T-024 R3: `API_ATTEMPT_KEY_SECRET` é obrigatório, sem padrão;
gere um segredo aleatório e configure-o no `.env` antes de iniciar. A configuração
erra nomeando a variável quando ela está ausente. As chaves de tentativas usam
HMAC-SHA256 desse segredo e a normalização de e-mail do próprio Postgres. Trocar o
segredo reinicia os orçamentos associados às chaves anteriores; faça isso como uma
operação de configuração coordenada. Origens IPv6 compartilham o prefixo /64;
IPv4 usa /32. Ausência de endereço ASGI impede autenticação com 403.

Login e troca de senha reservam o orçamento em transações curtas com advisory
locks não bloqueantes; disputa recebe 429. As reservas contam como falhas enquanto
a senha é verificada sem conexão ou trava retida; no sucesso, são removidas.
Cancelamento ou erro conserva a reserva até a janela expirar. Uma falha de senha
atual compartilha o orçamento de login da conta e origem. Argon2 roda em threads
AnyIO; senhas têm limite superior de 1024 caracteres. Origin de outro site ou
Sec-Fetch-Site cross-site é recusado com 403 no login/cadastro; clientes sem esses
cabeçalhos continuam aceitos. Configure o proxy confiável corretamente para que a
URL ASGI usada na comparação de Origin represente a URL pública.

O banco armazena somente SHA-256 dos tokens de sessão e CSRF; cookie e resposta
mantêm os valores brutos. Além da validade absoluta, há expiração por inatividade
(`API_SESSION_IDLE_SECONDS`, 3600 por padrão); `last_seen_at` avança no máximo uma
vez por minuto. O relógio de leitura vem do Postgres. A função
`training_api.limits.purge_expired` remove tentativas fora de suas janelas e sessões
fora da validade absoluta ou de inatividade. Ela é chamada oportunisticamente antes das
transações de reserva/cadastro e no uso de sessão válida, em sessão própria e com
a periodicidade/lotes descritos nas R4/R5 abaixo; sem tráfego, a limpeza fica para a
próxima chamada. Não há serviço ou scheduler novo.

`/users/me` e o login enviam `Cache-Control: no-store`.
`API_DOCS_ENABLED=false` desliga `/docs` e `/redoc`, preservando `/openapi.json`.
A 0006 foi ajustada antes de aplicação em banco real; bancos descartáveis de teste
são recriados. O nome do cookie e a decisão sobre lista local de senhas comuns
permanecem sujeitos à decisão humana registrada na rodada de revisão.

Rodada R4: hash e verificação compartilham um `anyio.CapacityLimiter` exclusivo da
aplicação. `API_PASSWORD_HASH_CONCURRENCY` define o máximo simultâneo; quando não
configurado, o padrão é o número de núcleos, com mínimo de 2. A fila desse
limitador não retém conexões do banco. Dimensione o limite de acordo com memória
e CPU disponíveis (cada Argon2 usa memória própria).

A purga agora roda em sessão própria curta, fora das transações de reserva e de
autenticação. A execução oportunística ocorre no máximo uma vez por minuto por
instância de aplicação no processo, com lotes de até 100 linhas por tabela, repetidos dentro do orçamento descrito
na R5; `FOR UPDATE SKIP LOCKED` pula linhas ocupadas. Há índice em `last_seen_at` além de
`created_at`. Sem tráfego ou com grande acúmulo, o restante fica para os lotes dos
minutos seguintes. `current_user` e `require_csrf` confirmam sua transação antes
de retornar, permitindo iniciar SSE sem reter a conexão da autenticação.

Depois de verificar a senha correta, login e troca de senha removem a reserva
também em disputa NOWAIT, revogação concorrente da sessão ou outro erro posterior.
Antes dessa remoção, confirmam ou desfazem a transação de autenticação para liberar
qualquer conexão/lock; a limpeza é protegida de cancelamento. Senhas incorretas
continuam registradas no orçamento até o vencimento da janela.

Rodada R5: `API_PASSWORD_HASH_QUEUE_MULTIPLIER` limita a espera ao múltiplo das
vagas do Argon2 (padrão 4). A admissão compartilhada contabiliza execução e espera
antes do primeiro await; excedente recebe 503 sem chamar hash/verificação e sem
reter conexão. O contador é liberado em finally também no cancelamento/erro.
Conta inativa conserva as reservas de login, tanto com senha certa quanto errada;
apenas credencial de conta ativa permite a limpeza após verificação.

A purga repete lotes completos, alternando as tabelas, até um lote vir incompleto
ou atingir orçamento aproximado de 200 ms. Usa `ctid = ANY(ARRAY(SELECT ... FOR
UPDATE SKIP LOCKED))` para localizar as linhas e mantém a sessão própria e a
periodicidade de um minuto. O orçamento é verificado entre comandos SQL; uma
consulta já em curso pode ultrapassá-lo. Registros restantes ficam para a execução
seguinte.

Rodada R6: a recusa de admissão do hasher lança `PasswordBudgetExceeded` (503),
distinta de falha de credencial. Login e troca de senha desfazem a transação e
removem suas reservas nessa recusa, sem consumir os limites por conta/origem.
Erros de senha efetivamente verificados continuam registrados. O rollback ocorre
antes da sessão curta de limpeza, protegida de cancelamento.

## Chat e linhagem

`POST /chat` recebe `ChatRequest` (`session_id`, `mensagem`) com cookie e
`X-CSRF-Token`. A identidade vem da sessão; os documentos disponíveis são
resolvidos no Postgres por owner e estado ready do protocolo de ingestão.
`GET /lineage/items/{item_id}` e `GET /lineage/turns/{chat_turn_id}` consultam
a linhagem do dono; outro dono e inexistente recebem o mesmo 404.

No lifespan, a composição real exige a configuração da CLI em `.env`
(modelos/chaves, `PROMPT_SOURCE=offline`, índice Qdrant e banco da aplicação),
mais `CHAT_RERANKER_CALIBRATION` apontando ao manifesto calibrado existente.
Não há mudança de modelo ou calibração automática. Ausência ou falha de
configuração deixa `POST /chat` explicitamente indisponível (503), preservando
autenticação e GET de linhagem. O workspace completo deve estar instalado
(`uv sync --all-packages`); nenhuma biblioteca nova é necessária.

A composição mantém índice, clientes e reranker no lifespan e cria pipeline e
orçamento de chamadas por turno via `app.state.chat_pipeline_factory`. Tracing
local sem exportação Langfuse; embeddings e agentes usam uma tentativa.

SSE usa `progress` (`stage`, `sequence`, `label`, `seconds`) e um único `final`
com `ChatResponse` após persistência. Etapas paralelas são ordenadas e reparos
são agregados; etapas E8–E12 aguardam o encerramento para impedir duplicação.
Cabeçalhos: `Cache-Control: no-cache` e `X-Accel-Buffering: no`; configurar o
proxy sem buffering/compressão e com timeout acima da duração de um turno.
Desconexão cancela a task e aguarda o tratamento de cancelamento do domínio.
Não há retomada do stream na v1.

### Limites e execução do chat (T-025 R3)

Antes de parsear JSON, o middleware ASGI de `POST /chat` impõe **256 KiB**,
contando também corpo chunked. Configure ainda `client_max_body_size 256k;` no
nginx/proxy, além de desabilitar buffering e compressão no SSE. A rota recusa
`session_id` vazio após strip ou acima de 256 caracteres sem mudar ChatRequest.

Parâmetros `API_*` opcionais, inteiros positivos:

| Variável | Padrão | Efeito |
| --- | --- | --- |
| `API_CHAT_MAX_ACTIVE_PER_OWNER` | 1 | Turnos ativos por dono neste processo; excedente 429 antes do SSE |
| `API_CHAT_MAX_ACTIVE_GLOBAL` | 4 | Turnos ativos neste processo; excedente 503 antes do SSE |
| `API_CHAT_TURN_TIMEOUT_SECONDS` | 180 | Prazo do trabalho; cancela a pipeline e aguarda persistência/drenagem |
| `API_CHAT_SESSION_ID_MAX_LENGTH` | 256 | Tamanho máximo da sessão de chat |
| `API_CHAT_MAX_REQUESTS_PER_WINDOW` | 20 | Pedidos aceitos por dono e janela na borda, inclusive recusa E1 |
| `API_CHAT_REQUEST_WINDOW_SECONDS` | 60 | Janela da taxa na borda |
| `API_CHAT_POOL_SIZE` | 4 | Conexões do engine do chat, sem overflow |
| `API_CHAT_POOL_TIMEOUT_SECONDS` | 2 | Espera máxima por conexão do pool do chat |

O limite de taxa na borda é persistido no Postgres em um namespace separado de
`chat_rate_event`; o E1 mantém o seu próprio limite inalterado. A leitura/reserva
usa lock não bloqueante e transação curta antes de enviar HTTP 200. Os limites
de turnos ativos são locais ao processo; ao usar múltiplos processos, dimensione
o total e a proteção do proxy de acordo. Não há promessa de quota de concorrência
distribuída nesta implementação.

Cada turno roda em thread dedicada com loop asyncio próprio. Admissão de chamadas
e recuperação usam o executor privado desse loop, sem ocupar o executor padrão
do ASGI. O prazo também roda no worker, inclusive enquanto o envio ao cliente
está bloqueado. Cancelamento é propagado por `call_soon_threadsafe`; cliente
lento/desconectado fecha explicitamente o gerador pela tarefa de background.
Trabalho síncrono já em execução não é interrompido à força: a vaga fica ocupada
até a drenagem. O genai tem timeout explícito baseado em `LLM_TIMEOUT_SECONDS`;
SQL do engine de chat limita conexão (2s), statement (5s) e lock (1s).

Exceção que escape do domínio após HTTP 200 termina com um único `final` de erro
sanitizado; os logs guardam só o tipo/categoria da exceção, sem mensagem ou
traceback. A API confirma o commit por conexão separada antes de anunciar um
turno. Se nem a persistência de erro for possível, o `final` leva `status=erro`,
sem itens/fontes e com `chat_turn_id=null` e `trace_id=null`: não anuncia um ID
inexistente. GETs de linhagem respondem com `Cache-Control: no-store`.

A CLI mantém somente o agregado `E5_E7` no seu callback de progresso, como antes;
o callback adicional `stage_progress` de E5/E6/E7 é habilitado apenas pela API.
A regra de escape e os campos públicos da linhagem permanecem conforme o contrato
atual; A4 e I2 aguardam decisão humana e não foram alterados nesta rodada.

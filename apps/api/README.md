# training-api

Autenticação própria com FastAPI, fastapi-users, Argon2id e sessões opacas no
Postgres da aplicação (ADR-0017). O chat SSE e a consulta de linhagem entram na
T-025; esta implementação monta somente cadastro, login, logout, usuário atual e
troca de senha. Recuperação por e-mail e edição genérica de usuários ficam fora
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
a periodicidade/lotes descritos na R4 abaixo; sem tráfego, a limpeza fica para a
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
instância de aplicação no processo, com um lote de até 100 linhas por tabela;
`FOR UPDATE SKIP LOCKED` pula linhas ocupadas. Há índice em `last_seen_at` além de
`created_at`. Sem tráfego ou com grande acúmulo, o restante fica para os lotes dos
minutos seguintes. `current_user` e `require_csrf` confirmam sua transação antes
de retornar, permitindo iniciar SSE sem reter a conexão da autenticação.

Depois de verificar a senha correta, login e troca de senha removem a reserva
também em disputa NOWAIT, revogação concorrente da sessão ou outro erro posterior.
Antes dessa remoção, confirmam ou desfazem a transação de autenticação para liberar
qualquer conexão/lock; a limpeza é protegida de cancelamento. Senhas incorretas
continuam registradas no orçamento até o vencimento da janela.

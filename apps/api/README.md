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

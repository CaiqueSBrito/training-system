# T-024 — interface planejada da autenticação (G3)

Data: 2026-11-26 · Papel: rag-backend (executor anthropic, revisor google; autorização excepcional do humano `retomada=autorizar-anthropic-google`).

Este documento fixa a interface pública que os casos de `apps/api/tests/` exercitam. A implementação só começa depois do G3. Fontes: delta desta mudança (AUT-*), ADR-0017, ADR-0010, ADR-0011, ADR-0008.

## Construção da app (usada pelos testes)

| Símbolo | Forma |
| --- | --- |
| `training_api.config.ApiSettings` | configuração imutável; campos abaixo; `ApiSettings.from_env(env_file=".env")` lê `APP_POSTGRES_*` (via `training_lineage.database_url`) e `API_*` |
| `training_api.app.create_app(settings) -> FastAPI` | monta rotas, engine assíncrono (`postgresql+psycopg`, pool próprio, ADR-0017 item 12) e o lifespan que o descarta |
| `training_api.auth.current_user` / `current_owner_id` | dependências FastAPI: usuário ativo da sessão; `owner_id = str(user.id)`; sem sessão válida → 401. Toda rota de dados (inclusive `POST /chat` e a linhagem da T-025) usa `current_owner_id`; nenhuma rota lê `owner_id` de corpo ou query |
| `training_api.csrf.require_csrf` | dependência das rotas autenticadas com método POST/PUT/PATCH/DELETE; ordem: primeiro a sessão (401), depois o CSRF (403) |

Execução: `uvicorn training_api.app:app_from_env --factory` (a ser confirmado na implementação; os testes não dependem disso).

## Cookie e cabeçalho

| Item | Valor |
| --- | --- |
| Cookie de sessão | `training_session`, opaco (token aleatório do `DatabaseStrategy`, não JWT), `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`, `Max-Age` = validade da sessão |
| Cabeçalho CSRF | `X-CSRF-Token` |
| Origem para limite | endereço do cliente no escopo ASGI (`request.client.host`); a API não lê `X-Forwarded-For` por conta própria — atrás de proxy, isso é do `uvicorn --proxy-headers --forwarded-allow-ips` |

## Rotas

| Rota | Entrada | Sucesso | Falhas |
| --- | --- | --- | --- |
| `POST /auth/register` | JSON `{"email", "password"}`; campos de privilégio (`is_superuser`, `is_active`, `is_verified`) ignorados | 201 `{"id": "<uuid>", "email", "is_active", "is_superuser", "is_verified"}`; nunca senha nem hash | 400 `{"detail": "REGISTER_USER_ALREADY_EXISTS"}` (comparação sem distinguir maiúsculas); 400 `{"detail": {"code": "REGISTER_INVALID_PASSWORD", "reason": "..."}}`; 422 validação; 429 acima do limite por origem (inclusive para e-mail existente, mesmo corpo) |
| `POST /auth/login` | formulário `username`, `password` (OAuth2 do `fastapi-users`); não exige CSRF; cookie prévio ignorado | 200 `{"csrf_token": "<token>"}` + `Set-Cookie: training_session=<novo token>`; sempre token e CSRF novos | 400 `{"detail": "LOGIN_BAD_CREDENTIALS"}`, idêntico para e-mail inexistente e senha errada, sem `Set-Cookie`; 429 acima do limite (por conta ou origem), mesmo com senha correta, corpo igual para conta existente e inexistente |
| `POST /auth/logout` | cookie + `X-CSRF-Token` | 204; apaga o token da sessão (e seu CSRF); `Set-Cookie` que expira `training_session` | 401 sem sessão válida; 403 sem CSRF ou CSRF de outra sessão, sem efeito |
| `GET /users/me` | cookie | 200 `{"id", "email", "is_active", "is_superuser", "is_verified"}` do usuário da sessão; query ignorada | 401 sem sessão, sessão revogada ou vencida |
| `POST /auth/change-password` | cookie + `X-CSRF-Token`; JSON `{"current_password", "new_password"}` | 204; grava novo hash Argon2id e apaga **todos** os tokens da conta (inclusive o desta sessão) | 400 senha atual errada ou nova fora da política; 401; 403; 422 se campos extras forem proibidos (`owner_id` etc. nunca são aplicados) |

Não montadas (ADR-0017 item 11): `forgot-password`, `reset-password`, `request-verify-token`, `verify`, `PATCH /users/me`, `/users/{id}`. Ficam para a T-025: `POST /chat` (SSE) e as rotas de linhagem, ambas com `current_owner_id` e `require_csrf` no POST.

## Migração 0006 planejada (`packages/lineage`, aditiva)

`revision = '0006'`, `down_revision = '0005'`; 0001–0005 intactas. Tabelas também declaradas em `training_lineage.tables` (o `fastapi-users-db-sqlalchemy` recebe modelos mapeados sobre essas tabelas).

| Tabela | Colunas | Restrições |
| --- | --- | --- |
| `app_user` | `id UUID PK`, `email VARCHAR(320) NOT NULL`, `hashed_password VARCHAR(1024) NOT NULL`, `is_active BOOL NOT NULL DEFAULT true`, `is_superuser BOOL NOT NULL DEFAULT false`, `is_verified BOOL NOT NULL DEFAULT false`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` | índice único em `lower(email)`; `CHECK (hashed_password LIKE '$argon2id$%')` |
| `auth_access_token` | `token VARCHAR(43) PK`, `user_id UUID NOT NULL FK app_user ON DELETE CASCADE`, `csrf_token VARCHAR(64) NOT NULL UNIQUE`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` (índice) | sessão vale enquanto `created_at > now() - validade` |
| `auth_attempt` | `id BIGSERIAL PK`, `kind TEXT` (`login_failure`, `register`), `scope TEXT` (`account`, `origin`), `key TEXT` (sha256 hex do e-mail normalizado ou da origem; nunca o e-mail cru), `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` | `CHECK` dos domínios; índice `(kind, scope, key, created_at)`; contagem por janela deslizante, válida entre processos |

Downgrade `0006 → 0005`: recusa (exceção, nada apagado) se `app_user` tiver qualquer linha; com a tabela vazia, remove as três tabelas.

## Limites padrão (configuráveis por `ApiSettings` e `.env`)

| Campo | Variável | Padrão | Nos testes |
| --- | --- | --- | --- |
| `database_url` | `APP_POSTGRES_*` | — | URL do banco descartável |
| `cookie_secure` | `API_COOKIE_SECURE` | `true` | `true` |
| `session_lifetime_seconds` | `API_SESSION_LIFETIME_SECONDS` | 604800 (7 dias) | 3600 |
| `password_min_length` | `API_PASSWORD_MIN_LENGTH` | 12 | 12 |
| `login_max_failures_per_account` | `API_LOGIN_MAX_FAILURES_PER_ACCOUNT` | 5 | 3 |
| `login_max_failures_per_origin` | `API_LOGIN_MAX_FAILURES_PER_ORIGIN` | 20 | 6 |
| `login_window_seconds` | `API_LOGIN_WINDOW_SECONDS` | 900 | 900 |
| `register_max_per_origin` | `API_REGISTER_MAX_PER_ORIGIN` | 5 | 3 |
| `register_window_seconds` | `API_REGISTER_WINDOW_SECONDS` | 3600 | 3600 |

Contam para o limite de login só as falhas; acima do limite a tentativa é recusada com 429 **antes** de verificar a senha. Contam para o cadastro todas as tentativas da origem. As variáveis `API_*` entram no `.env.example` na implementação.

## Casos × cenários

| Requisito | Cenário do delta | Casos em `apps/api/tests/test_api_auth.py` |
| --- | --- | --- |
| AUT-CAD-001 | conta criada com hash `$argon2id$` | `test_AUT_CAD_001_cadastro_cria_conta_com_hash_argon2id`, `..._cadastro_nao_aceita_privilegio_do_corpo` |
| AUT-CAD-001 | e-mail repetido sem dados da conta | `..._email_repetido_recusado_sem_dados_da_conta` (2 grafias) |
| AUT-CAD-001 | senha curta sem criar conta | `..._senha_abaixo_do_minimo_recusada_sem_criar_conta` |
| AUT-SES-001 | sessão no banco + cookie com 3 atributos | `test_AUT_SES_001_login_cria_sessao_no_banco_e_cookie_seguro` |
| AUT-SES-001 | resposta igual inexistente × senha errada | `..._falha_identica_para_email_inexistente_e_senha_errada` |
| AUT-SES-001 | token novo, sem fixação | `..._login_sempre_emite_token_novo_sem_fixacao` |
| AUT-SES-002 | logout → 401 no mesmo cookie | `test_AUT_SES_002_logout_revoga_o_mesmo_cookie`, `..._logout_revoga_so_a_propria_sessao` |
| AUT-SES-002 | troca de senha revoga as duas sessões | `..._troca_de_senha_revoga_todas_as_sessoes`, `..._troca_de_senha_exige_senha_atual_e_politica` |
| AUT-SES-002 | sessão vencida → 401 | `..._sessao_vencida_recebe_401`, `..._cookie_desconhecido_recebe_401` (4 valores) |
| AUT-CSR-001 | sem cabeçalho → 403 sem efeito | `test_AUT_CSR_001_sem_cabecalho_csrf_recebe_403_sem_efeito` (logout, troca de senha). **`POST /chat` sem CSRF e "nenhum turno criado": T-025** |
| AUT-CSR-001 | CSRF de outra sessão → 403 | `..._csrf_de_outra_sessao_ou_invalido_recebe_403` (outro usuário, outra sessão do mesmo usuário, aleatório, vazio × 2 rotas), `..._csrf_nao_vale_sem_cookie`, `..._leitura_nao_exige_csrf` |
| AUT-LIM-001 | login N+1 → 429 com senha correta | `test_AUT_LIM_001_login_por_conta_429_mesmo_com_senha_correta`, `..._limite_por_conta_nao_revela_se_a_conta_existe`, `..._login_por_origem_429_para_qualquer_conta`, `..._abaixo_do_limite_login_correto_passa` |
| AUT-LIM-001 | cadastro por origem → 429 | `..._cadastro_por_origem_429` |
| AUT-OWN-001 | `owner_id` do corpo ignorado/recusado | `..._owner_id_do_corpo_nunca_e_usado` (troca de senha), `..._dono_vem_da_sessao_e_nao_da_query`, `..._owner_id_e_o_uuid_textual_do_usuario`. **No chat (turno gravado com o dono da sessão): T-025** |
| AUT-OWN-001 | sem cookie → 401 | `..._rota_de_dados_sem_sessao_recebe_401` (3 rotas), `..._rotas_de_email_e_edicao_de_usuario_nao_montadas`. **`POST /chat` e linhagem sem cookie: T-025** |

Os cenários marcados como T-025 dependem de `POST /chat` e da consulta de linhagem, que só existem na T-025; não foi criada rota de teste para simulá-los. Na T-025 eles usam a mesma `current_owner_id` e `require_csrf` daqui.

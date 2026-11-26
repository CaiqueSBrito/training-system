Revisor: subagente `security-reviewer` do omp (papel `seguranca-llm`, anthropic), somente leitura, commit ccd43dd, 2026-11-26.

# Revisão de segurança da T-024 (autenticação da apps/api), commit ccd43dd

## Veredito: reprovado

O motivo é só o achado A1: com cerca de 10 requisições simultâneas de login vindas de um único IP, sem nenhuma credencial, um atacante para todas as rotas que usam o banco de autenticação. As outras exigências de autenticação e sessão estão corretamente implementadas: hash Argon2id, respostas iguais no login, sessão nova a cada login, revogação, cookie, CSRF, `owner_id` e o downgrade da 0006. Corrigido o A1 e com um teste de concorrência passando, o veredito passa a **aprovado com ressalvas** (A2 a A11).

Esta revisão foi só leitura de código. Não rodei nenhum teste nem payload. Onde escrevi [INFERÊNCIA], a conclusão vem do código e não de execução.

---

## Achados

### A1 — Alto: o limite de login segura uma conexão do banco enquanto espera a trava, e quem tem a trava precisa de uma segunda conexão
- **Onde:** `apps/api/src/training_api/limits.py:54-66` (`login_attempt`), `apps/api/src/training_api/app.py:40-43` (pool de 5 conexões mais 5 extras, espera padrão de 30 s) e `app.py:83-84`. A segunda sessão vem de `auth.py:37-48` (`get_session` via `get_user_manager`).
- **Caminho de exploração [INFERÊNCIA]:**
  1. Cada login abre uma sessão de tentativas, pega uma conexão do pool e espera em `pg_advisory_xact_lock` da conta e depois da origem. Todas as requisições de um mesmo IP disputam a mesma trava de origem.
  2. Quem consegue a trava chama `manager.authenticate`. Isso usa outra sessão (`get_session`), que só pede conexão nesse momento, porque o SQLAlchemy conecta de forma preguiçosa.
  3. Com 10 ou mais requisições simultâneas do mesmo IP, as 10 conexões do pool estão presas em requisições esperando a trava. Quem tem a trava fica até 30 s esperando conexão e recebe 500.
  4. O 500 desfaz a transação, então a falha **não é registrada**. O limite por origem nunca chega ao 429, e a próxima requisição na fila repete o ciclo.
  5. Enquanto isso, `/users/me`, logout, cadastro e, na T-025, `POST /chat` e a linhagem esperam conexão e falham.
- **Impacto:** a API inteira do processo fica indisponível, antes de qualquer autenticação.
- **Correção sugerida:**
  - Não manter a trava durante a verificação da senha. Abordagem de reserva: em uma transação curta, travar, contar (contando também as reservas pendentes), inserir a falha antes de verificar e confirmar. Depois verificar a senha sem trava e, se der certo, apagar a reserva.
  - Se a trava continuar, usar uma única sessão e conexão por login, mais `pg_try_advisory_xact_lock` ou `SET LOCAL lock_timeout`, recusando com 429 quando houver disputa.
- **Teste:** 12 ou mais `POST /auth/login` simultâneos do mesmo `client`, medindo ao mesmo tempo o tempo de resposta de `/users/me` de outra origem e a gravação das falhas.

### A2 — Médio: o limite por origem usa o IP exato, e com IPv6 basta trocar de endereço
- **Onde:** `limits.py:15-17`.
- **Problema:** um atacante com um bloco IPv6 /64 (2^64 endereços) passa a ter uma origem nova a cada requisição.
  - O cadastro só tem limite por origem, então fica liberada a criação de contas em massa (AUT-LIM-001).
  - No login sobra só o limite por conta. O atacante ainda consegue bloquear qualquer conta em escala (ver A11) e gerar trabalho Argon2 sem limite (ver A8).
- **Correção:** agregar IPv6 por /64 na chave de origem (`ipaddress.ip_network(f'{host}/64', strict=False)`) e manter IPv4 por /32.

### A3 — Baixo (depende do deploy): origem atrás de proxy ou socket unix
- **Onde:** `limits.py:17` e `apps/api/README.md:28-30`.
- **Problemas:**
  - Sem `--forwarded-allow-ips` configurado para o proxy real, todos os clientes aparecem com o IP do proxy. O limite fica global: 20 falhas a cada 15 min derrubam o login de todos, e 5 cadastros por hora valem para todos.
  - Com `--uds`, `request.client` é `None` e todos caem na mesma chave `''`.
  - Com `--forwarded-allow-ips='*'` e a API exposta diretamente, `X-Forwarded-For` pode ser forjado e o limite por origem deixa de valer.
- **Correção:** fixar no artefato de deploy o IP exato do proxy confiável. Recusar o login (falhar fechado) quando `request.client` for `None`, em vez de usar a chave `''`.

### A4 — Médio-baixo: troca de senha sem limite de tentativas da senha atual
- **Onde:** `app.py:113-125`, com `verify_and_update` na linha 123.
- **Problema:** quem tem um cookie e um token CSRF roubados (por exemplo via XSS) pode testar a senha atual sem limite. Se acertar, troca a senha e assume a conta de forma permanente. Cada tentativa também custa um Argon2. Vai contra a proteção contra automação em todo ponto de verificação de credencial (ASVS 5.0, V6.3).
- **Correção:** contar `INVALID_CURRENT_PASSWORD` na mesma chave `login_failure`/`account` (e de origem) e verificar o limite antes de verificar a senha.

### A5 — Baixo [INFERÊNCIA, depende da collation]: a chave da conta é normalizada de um jeito e a busca no banco de outro
- **Onde:** `limits.py:56` usa `email.strip().lower()` do Python. `database.py:46` busca com `lower()` do Postgres.
- **Problema:** para `İ` (U+0130), o `lower()` do Python dá `i̇` (dois code points). O `lower()` do Postgres com collation libc ou musl provavelmente dá `i`.
  - Então `vİctim@x.com` autentica a conta `victim@x.com`, mas cai em outra chave de limite.
  - O orçamento por conta é multiplicado por 2^k, sendo k o número de letras "i" no e-mail.
- **Correção:** calcular a chave pela mesma função usada na busca, por exemplo `encode(sha256(convert_to(lower(:email),'UTF8')),'hex')` no SQL, ou recusar `username` que não seja ASCII ou que não passe pela normalização de `EmailStr`.

### A6 — Baixo: o login aceita requisição forjada de outro site (login CSRF)
- **Onde:** `app.py:77-99`.
- **Problema:** o formulário `x-www-form-urlencoded` é uma requisição simples, que não dispara verificação CORS. Um POST de navegação de outro site consegue gravar um cookie `SameSite=Lax`. Assim, um site malicioso pode logar a vítima na conta do atacante. O impacto é pequeno hoje, porque o token CSRF só aparece no corpo da resposta, mas vale corrigir antes do `apps/web`.
- **Correção:** recusar com 403 quando `Origin` ou `Sec-Fetch-Site` indicarem outro site em `/auth/login` e `/auth/register`. Como defesa extra, fazer o mesmo em todo método que altera estado.

### A7 — Baixo: a política de senha só verifica o tamanho mínimo
- **Onde:** `auth.py:29-33`.
- **Problema:** não há verificação contra senhas comuns ou vazadas (ASVS 5.0, 6.2.4, nível L1).
- **Correção:** comparar com uma lista local das senhas mais comuns. É um arquivo de dados, não uma biblioteca, mas a decisão é do humano.

### A8 — Baixo: Argon2 roda de forma síncrona e bloqueia o servidor
- **Onde:** `app.py:84` e `app.py:123` (verificação), `app.py:129` (hash da senha nova). No cadastro, o hash é feito por `BaseUserManager.create`.
- **Problema:** cada hash ou verificação (64 MiB, t=3) bloqueia o servidor assíncrono inteiro do processo. Somado a A2 e A4, vira uma forma de esgotar a CPU.
- **Correção:** sobrescrever `authenticate` e `create` no `UserManager` e rodar o hash em uma thread separada (`anyio.to_thread.run_sync`).

### A9 — Baixo: sessão sem expiração por inatividade
- **Onde:** `auth.py:44-48` e `config.py:16`.
- **Problema:** só existe a validade absoluta de 7 dias contados de `created_at`. Não há expiração por inatividade (ASVS 5.0, 7.3.1).
- **Correção:** adicionar `last_seen_at` com expiração por inatividade (por exemplo 30 a 60 min, atualizada no máximo uma vez por minuto) ou documentar a aceitação do risco.

### A10 — Baixo: segredos sem proteção no banco e dados sem purga
- **Onde:** `0006_auth.py:22-35` e `tables.py:24-44`.
- **Problemas:**
  - O token de sessão e o token CSRF ficam gravados em texto claro. Quem conseguir ler a tabela sequestra todas as sessões.
  - `auth_attempt` usa SHA-256 sem segredo. Para IPv4 (2^32 valores) e para e-mails conhecidos, a chave pode ser revertida por força bruta ou dicionário, então continua sendo dado pessoal.
  - Nem tokens vencidos nem tentativas antigas são apagados, e as tabelas crescem sem limite (pior com A2).
- **Correção:**
  - Guardar no banco `sha256(token)` (e o mesmo para o CSRF), com o cookie levando o valor bruto.
  - Gerar as chaves de tentativa com HMAC-SHA256 e segredo vindo de `API_*`.
  - Criar uma purga periódica do que estiver fora da janela ou da validade.

### A11 — Baixo: prefixo e trava do cookie
- **Onde:** `auth.py:13` e `config.py:15`.
- **Problemas:**
  - `training_session` não usa os prefixos `__Host-` ou `__Secure-` (ASVS 5.0, 3.3.1).
  - `API_COOKIE_SECURE=false` é aceito sem nenhuma trava de ambiente; só a documentação avisa.
- **Correção:** renomear para `__Host-training_session` (já atende Secure, Path=/ e sem Domain). Recusar `cookie_secure=false` fora de um modo local explícito.

### Informativos e riscos já aceitos (sem ação obrigatória)
- **I1:** `/users/me` (`app.py:101-103`) não envia `Cache-Control: no-store`. O login envia.
- **I2:** `/docs`, `/redoc` e `/openapi.json` ficam montados por padrão (`app.py:53`). O OpenAPI é exigido por API-CTR-001, mas convém desligar as páginas de documentação em produção.
- **I3, aceito pelo item 9 da ADR-0017:** o cadastro com e-mail existente responde `400 REGISTER_USER_ALREADY_EXISTS`, o que revela que o e-mail está em uso. O limite por origem que contém isso fica mais fraco por causa de A2. Sem verificação de e-mail, alguém pode registrar o e-mail de outra pessoa antes dela.
- **I4, aceito por AUT-LIM-001:** o bloqueio por conta (5 falhas em 15 min, 429 mesmo com a senha correta) permite bloquear contas específicas de propósito.
- **I5:** a resposta 422 do cadastro devolve o `input`, que pode incluir a senha, ao próprio cliente. Não há vazamento para terceiros.

---

## Controles verificados e aprovados
- **Argon2id:** o `Argon2Hasher` do pwdlib usa `argon2.Type.ID` por padrão. O `CHECK app_user_argon2id` (na migração e em `tables.py`) bloqueia qualquer hash que não seja `$argon2id$`, mesmo com bcrypt na lista de verificação. Hash antigo é refeito dentro do `authenticate`.
- **Enumeração no login:**
  - E-mail inexistente, senha errada e conta inativa recebem o mesmo `400 LOGIN_BAD_CREDENTIALS`.
  - Para e-mail inexistente, um hash falso é calculado (`manager.authenticate`).
  - O 429 sai antes de verificar a senha e não depende de a conta existir.
  - O 429 do cadastro sai antes de ler o corpo e conta todas as tentativas, inclusive as que dão 422.
- **Fixação:** todo login grava um token novo (`secrets.token_urlsafe()`, 43 caracteres). Um cookie anterior nunca vira sessão autenticada.
- **Revogação:**
  - O logout apaga a linha da sessão. O commit acontece antes de a resposta sair, por causa de `scope='function'`.
  - A troca de senha grava o hash novo e apaga todos os tokens da conta na mesma transação, com `SELECT … FOR UPDATE`.
  - O login também trava a linha do usuário (`database.py:46`), então não sobra sessão criada com o hash antigo.
- **Cookie:** HttpOnly, Secure (padrão), SameSite=Lax, Path=/, Max-Age igual à validade. A resposta do login tem `no-store`.
- **CSRF:**
  - Gerado com `token_urlsafe(32)`, um por sessão, coluna `UNIQUE`, buscado pelo cookie da própria sessão.
  - Comparação em tempo constante com `compare_digest`; recusa cabeçalho vazio ou com mais de 64 caracteres.
  - A sessão é verificada antes (401) e o CSRF depois (403). Cobre POST, PUT, PATCH e DELETE em logout e troca de senha; GET não exige.
  - Não há CORSMiddleware, então o cabeçalho customizado também força a verificação prévia do navegador.
  - Atenção: a cobertura depende de cada rota declarar a proteção, e a T-025 precisa declará-la em `POST /chat`.
- **Limites (exceto A1, A2, A3 e A5):** a contagem e o registro ficam em série sob `pg_advisory_xact_lock`, sempre na ordem conta → origem, o que evita impasse entre as duas travas. O login conta só falhas. A chave é SHA-256 em hexadecimal, garantida por `CHECK`. `X-Forwarded-For` não é lido pela aplicação.
- **Validade:** `read_token` filtra `created_at >= now - lifetime`. O tamanho 43 é verificado antes de consultar o banco. Usuário inativo recebe 401.
- **Rotas:** só existem cadastro, login, logout, `/users/me` (GET) e troca de senha. Nenhum roteador do fastapi-users está montado: não há reset, verify, `PATCH /users/me` nem `/users/{id}`.
- **owner_id:** `current_owner_id = str(user.id)`. `PasswordChange` tem `extra='forbid'` e o cadastro usa `safe=True`, que ignora campos de privilégio.
- **Erros e logs:** nenhum `print` ou `logging` na API. Os dois engines usam `hide_parameters=True`. A URL do banco mascara a senha e o modo debug está desligado. A violação de unicidade (23505) vira 400 genérico.
- **Downgrade da 0006:** faz `LOCK TABLE app_user IN ACCESS EXCLUSIVE MODE` e lança exceção (23514) se houver contas, sem apagar nada. Com a tabela vazia, remove só as três tabelas da 0006. `tables.py` bate com a migração.

## Escopo lido
- `apps/api/src/training_api/{app,auth,csrf,limits,database,config}.py`
- `packages/lineage/.../versions/0006_auth.py`, `tables.py:1-45` e `config.py`
- O delta AUT-*, a ADR-0017, `evidencias/T-024/interface.md` e `relatorio-r2.txt`, e `apps/api/README.md`
- Código instalado de fastapi-users 15.0.5 (`manager.authenticate`/`create`, `DatabaseStrategy`, `PasswordHelper`), do `fastapi_users_db_sqlalchemy.access_token`, do Argon2 do pwdlib 0.3.0 e as versões no `uv.lock`

Nenhum arquivo foi alterado e o `.env` não foi lido.
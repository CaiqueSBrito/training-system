# T-024 — revisão Anthropic (somente leitura)

Data: 2026-11-26 · Revisor: anthropic (Claude Opus 5.5) · Orca `task_4d7adb8575df` / `ctx_f28d38228486`
Objeto: commit `ccd43dd` (implementação OpenAI, `ctx_ff96e4bceb6d` / `ctx_283cbbb6916a`).
Escopo: implementação em `apps/api/src/training_api/*`, migração 0006, `training_lineage/tables.py` e testes de linhagem alterados. Os casos G3 (escritos por Anthropic e aprovados pelo humano) **não** foram reavaliados; conferi só a integridade deles.

## Veredito

**Aprovado com ressalvas.** Não achei defeito que contrarie AUT-*, `interface.md` ou ADR-0017. As ressalvas abaixo são endurecimento de segurança e lacunas de teste, para o `seguranca-llm` e para o humano decidirem. Esta revisão não substitui a revisão de segurança nem o G4.

## Prova executada

| Item | Status | Detalhe |
| --- | --- | --- |
| `uv run pytest apps/api packages/lineage -q` (uma execução, Postgres `training-system-infra-app-postgres-1`) | Passou | `153 passed, 1 warning in 34.90s`, igual a `pytest-r2.txt`; o aviso Pydantic vem de `test_chat.py` e não tem relação com a T-024 |
| sha256 dos 3 arquivos G3 contra `casos_aprovados` | Passou | `c770ed17…`, `eea4696c…`, `cb7be243…` conferem |
| Mock, stub ou dado fixo em produto | Passou | nenhum em `apps/api/src` nem na 0006 |
| Segredo versionado | Passou | `.env.example` só traz limites `API_*`; as evidências mascaram a senha do DSN (`***`) |
| Dependências | Passou | `fastapi 0.163.2`, `uvicorn 0.54.0`, `fastapi-users 15.0.5`, `fastapi-users-db-sqlalchemy 7.0.0`, `pwdlib 0.3.0`, `argon2-cffi 25.1.0`, `pyjwt 2.15.1`, `python-multipart 0.0.34`; só as aprovadas no G1 e o que elas puxam, entradas no esqueleto `21ce2c5` |
| `uv run validar-governanca` | Não executado | fora do pedido; a execução do executor está em `validador-r2.txt` |

## Requisitos × código × evidência

| Requisito | Código | Status | Observação |
| --- | --- | --- | --- |
| AUT-CAD-001 | `app.py:59-75`, `auth.py:20-33`, `database.py:43-55`, 0006 `CHECK $argon2id$` e índice `lower(email)` | Passou | `safe=True` descarta campos de privilégio; `IntegrityError 23505` cobre cadastro concorrente; o hash é do `pwdlib` com Argon2id (o padrão do `PasswordHelper`), e a CHECK no banco garante isso |
| AUT-SES-001 | `app.py:77-99`, `auth.py:51-57` | Passou | `DatabaseStrategy.write_token` usa `token_urlsafe()` (43 caracteres, opaco); o cookie prévio não é lido no login; a falha sai idêntica para os dois casos, e `authenticate` da biblioteca gera hash mesmo para e-mail inexistente, o que mitiga timing |
| AUT-SES-002 | `app.py:105-132`, `auth.py:60-72` | Passou | logout apaga o token; troca de senha apaga todos os tokens da conta na mesma transação do novo hash (`scope='function'` faz o commit antes da resposta); a validade é checada em `read_token` |
| AUT-CSR-001 | `csrf.py:12-23`, `database.py:26-31` | Passou (recorte T-024) | ordem 401 → 403; `compare_digest` com limite de tamanho; CSRF ligado ao token da sessão. A parte de `POST /chat` fica para a T-025 (Não se aplica aqui) |
| AUT-LIM-001 | `limits.py:20-66`, `app.py:83-98` | Passou | advisory lock por chave, contagem em janela no Postgres, 429 antes de verificar a senha, chaves sha256 (nunca o e-mail cru); a tentativa de cadastro é gravada em commit separado |
| AUT-OWN-001 | `auth.py:75-76`; `PasswordChange extra='forbid'` em `app.py:26-29` | Passou (recorte T-024) | `owner_id = str(user.id)`; a parte de chat e linhagem fica para a T-025 |

Fidelidade à `interface.md` e à ADR-0017: rotas, códigos, corpos, cookie `training_session`, cabeçalho `X-CSRF-Token`, limites padrão, as três tabelas e o comportamento do downgrade conferem. Os dois engines têm pools separados e nenhuma transação atravessa os dois (ADR-0017 item 12). Roteadores de e-mail e `/users/{id}` não montados (item 11).

Migração 0006 (`0006_auth.py`): é aditiva, `down_revision='0005'` e não toca 0001–0005. No downgrade, o `LOCK ... ACCESS EXCLUSIVE` vem antes da verificação de contas, o que fecha a corrida com cadastro concorrente. `tables.py` espelha a 0006 coluna por coluna.

Testes de linhagem alterados (`test_lineage.py`, `test_ressalvas_persistencia.py`): **nenhuma asserção afrouxada**. O catálogo continua em igualdade exata de conjunto, agora com as tabelas de 0004/0005/0006. A versão passou do literal `'0003'` para `get_current_head()`, e as asserções de dados, alvos inválidos e roundtrip ficaram intactas.

## Achados (ressalvas, nenhum bloqueante)

1. **Troca de senha é oráculo de senha sem limite** — `apps/api/src/training_api/app.py:123-125`. Quem tem um cookie e um CSRF roubados (XSS, máquina compartilhada) pode testar `current_password` à vontade: a falha `INVALID_CURRENT_PASSWORD` não conta em `auth_attempt`. A AUT-LIM-001 não exige isso, mas o ganho é de baixo custo: gravar `login_failure/account` e checar o limite antes de verificar. Decisão: `seguranca-llm` ou humano.
2. **Downgrade 0006 com contas sem teste** — `packages/lineage/src/training_lineage/migrations/versions/0006_auth.py:39-49`. A recusa em apagar contas existentes (proposta item 3, ADR-0017 Consequências) não é exercitada por nenhum teste. `test_lineage.py:204` só cobre o downgrade com a tabela vazia. Sugestão: um caso que insere `app_user`, chama `migrate(engine, '0005')`, espera erro e confere que as três tabelas e a linha continuam lá.
3. **Login CSRF** — `app.py:77`. O login não exige CSRF (decisão da interface aprovada no G3) e aceita formulário, que é uma requisição "simples" para o navegador. Com `SameSite=Lax`, um site de terceiros consegue fazer o navegador da vítima entrar na conta do atacante por POST top-level. O impacto fica limitado, porque o `csrf_token` não chega ao front da vítima, mas o registro é para o `seguranca-llm`, junto com a T-025 e o `apps/web` (por exemplo, checar `Origin` ou `Sec-Fetch-Site` no login).
4. **Sem limite superior de senha nem de corpo** — `auth.py:29-33`. A política só tem mínimo; senhas de megabytes passam pelo Argon2 e por `python-multipart` no login. O limite de taxa contém isso por origem, mas não por tamanho. Sugestão: um máximo (por exemplo, 1024) e um limite de corpo no proxy.
5. **Crescimento sem limpeza** — `limits.py:36-39` e `auth_access_token`. Linhas de `auth_attempt` e tokens vencidos nunca são apagados. Não é defeito da v1, mas cabe registrar uma tarefa de limpeza (worker).
6. **Origem única atrás de proxy** — `limits.py:15-17`, documentado em `apps/api/README.md`. Sem `--proxy-headers --forwarded-allow-ips`, todos os clientes compartilham uma origem: o limite de 20 falhas vira global e os advisory locks serializam todos os logins. Isso é do deploy, mas vale levar ao checklist de deploy.
7. **Nit — cabeça dinâmica** — `packages/lineage/tests/test_lineage.py:21-24` e `:215`, `test_ressalvas_persistencia.py:29-32` e `:131`. `get_current_head()` deixa de fixar a revisão esperada. O catálogo exato compensa, mas um `assert migration_head == '0006'` explícito manteria a regressão visível.
8. **Nit (pré-existente, registro)** — `tarefas/T-024.md:56` diz que os testes "fixavam a cabeça 0005", mas eles fixavam `'0003'` e o catálogo sem as tabelas de chat. Ou seja, já estavam desatualizados desde as migrações 0004/0005, e não só por causa da T-024. Sugiro corrigir o texto e anotar no `regressao.md` que essa regressão passou despercebida.

## Pendências

- Revisão de segurança `seguranca-llm` (obrigatória) e revisão independente Google, conforme a tarefa.
- G4 humano.
- Nenhum arquivo além deste foi editado; sem commit.

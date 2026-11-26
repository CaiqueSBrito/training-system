Revisor: subagente `security-reviewer` (papel `seguranca-llm`), somente leitura, commit 336c444, 2026-11-26.

# Reavaliação de segurança da T-024 no commit 336c444 (correções da rodada R3)

## Veredito: aprovado com ressalvas

A1 a A6, A8 a A10, I1 e I2 foram corrigidos. A7 e A11 continuam pendentes por decisão humana e não entram no veredito. A correção do A8 trouxe um problema novo (N1, médio): o Argon2 passou a rodar em threads sem limite de quantas ao mesmo tempo. A correção é pequena e convém fazê-la antes de expor a API. Os outros pontos novos são baixos ou informativos.

Esta revisão foi só leitura. Como não tenho shell, não rodei `git diff`: li direto o estado atual dos arquivos listados no `relatorio-r3.txt` e comparei com o que revisei no ccd43dd. Não rodei testes e não li o `.env`. Onde escrevi [INFERÊNCIA], a conclusão vem do código e não de execução.

## Situação dos achados anteriores

- **A1, resolvido.**
  - **Reserva:** `reserve_login` (`limits.py:84-100`) faz trava não bloqueante (`pg_try_advisory_xact_lock`, `limits.py:52`), que responde 429 quando há disputa. Na mesma transação curta conta, insere as duas reservas e confirma antes do Argon2.
  - **Conexões:**
    - `authenticate` (`auth.py:63-89`) confirma a transação antes de ir para a thread.
    - A trava da linha do usuário usa `NOWAIT` (`auth.py:77`) e fica só entre o `SELECT` e a gravação do token na mesma sessão (`app.py:86-93`).
    - Nenhum ponto pede outra conexão enquanto segura uma trava. A troca de senha segue o mesmo padrão (`app.py:121-158`).
  - **Falhas:** a reserva gravada antes de verificar a senha funciona como falha registrada. Só o sucesso a remove (`release_login`, `limits.py:103-105`).
  - **Reserva esquecida:** uma reserva nunca removida não bloqueia a conta para sempre. Ela sai da janela e é apagada pela purga. A purga (`<=`) e a contagem (`>`) usam o mesmo corte de tempo.
- **A2, resolvido:** IPv6 é agrupado por /64, IPv4 por /32, e IPv4 mapeado em IPv6 vira o IPv4 original (`limits.py:20-31`).
- **A3, resolvido na aplicação:** sem `request.client`, ou com endereço inválido, a resposta é 403, sem cair numa chave vazia (`limits.py:21-30`). A configuração do proxy continua sendo do deploy (README).
- **A4, resolvido:** a troca de senha usa o mesmo limite do login, reservado antes da verificação (`app.py:123`). Erra a senha atual: a reserva fica. Acerta, mas a nova senha é inválida: a reserva é removida (`app.py:138`).
- **A5, resolvido:** a chave da conta usa o `lower()` do próprio Postgres (`limits.py:88-89`), a mesma função da busca e do índice. Não há mais `strip()` nem `lower()` do Python.
- **A6, resolvido:**
  - `require_same_origin` (`browser.py:17-27`) recusa `Sec-Fetch-Site: cross-site`, `Origin: null` e qualquer esquema, host, porta, caminho ou credencial diferente da origem.
  - Um atacante não consegue escolher o `Host` do navegador da vítima, então não achei forma de contornar.
  - Requisição sem `Origin` é aceita; navegadores atuais sempre enviam `Origin` em POST de outra origem.
  - Vale para login e cadastro, e roda antes do limite.
- **A8, resolvido, mas veja N1:** o hash e a verificação rodam em `to_thread.run_sync` (`auth.py:42-46`).
- **A9, resolvido:** `last_seen_at` com expiração por inatividade e validade absoluta, as duas calculadas pelo relógio do banco (`database.py:65-81`). Atualiza no máximo uma vez por minuto (`database.py:78`).
- **A10, resolvido:**
  - O banco guarda só o SHA-256 do token e do CSRF (`auth.py:92-98`). A busca aplica o hash ao valor bruto (`database.py:67-68`).
  - A revogação continua funcionando: `destroy_token` passa por `get_by_token`, que aplica o hash, e a troca de senha apaga por `user_id`.
  - Um digest usado como cookie é recusado pelo tamanho 43 e pelo hash duplo. O CSRF é comparado em tempo constante sobre os digests (`csrf.py:20-23`).
  - As chaves de tentativa usam HMAC com `API_ATTEMPT_KEY_SECRET`, obrigatório e fora do `repr` (`config.py:12-16`).
  - A purga existe (`limits.py:38-48`).
- **I1, resolvido:** `/users/me` responde `no-store` (`app.py:97-99`).
- **I2, resolvido:** `/docs` e `/redoc` podem ser desligados (`app.py:53-55`); `/openapi.json` continua, como exige o API-CTR-001.

Não achei regressão em: Argon2id e `CHECK`, respostas iguais no login (inclusive acima de 1024 caracteres e conta inativa), token novo a cada login, revogação na troca de senha (o hash é revalidado sob `NOWAIT` em `auth.py:82-83` e `app.py:147-151`), atributos do cookie, `safe=True` no cadastro, `extra='forbid'`, CSRF exigido antes do efeito e downgrade da 0006 (sem mudança e agora com teste).

## Achados novos

### N1 — Médio [INFERÊNCIA]: o Argon2 em threads não tem limite de execuções simultâneas
- **Onde:** `apps/api/src/training_api/auth.py:42-46`, chamado em `auth.py:58`, `auth.py:69` e `auth.py:71`, e em `app.py:133` e `app.py:140`.
- **Como acontece:**
  - `to_thread.run_sync` usa o limitador padrão do anyio, de 40 threads.
  - Cada hash ou verificação com os parâmetros padrão do argon2-cffi (RFC 9106 de baixa memória) usa 64 MiB e 4 linhas internas.
  - Como a reserva é confirmada antes do hash, um IPv4 sozinho já põe 20 hashes em execução de uma vez (o limite por origem), usando e-mails aleatórios para não esbarrar no limite por conta.
  - Com 2 IPv4 ou alguns /64 de IPv6, são cerca de 40 hashes em paralelo: aproximadamente 2,5 GiB de pico e 160 threads nativas.
  - Antes do R3, os hashes rodavam um de cada vez dentro do servidor (custo de CPU, cerca de 64 MiB).
  - Sem autenticação, o atacante consegue um pico de memória que pode derrubar o processo por falta de memória, conforme o limite do container.
- **Correção:** um `anyio.CapacityLimiter` próprio do Argon2, com poucas vagas (por exemplo 2 a 4, ou o número de núcleos), passado em `limiter=` nas duas chamadas. Se não houver vaga, esperar é seguro, porque nenhuma conexão nem trava fica presa durante a espera; recusar com 429 também serve. Um teste de 40 logins simultâneos medindo quantos hashes rodam ao mesmo tempo confirma a correção.

### N2 — Baixo, ainda sem efeito hoje (relevante para a T-025): a purga roda dentro da transação da requisição autenticada
- **Onde:** `apps/api/src/training_api/database.py:80` chama `purge_expired` (`limits.py:38-48`) a cada `current_user` e `require_csrf`.
- **Como acontece:**
  - O `DELETE` trava as linhas expiradas até o commit da sessão da requisição.
  - Hoje as rotas são curtas, e a troca de senha confirma logo depois de `current_user` (`app.py:121-122`).
  - Se uma rota da T-025 (chat ou linhagem) mantiver a transação de autenticação aberta durante trabalho longo, as purgas concorrentes ficam bloqueadas segurando conexões.
  - Isso inclui a purga feita dentro de `reserve_login`, sob trava de conta e de origem. O resultado é de novo o pool esgotado do A1.
- **Também:**
  - Cada requisição autenticada faz dois `DELETE`, e `last_seen_at` não tem índice, então há uma varredura completa da tabela de sessões por requisição.
  - [INFERÊNCIA] Purgas concorrentes podem raramente entrar em impasse no Postgres (40P01), o que vira um 500.
- **Correção:**
  - Purgar fora da transação da requisição, numa sessão própria e curta, com `DELETE … WHERE ctid IN (SELECT … FOR UPDATE SKIP LOCKED LIMIT n)`, ou só de vez em quando.
  - Indexar `last_seen_at`.
  - Na T-025, confirmar a sessão de autenticação antes do trabalho longo.

### N3 — Informativo: reservas mantidas mesmo com a senha correta
- **Onde:** `auth.py:79-81` e `app.py:144-151`.
- **O que acontece:** quando há disputa `NOWAIT` (429), sessão revogada no meio da troca (401) ou erro depois de verificar a senha, a reserva fica e conta como falha mesmo com a senha certa.
- **Risco:** só o próprio usuário consegue provocar isso (por exemplo, dois logins certos ao mesmo tempo), porque a trava da linha só é tomada depois de verificar a senha. Um atacante não consegue usar isso para bloquear a conta. Aceitável; se quiser, remover a reserva nesses caminhos.

### N4 — Informativo: a 0006 foi alterada no lugar
- **Onde:** `0006_auth.py:22-28` (`token VARCHAR(64)` e `last_seen_at`).
- **Risco:** a revisão ainda não foi entregue nem aceita (sem G4), então editar no lugar é aceitável. Mas um banco que já tenha recebido a 0006 do ccd43dd fica com `VARCHAR(43)` e sem `last_seen_at`, e o login quebra. Esse banco precisa ser recriado, porque o downgrade recusa se houver contas. A docstring ainda diz "snapshot imutável".
- **Cosmético:** `tables.py:31` usa `append_column`, então a ordem das colunas difere da migração.

### N5 — Informativo (deploy): a verificação de Origin depende de o proxy repassar a URL pública
- **Onde:** `browser.py:23`.
- **Risco:** `request.base_url` vem de `Host` e de `X-Forwarded-Proto` (este só quando o proxy é confiável). Atrás de um proxy com TLS que não preserve `Host`, ou sem `--forwarded-allow-ips`, todo login de navegador recebe 403. A falha é fechada, não é uma forma de contornar. O artefato de deploy precisa preservar `Host` e confiar no IP exato do proxy, como já diz o A3 no README.

## Pendências fora do veredito
A7 (senhas comuns) e A11 (prefixo `__Host-` e trava de `cookie_secure`) aguardam decisão humana. I3, I4 e I5 continuam como riscos aceitos e não mudaram.

## Escopo lido
- `relatorio-r3.txt`
- `apps/api/src/training_api/{app,auth,browser,config,csrf,database,limits}.py`
- `0006_auth.py` e `tables.py:9-45`
- Os nomes dos testes em `test_api_auth_hardening.py`
- As entradas `API_*` no README e no `.env.example`
- Os padrões do Argon2 no pwdlib e no argon2-cffi
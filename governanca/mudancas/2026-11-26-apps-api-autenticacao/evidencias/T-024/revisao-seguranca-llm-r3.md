Revisor: subagente `security-reviewer` (papel `seguranca-llm`), somente leitura, commit 77cc959, 2026-11-26.

# Reavaliação de segurança da T-024 no commit 77cc959 (rodada R4)

## Veredito: aprovado com ressalvas

N1, N2, N3 e N4 estão corrigidos. A rodada trouxe uma regressão nova, baixa (R4-1), e há duas observações baixas ou informativas (R4-2 e R4-3). Nenhuma impede a aprovação.

Esta revisão foi só leitura: `relatorio-r4.txt`, os seis módulos da API, a `0006_auth.py` e a `tables.py`. Não rodei testes nem `git diff` e não li o `.env`. Onde escrevi [INFERÊNCIA], a conclusão vem do código e não de execução.

## Achados da rodada anterior

- **N1, resolvido.**
  - O limite de hashes simultâneos é um só para toda a aplicação, criado em `app.py:59` e entregue a cada requisição por `auth.py:112-113`.
  - As duas únicas chamadas de hash e verificação passam `limiter=` (`auth.py:45` e `auth.py:48`). Não sobrou nenhum uso direto de `password_helper` na API.
  - O padrão é `max(2, cpu_count)` (`config.py:23`).
  - Quem espera vaga não segura conexão nem trava:
    - Login: a reserva já foi confirmada, e `authenticate` confirma a transação antes do hash (`auth.py:71`).
    - Cadastro: a tentativa é confirmada na sessão própria, e o hash vem depois de um commit (`auth.py:53`).
    - Troca de senha: há commit em `app.py:140` antes de verificar e de gerar o hash.
- **N2, resolvido.**
  - `maybe_purge_expired` (`limits.py:61-70`) abre uma sessão própria. É chamada quando a requisição ainda não tem conexão: em `current_user` antes de ler o token (`auth.py:139`), antes da reserva do login (`limits.py:108`) e antes do registro de cadastro (`limits.py:98`).
  - A trava local (`threading.Lock`) não atravessa nenhum `await`, e a purga roda no máximo a cada 60 s (`limits.py:68`).
  - Os `DELETE` usam `ctid IN (… LIMIT :batch_size FOR UPDATE SKIP LOCKED)` (`limits.py:46` e `limits.py:55`).
  - `current_user` confirma a transação antes de retornar (`auth.py:142`), e `require_csrf` também (`csrf.py:21`).
  - O índice `auth_access_token_last_seen_at` existe na `0006_auth.py:30` e na `tables.py:33`.
- **N3, resolvido.** O login (`app.py:104-109`) e a troca de senha (`app.py:170-174`) desfazem a transação e só depois liberam a reserva, dentro de `CancelScope(shield=True)`. A liberação só acontece depois de a senha ser verificada. Nunca há duas conexões presas ao mesmo tempo. Senha errada continua contando como falha.
- **N4, resolvido.** A docstring foi ajustada (`0006_auth.py:1`). `last_seen_at` está na mesma posição na migração (`:26`) e na `tables.py` (`:29`), e o `append_column` saiu. O downgrade não mudou.

## Achados novos

### R4-1 — Baixo (regressão do N3): o limite de tentativas revela a senha correta de conta inativa
- **Onde:** `auth.py:78` marca `password_verified = True` antes de qualquer verificação de `is_active`. `app.py:95` responde 400 para a conta inativa, mas `app.py:105-109` libera a reserva.
- **Problema:**
  - Em conta inativa, a senha certa não consome o limite e a errada consome. A resposta é o mesmo 400, mas um atacante descobre se acertou pelo momento em que chega o 429. É preciso cerca de N tentativas para cada senha testada.
  - Também pode haver diferença de tempo [INFERÊNCIA]: no caminho da senha certa há um `SELECT … NOWAIT` e um `DELETE` a mais.
  - Isso quebra a igualdade de resposta de AUT-SES-001 para contas inativas e expõe uma senha válida, que pode ter sido reutilizada em outro lugar.
  - O alcance é pequeno: nenhuma rota desativa contas hoje, só uma alteração direta no banco.
- **Correção:** marcar a senha como verificada só depois de `locked.is_active`, em `auth.py:84-85`. Com a conta inativa, devolver `None` mantendo a reserva.

### R4-2 — Baixo: a purga tem vazão fixa, e um ataque contínuo faz as tabelas crescerem
- **Onde:** `limits.py:39-58` e `limits.py:68`.
- **Problema:**
  - A purga apaga no máximo 100 tentativas e 100 sessões por minuto em cada processo.
  - Muitas origens IPv6 geram mais de 100 tentativas por minuto: cada falha de login grava 2 linhas.
  - Uma conta válida fazendo login repetido gera mais de 100 sessões por minuto. Login com sucesso não tem limite, e só o limite de hashes simultâneos freia.
  - Sob ataque contínuo, `auth_attempt` e `auth_access_token` crescem sem limite. Antes do R4, a purga não tinha limite e acompanhava.
  - [INFERÊNCIA] `DELETE … WHERE ctid IN (subconsulta)` tende a virar uma junção que varre a tabela inteira de fora uma vez por minuto.
- **Correção:**
  - Repetir os lotes dentro da mesma purga até um lote vir incompleto ou estourar um orçamento de tempo (por exemplo 200 ms).
  - Usar `ctid = ANY(ARRAY(SELECT …))` para o planejador acessar as linhas diretamente pelo `ctid`.
  - Opcionalmente, limitar quantas sessões ativas cada conta pode ter.

### R4-3 — Informativo: a fila do limite de hashes não tem teto
- **Onde:** `auth.py:44-48`.
- **Problema:** a fila do `CapacityLimiter` cresce sem limite e não tem tempo máximo de espera. Com muitas origens, um atacante pode atrasar logins legítimos sem limite. Não há conexão nem trava presa durante a espera, então o problema é só de latência.
- **Correção opcional:** recusar com 429 ou 503 quando a fila passar de um teto, por exemplo quando `limiter.statistics().tasks_waiting` ficar acima de 4 vezes o número de vagas.

## Sem regressão
- A sessão confirmada antes da rota não perde dados, porque `expire_on_commit=False`.
- O logout e a troca de senha reabrem a transação na mesma sessão e confirmam no fim.
- A purga com `SKIP LOCKED` não espera linhas presas: uma sessão sendo atualizada é só pulada.
- Revogação, CSRF, verificação de Origin, chaves HMAC, hash dos tokens no banco e downgrade da 0006 estão como no R3.
- A7 e A11 continuam pendentes por decisão humana e não entram no veredito.
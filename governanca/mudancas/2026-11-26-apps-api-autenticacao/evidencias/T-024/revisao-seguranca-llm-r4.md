Revisor: subagente `security-reviewer` (papel `seguranca-llm`), somente leitura, commit ba3c757, 2026-11-26.

# Reavaliação de segurança da T-024 no commit ba3c757 (rodada R5)

## Veredito: aprovado com ressalvas

R4-1, R4-2 e R4-3 estão corrigidos. A correção do R4-3 trouxe uma regressão nova, baixa (R5-1). Não achei outras.

Esta revisão foi só leitura: `relatorio-r5.txt` e o estado atual de `auth.py`, `limits.py`, `app.py` e `config.py`. Não rodei testes e não li o `.env`.

## Os três achados conferidos

- **R4-1, resolvido.**
  - `auth.py:105` devolve `None` para senha errada e para conta inativa no mesmo ponto, depois da mesma verificação. Os dois sinais de liberação continuam falsos, então a reserva fica (`app.py:105-109`).
  - Senha certa e senha errada em conta inativa seguem o mesmo caminho de código: mesmo custo, mesmo 400 e mesmo consumo do limite.
  - A conta que fica inativa entre a leitura e a trava também mantém a reserva (`auth.py:119-120`).
  - `password_verified` só é marcado depois da trava confirmar conta ativa e hash inalterado (`auth.py:121`).
  - `verified_nowait` (`auth.py:115`) só ocorre com conta ativa já verificada, então a correção do N3 continua valendo sem servir de oráculo.
- **R4-2, resolvido.**
  - Os dois `DELETE` usam `ctid = ANY(ARRAY(… LIMIT :batch_size FOR UPDATE SKIP LOCKED))` (`limits.py:44` e `limits.py:53`).
  - O laço `limits.py:63-71` repete só a tabela cujo lote veio completo, até o orçamento de cerca de 200 ms.
  - Linhas já apagadas pela própria transação não aparecem nos lotes seguintes, então o laço sempre avança. O laço termina pelo prazo.
  - Limitação aceitável, documentada pelo executor: o orçamento é verificado entre comandos e não interrompe um comando já em andamento.
- **R4-3, resolvido.**
  - `PasswordBudget.admit` (`auth.py:35-44`) conta trabalhos em execução e em espera sob uma trava local que nunca atravessa `await`. Acima de vagas × (1 + multiplicador), responde 503 antes de chegar ao hash ou à verificação (`auth.py:38`).
  - O decremento fica em `finally`, então vale também em erro ou cancelamento.
  - Nenhum chamador segura conexão ou trava ao ser admitido: o login confirma a reserva e `authenticate` confirma antes de verificar; o cadastro confirma antes do hash; a troca de senha faz commit antes de `app.py:144`.
  - O 503 aparece igual para e-mail existente (verificação) e inexistente (hash falso), então não cria enumeração.
  - `config.py` valida `password_hash_queue_multiplier` como inteiro positivo.

## Achado novo

### R5-1 — Baixo (regressão do R4-3): o 503 de sobrecarga conta como falha de login
- **Onde:** um 503 lançado em `auth.py:38`, vindo de `authenticate` (`auth.py:102` ou da verificação logo antes de `auth.py:105`), chega a `app.py:105-109` com os dois sinais falsos. A reserva feita em `app.py:93` fica, como se fosse uma falha de credencial. O mesmo ocorre na troca de senha: um 503 na verificação em `app.py:144`, que está fora do `try`, também mantém a reserva.
- **Impacto:** um atacante que lota a fila de hashes (com muitas origens) faz cada tentativa legítima receber 503 e ainda gastar o limite da conta e da origem. Depois de 5 tentativas, o usuário legítimo leva 429 por 15 min, sem ter errado a senha. No R4, a espera não consumia o limite.
- **Correção:** quando a recusa for por sobrecarga, sem nenhuma verificação executada, remover a reserva e devolver o 503. Por exemplo, uma exceção própria de `admit` tratada no `finally` do login e em volta de `app.py:144`. Isso é seguro: nenhuma senha foi verificada, então o atacante não aprende nada e não ganha tentativas.

## Sem regressão
- Igualdade das respostas de login (inclusive conta inativa), trava `NOWAIT` com revalidação do hash e revogação na troca de senha.
- Liberação protegida com `CancelScope(shield=True)` depois do rollback, nunca com duas conexões ao mesmo tempo.
- A purga continua em sessão própria, no máximo a cada 60 s, com `SKIP LOCKED`.
- Cosmético: `app.state.password_limiter` (`app.py:62`) ficou redundante, sem efeito na segurança.
- A7 e A11 continuam pendentes por decisão humana e não entram no veredito.
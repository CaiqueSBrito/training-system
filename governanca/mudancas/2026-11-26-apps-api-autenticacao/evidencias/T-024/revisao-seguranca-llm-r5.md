Revisor: subagente `security-reviewer` (papel `seguranca-llm`), somente leitura, commit 806aff8, 2026-11-26.

# Reavaliação de segurança da T-024 no commit 806aff8 (rodada R6)

## Veredito: aprovado

O R5-1 está corrigido e não achei regressão. Não há ressalva nova. A7 e A11 continuam pendentes por decisão humana e ficaram fora do veredito, como você pediu.

Esta revisão foi só leitura: `relatorio-r6.txt`, `auth.py` e `app.py`. Não rodei testes e não li o `.env`.

## R5-1: o 503 de sobrecarga agora remove a reserva

**De onde vem o 503**
- `PasswordBudgetExceeded` é lançado só por `admit` (`auth.py:45`). Isso acontece antes do `yield`, portanto antes de qualquer `to_thread.run_sync` (`auth.py:79` e `auth.py:83`).
- Não há outro ponto que lance essa exceção (conferido com `grep`).

**Login**
- `app.py:105-106` marca `overloaded`, e `app.py:109` passa a liberar a reserva nesse caso.
- Uma verificação já feita não escapa do limite. Em `authenticate`, o 503 só pode vir de uma de duas chamadas, ambas a primeira e única operação de senha:
  - o hash falso para e-mail inexistente (`auth.py:~102`);
  - a verificação (`auth.py:~104`).
- Depois da verificação nenhuma outra chamada passa pelo limite de hashes: `upgraded_hash` é reaproveitado sem novo hash, e `write_token` não faz hash.
- Por isso o 503 só sai quando nenhuma senha foi testada. Liberar a reserva não devolve nenhum resultado de credencial e não dá tentativas grátis.
- E-mail existente e inexistente fazem o mesmo trabalho até a admissão e liberam do mesmo jeito, então não surge enumeração.

**Troca de senha**
- `app.py:147-154` captura o 503 só em volta da verificação da senha atual (`app.py:148`), faz rollback, libera a reserva com `CancelScope(shield=True)` e relança o 503.
- O 503 ao gerar a senha nova já estava dentro do `try`/`finally` (`app.py:181`), que só roda depois de a senha atual ser validada.

**Conexões e travas**
- No login, `authenticate` confirma a transação antes de verificar (`auth.py:~100`). Na troca de senha, há commit antes de `app.py:148`.
- O rollback não tem trabalho pendente e devolve a conexão antes de `release_login` abrir a sessão curta. A trava local de `admit` não atravessa `await`.
- Nunca há duas conexões nem trava de linha ou advisory presas ao mesmo tempo.

**Os achados anteriores continuam valendo**
- Senha errada e conta inativa ainda mantêm a reserva, porque os sinais continuam falsos (R4-1).
- `NOWAIT` com conta ativa já verificada continua liberando a reserva (N3).
- O cadastro continua contando todas as tentativas por origem, como antes.

## Regressão
- Nenhuma.
- O cosmético: `app.state.password_limiter` saiu. O único limitador fica em `PasswordBudget` e chega por `UserManager.password_limiter` (`auth.py:67`). Não sobrou nenhuma referência ao atributo removido em `apps/api/src`.
# ADR-0017 — Autenticação própria com fastapi-users e sessão no Postgres da aplicação

Estado: **Aceita** (humano, G1 2026-11-26). Resolve o item 11 da arquitetura §11.

## Contexto

A pipeline E1–E12 só roda pela CLI local (`training-chat`), com o `owner_id` informado à mão. A `apps/api` (ADR-0010, ADR-0011) vai expor o chat e a consulta de linhagem a um cliente HTTP (o futuro `apps/web`, ADR-0013), e a ADR-0011 deixou a autenticação para a mudança da `apps/api`. Sem ela, nada garante quem é o dono dos documentos, turnos e itens.

Pedido do humano (mudança `2026-11-26-apps-api-autenticacao`):

- autenticação **própria**: contas e senhas no nosso Postgres, sem provedor de identidade externo;
- uma **biblioteca externa** para não escrever do zero hash de senha, sessão e fluxos de login.

Restrições que pesam:

- É a primeira superfície exposta do sistema; erros aqui expõem os dados de todos os usuários.
- O cliente é um navegador (ADR-0013) que lê o SSE do chat com `fetch` (ADR-0011). Cookie de sessão é o caminho natural; token guardado em JavaScript fica exposto a XSS.
- Um deploy e um banco da aplicação (ADR-0008, ADR-0010): as contas e as sessões ficam no mesmo Postgres da linhagem e da fila, sem serviço novo.
- A linhagem (`packages/lineage`) usa SQLAlchemy **síncrono** com psycopg 3 e migrações Alembic; o `owner_id` das tabelas de linhagem é `TEXT`.
- Toda dependência nova passa pelo humano e pela carência de 7 dias (ADR-0007).

## Decisão

1. **Biblioteca**: `fastapi-users` **15.0.5** com `fastapi-users-db-sqlalchemy` **7.0.0**. O hash de senha é feito pelo `pwdlib` (dependência do `fastapi-users`) com **Argon2id**; a tabela guarda só o hash `$argon2id$`, nunca a senha (AUT-CAD-001).
2. **Contas no Postgres da aplicação**: tabela de usuário do `fastapi-users-db-sqlalchemy` (id UUID, e-mail único, hash, flags), criada por migração Alembic aditiva em `packages/lineage`, com downgrade que recusa apagar contas existentes. O `owner_id` usado pelo chat e pela linhagem é a forma textual do id do usuário.
3. **Sessão no servidor**: `DatabaseStrategy` (tokens de acesso numa tabela do mesmo banco, com validade configurável) + `CookieTransport`. O cookie é opaco e sai com `HttpOnly`, `Secure` e `SameSite=Lax` (AUT-SES-001). Cookie de sessão revogada, inexistente ou vencida é tratado como não autenticado (401).
4. **Novo token a cada login**: cada login bem-sucedido grava um token novo e aleatório; um cookie que existia antes do login nunca é promovido a sessão autenticada (sem fixação de sessão, AUT-SES-001).
5. **Revogação** (AUT-SES-002): o logout apaga o token da sessão; a troca de senha apaga **todos** os tokens da conta, inclusive o da sessão que fez a troca, que precisa de novo login.
6. **Login sem enumeração**: e-mail inexistente e senha errada recebem a mesma resposta (status e corpo) (AUT-SES-001).
7. **CSRF por token de sessão** (AUT-CSR-001): cada sessão tem um token CSRF aleatório, devolvido ao cliente no login e ligado ao token da sessão no banco. Toda requisição autenticada por cookie com método que altera estado (POST, PUT, PATCH, DELETE) precisa trazer esse token num cabeçalho; ausente, inválido ou de outra sessão → 403, sem efeito colateral. O `SameSite=Lax` é defesa adicional, não substituto. O `fastapi-users` não traz CSRF; a verificação é da `apps/api`.
8. **Limite de tentativas** (AUT-LIM-001): login limitado **por conta e por origem**, cadastro limitado **por origem**, em janelas com limites configuráveis. Acima do limite, a resposta é 429 mesmo com a senha correta e sem revelar se a conta existe. O `fastapi-users` não traz limite de taxa; a contagem é da `apps/api`, persistida no Postgres da aplicação para valer entre processos, sem dependência nova.
9. **Cadastro aberto** na v1 a qualquer pessoa, com o limite do item 8 e política de tamanho mínimo de senha (AUT-CAD-001). E-mail já cadastrado é recusado sem devolver id nem dados da conta existente; o fato de o e-mail estar em uso é inevitável num cadastro aberto e fica contido pelo limite por origem.
10. **`owner_id` só da sessão** (AUT-OWN-001): nenhuma rota aceita `owner_id` no corpo ou na query; o dono de chat e consultas é sempre o usuário da sessão autenticada, e rota de dados sem sessão válida responde 401.
11. **Rotas usadas**: cadastro, login, logout, usuário atual e troca de senha. Os roteadores de verificação de conta e de recuperação de senha do `fastapi-users` **não** são montados: **e-mail fica fora da v1** (exige serviço de envio novo).
12. **SQLAlchemy assíncrono só na autenticação**: o `fastapi-users-db-sqlalchemy` exige `AsyncSession`. A `apps/api` cria um engine assíncrono (`postgresql+psycopg`, o mesmo driver da linhagem) só para usuários, sessões, CSRF e contadores; a linhagem continua com o engine síncrono. Mesmo banco, **pools separados**, cada um com suas transações; nenhuma transação atravessa os dois engines.

### Risco: `fastapi-users` em modo manutenção

O README do `fastapi-users` declara *maintenance mode*: só correções de segurança, sem recursos novos. A 15.0.5 (2026-03-27) cobre tudo que a v1 usa, mas a evolução fica presa (OIDC, MFA, novos transportes não virão da biblioteca).

**Critério de saída**: trocar o `fastapi-users` por `pwdlib` + sessão própria (tabela de sessão, cookie e rotas escritas na `apps/api`, mantendo as tabelas e o comportamento desta ADR) se ocorrer qualquer um:

- falha de segurança publicada no `fastapi-users` ou no `fastapi-users-db-sqlalchemy` sem versão corrigida disponível;
- incompatibilidade com uma versão de FastAPI ou SQLAlchemy de que o projeto precise, sem versão compatível da biblioteca publicada dentro da carência de 7 dias (ADR-0007).

A troca é uma ADR nova que substitui esta.

## Alternativas consideradas

| Opção | Por que não |
| --- | --- |
| `pwdlib` + sessão própria (hash pela biblioteca, sessão, cookie e rotas escritos no projeto) | mais código de segurança escrito à mão (cadastro, login, gerenciamento de usuário, sessão), contra o pedido do humano de não escrever esses fluxos do zero; fica como plano de saída |
| `authx` com JWT | JWT stateless não se revoga no logout nem na troca de senha sem lista de revogação no banco, o que devolve o estado ao servidor; token maior e com dados decodificáveis no cliente; nada a ganhar num deploy único com banco já disponível |
| OIDC com provedor externo (Keycloak, Auth0, Google) | contraria o pedido de autenticação própria; o provedor gerido guarda as identidades fora do nosso banco, e o self-hosted (Keycloak) é mais um serviço para operar; fica para uma mudança de login social, fora do escopo |

## Consequências

- `fastapi-users`, `fastapi-users-db-sqlalchemy` e o que eles puxarem (`pwdlib` com Argon2) são dependências novas, aprovadas no G1 e sujeitas à carência de 7 dias (ADR-0007); entram com a `apps/api`.
- `packages/lineage` ganha uma migração aditiva com as tabelas de usuário, token de acesso e o que a CSRF e o limite de tentativas precisarem; o downgrade recusa apagar contas existentes.
- A `apps/api` passa a ter dois engines SQLAlchemy sobre o mesmo banco; o tamanho somado dos pools precisa caber no `max_connections` do Postgres da aplicação.
- O cliente (`apps/web`) guarda o token CSRF e o envia em toda requisição que altera estado, inclusive o `POST /chat` (ADR-0011); o `fetch` precisa enviar o cookie (`credentials`).
- O cookie `Secure` exige HTTPS fora do ambiente local; o proxy reverso do deploy precisa terminar TLS.
- Sem e-mail, quem esquece a senha não recupera a conta na v1, e e-mails não são verificados; fica registrado como lacuna até haver serviço de envio.
- Verificação de segurança obrigatória da autenticação (papel `seguranca-llm`), com testes de fixação de sessão, CSRF, enumeração de usuário e força bruta.
- O modo manutenção do `fastapi-users` é acompanhado pelo critério de saída acima a cada atualização de dependências.

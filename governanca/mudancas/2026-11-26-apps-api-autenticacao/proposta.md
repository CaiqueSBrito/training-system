---
id: 2026-11-26-apps-api-autenticacao
titulo: apps/api com autenticação própria, chat por SSE e contratos pelo OpenAPI
trilha: padrao
estado: aprovada
aprovacao: { por: humano, em: 2026-11-26, palavras: "aprovado" }
---

## Por quê

A pipeline E1–E12 só roda pela CLI local (`training-chat`), com o `owner_id` informado à mão. Não há como um cliente (o futuro `apps/web`) usar o chat, e nada garante quem é o dono dos dados. As ADRs 0010, 0011 e 0014 já definem a forma da API (monólito modular, FastAPI + Uvicorn, progresso por SSE, contratos pelo OpenAPI) e deixam a autenticação para esta mudança (arquitetura §11, item 11).

Pedido do humano: autenticação **própria** (usuários e senhas no nosso Postgres, sem provedor de identidade externo), usando uma **biblioteca externa** para não escrever do zero hash de senha, sessão e fluxos de login.

## O que muda

1. **ADR-0017 — autenticação própria.** Contas no Postgres da aplicação; senha com Argon2id; sessão no servidor com cookie opaco `HttpOnly`, `Secure`, `SameSite=Lax`, revogável no logout e na troca de senha; proteção CSRF nas rotas que alteram estado; limite de tentativas de login. A biblioteca é decidida no G1 (ver Esclarecer).
2. **`apps/api`** (novo membro do workspace, adaptador fino, ARQ-APP-001):
   - autenticação: cadastro, login, logout, usuário atual, troca de senha;
   - `owner_id` passa a vir **só da sessão**; nenhuma rota aceita `owner_id` do corpo ou da query;
   - `POST /chat` com progresso por SSE (ARQ-API-001): eventos de etapa em ordem e um único evento final com a `ChatResponse`, inclusive em recusa e erro, chamando a `ChatPipeline` existente;
   - consulta de linhagem de item e de turno do próprio usuário (OBS-LIN-001), 404 para item de outro dono;
   - OpenAPI publicado com os modelos de `packages/contracts` (ARQ-CTR-001).
3. **Migração aditiva** em `packages/lineage` com as tabelas de usuário e sessão (e o que a biblioteca exigir), com downgrade que recusa apagar contas existentes.
4. **Remoção do JSON Schema exportado** (`packages/contracts/schema/`, comando `exportar-schema-contratos`, `test_schema.py`), substituído por teste que confere o OpenAPI contra `model_json_schema()` — previsto pela ADR-0014.
5. Testes antes do produto, com Postgres e Qdrant descartáveis; nenhum mock em código de aplicação.

## Fora do escopo

- Upload de documentos e ingestão pela API: depende do `apps/worker` (Procrastinate, ADR-0012). Vem na mudança do worker.
- E-mail (verificação de conta e recuperação de senha por e-mail): exige serviço de envio novo. Fica registrado como lacuna.
- Login social / OIDC, MFA, papéis de administrador.
- `apps/web`, deploy, proxy reverso e CI.
- Retomada de stream SSE interrompido (ADR-0011: fora da v1).

## Riscos

- **Segurança da autenticação.** É a primeira superfície exposta. Mitigação: biblioteca mantida para hash e sessão, revisão obrigatória do papel `seguranca-llm` + revisão de segurança da autenticação, testes de sessão fixa, CSRF, enumeração de usuário e força bruta.
- **Biblioteca em modo manutenção.** O `fastapi-users` (15.0.5, 2026-03-27) declara no README que está em *maintenance mode*: só correções de segurança, sem recursos novos. Encaixa bem hoje, mas prende a evolução.
- **SQLAlchemy assíncrono.** A linhagem usa SQLAlchemy síncrono com psycopg; bibliotecas de auth costumam exigir sessão assíncrona. Dois modos de acesso ao mesmo banco precisam de pool e transações sem conflito.
- **Dependências novas** (passam pela carência de 7 dias, ADR-0007): `fastapi`, `uvicorn`, a biblioteca de auth e o que ela puxar.
- **Duração do turno.** O chat leva segundos e várias chamadas de LLM; desconexão do cliente precisa cancelar sem deixar turno meio gravado (a `ChatPipeline` já persiste cancelamento como erro).

## Esclarecido (humano, G1, 2026-11-26)

- Biblioteca: **`fastapi-users`** (15.0.5, com `fastapi-users-db-sqlalchemy` 7.0.0), sessão no banco (`DatabaseStrategy` + `CookieTransport`), Argon2 via `pwdlib`. O modo manutenção fica registrado como risco na ADR-0017, com critério de saída.
- Cadastro **aberto** a qualquer pessoa na v1, com limite de taxa.

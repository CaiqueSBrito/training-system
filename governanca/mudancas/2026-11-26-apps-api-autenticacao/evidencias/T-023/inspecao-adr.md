# T-023 — inspeção da ADR-0017 contra os requisitos AUT

Data: 2026-11-26 · Papel: arquiteto (anthropic) · Revisor: google

ADR inspecionada: `docs/adr/0017-autenticacao-propria.md`.

## Requisitos AUT × ADR-0017

| Requisito | Verificação | Onde na ADR | Status |
| --- | --- | --- | --- |
| **AUT-CAD-001** | Senha guardada só como hash Argon2id (`$argon2id$`), via `pwdlib` | Decisão, item 1 | Passou |
| AUT-CAD-001 | Cadastro aberto, com política de tamanho mínimo de senha | Decisão, item 9 | Passou |
| AUT-CAD-001 | E-mail repetido recusado sem id nem dados da conta existente | Decisão, item 9 | Passou |
| **AUT-SES-001** | Sessão persistida no Postgres (`DatabaseStrategy`) e cookie opaco `HttpOnly`/`Secure`/`SameSite=Lax` (`CookieTransport`) | Decisão, item 3 | Passou |
| AUT-SES-001 | Resposta igual para e-mail inexistente e senha errada | Decisão, item 6 | Passou |
| AUT-SES-001 | Token novo a cada login, sem fixação de sessão | Decisão, item 4 | Passou |
| **AUT-SES-002** | Logout invalida a sessão | Decisão, item 5 | Passou |
| AUT-SES-002 | Troca de senha invalida todas as sessões da conta | Decisão, item 5 | Passou |
| AUT-SES-002 | Cookie revogado ou vencido → não autenticado (401) | Decisão, item 3 | Passou |
| **AUT-CSR-001** | Token CSRF ligado à sessão, exigido em POST/PUT/PATCH/DELETE autenticados por cookie; ausente ou de outra sessão → 403 sem efeito | Decisão, item 7 | Passou |
| **AUT-LIM-001** | Login limitado por conta e por origem; cadastro por origem; 429 acima do limite, mesmo com senha correta, sem revelar a conta | Decisão, item 8 | Passou |
| AUT-OWN-001 (fora dos requisitos da T-023, citado no brief) | `owner_id` só da sessão; nenhuma rota aceita `owner_id` do cliente; 401 sem sessão | Decisão, item 10 | Passou |

## Fidelidade ao G1 (proposta, "Esclarecido")

| Verificação | Onde na ADR | Status |
| --- | --- | --- |
| `fastapi-users` 15.0.5 + `fastapi-users-db-sqlalchemy` 7.0.0 | Decisão, item 1 | Passou |
| `DatabaseStrategy` + `CookieTransport`, Argon2 via `pwdlib` | Decisão, itens 1 e 3 | Passou |
| Modo manutenção registrado como risco | Decisão, "Risco: `fastapi-users` em modo manutenção" | Passou |
| Critério de saída: `pwdlib` + sessão própria se falha de segurança sem correção ou incompatibilidade com FastAPI/SQLAlchemy dentro da carência de 7 dias | mesma subseção | Passou |
| Cadastro aberto com limite de taxa | Decisão, itens 8 e 9 | Passou |
| SQLAlchemy assíncrono exigido pela biblioteca × linhagem síncrona: mesmo banco, pools separados | Decisão, item 12; Consequências | Passou |
| E-mail (verificação e recuperação) fora da v1 | Decisão, item 11; Consequências | Passou |
| Alternativas pwdlib + sessão própria, authx/JWT, OIDC externo, com motivo de não escolha | Alternativas consideradas | Passou |

## Formato, índice e arquitetura

| Verificação | Onde | Status |
| --- | --- | --- |
| Estado `**Aceita** (humano, G1 2026-11-26)` | ADR, cabeçalho | Passou |
| Seções Contexto, Decisão, Alternativas consideradas, Consequências | ADR | Passou |
| Número 0017 livre e sequencial após a 0016 | `docs/adr/` | Passou |
| Linha da 0017 no índice | `docs/README.md` | Passou |
| §11 item 11 marcado como resolvido, com link para a ADR-0017 | `docs/arquitetura/pipeline-rag.md` §11 | Passou |
| Nenhuma dependência adicionada ao workspace (a ADR só registra as aprovadas no G1) | `git diff` sem `pyproject.toml` nem `uv.lock` | Passou |
| Nenhum código editado | `git diff` só em `docs/` e `evidencias/T-023/` | Passou |
| Cenários AUT-* em execução | — | Não se aplica: a T-023 só documenta; a prova em execução é das tarefas da `apps/api` |
| Casos de teste (G3) | — | Não se aplica: tarefa só de documentação |
| Validador | `validador.txt` | Passou (exit 0) |

## Observações para o orquestrador

- Decisões que a ADR fixa além do texto literal do G1, para o revisor conferir: CSRF e limite de tentativas implementados na `apps/api` (o `fastapi-users` não os oferece), com contagem persistida no Postgres da aplicação, sem dependência nova; o `owner_id` é a forma textual do UUID do usuário (as colunas `owner_id` da linhagem são `TEXT`); o engine assíncrono usa o mesmo driver psycopg 3 da linhagem.
- Num cadastro aberto, a recusa de e-mail repetido revela que o e-mail está em uso; AUT-CAD-001 exige só não revelar dados da conta, e a ADR registra a mitigação pelo limite por origem. Se o humano quiser esconder também a existência, é mudança de requisito.
- O cabeçalho "Decisões:" de `docs/arquitetura/pipeline-rag.md` segue desatualizado (observação já feita na T-009); a T-023 só pode editar a §11.

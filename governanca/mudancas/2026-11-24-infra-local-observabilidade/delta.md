# Delta — Infraestrutura local e observabilidade base

Spec afetada: `governanca/specs/infra/spec.md` (criada por esta mudança).

## ADICIONA

### OBS-INF-001 — Infraestrutura local com um comando
QUANDO o desenvolvedor executar o comando de subida da infraestrutura local, o sistema DEVE subir o Qdrant, o Postgres da aplicação e o Langfuse com todos os healthchecks passando, configurados só pelo `.env`. (ADR-0002, ADR-0005, ADR-0008)

- Cenário: Dado um `.env` preenchido a partir do `.env.example`, Quando o comando de subida termina, Então todos os serviços estão `healthy` e a interface do Langfuse responde.
- Cenário: Dado o repositório, Quando procurado por senhas e chaves, Então nenhum segredo da infraestrutura está versionado.

### OBS-INF-002 — Linhagem separada do Langfuse
O sistema DEVE manter a linhagem num Postgres da aplicação separado do banco usado pelo Langfuse. (ADR-0008)

- Cenário: Dada a infraestrutura no ar, Quando as conexões são inspecionadas, Então a aplicação e o Langfuse usam bancos e credenciais diferentes.

### OBS-CFG-001 — Credencial de observabilidade ausente
SE faltar alguma variável de credencial do Langfuse ao configurar a observabilidade, ENTÃO o sistema DEVE falhar ao iniciar com uma mensagem que nomeia a variável ausente. (§8)

- Cenário: Dado `LANGFUSE_SECRET_KEY` vazio, Quando a observabilidade é configurada, Então a inicialização falha citando `LANGFUSE_SECRET_KEY`.

## MODIFICA

## REMOVE

# Delta — apps/api com autenticação própria, chat por SSE e contratos pelo OpenAPI

Spec afetada: `governanca/specs/api/spec.md` (criada por esta mudança).

Fontes: proposta e G1 desta mudança (2026-11-26); ADR-0010, ADR-0011, ADR-0014 e a ADR-0017 que esta mudança cria. Os requisitos ARQ-API-001 e ARQ-CTR-001 da mudança `2026-11-24-arquitetura-aplicacao` são cumpridos pelos requisitos API-* abaixo.

## ADICIONA

### AUT-CAD-001 — Cadastro aberto
QUANDO alguém enviar e-mail e senha válidos para cadastro, o sistema DEVE criar a conta com a senha guardada só como hash Argon2id e, SE o e-mail já existir, DEVE recusar sem revelar dados da conta existente. (G1; ADR-0017)

- Cenário: Dado um e-mail novo e uma senha dentro da política, Quando o cadastro é enviado, Então a conta é criada e a tabela guarda um hash `$argon2id$`, nunca a senha.
- Cenário: Dado um e-mail já cadastrado, Quando outro cadastro com ele é enviado, Então a resposta é recusa sem o id nem outros dados da conta existente.
- Cenário: Dada uma senha abaixo do tamanho mínimo da política, Quando o cadastro é enviado, Então é recusado sem criar conta.

### AUT-SES-001 — Sessão no servidor por cookie
QUANDO o login for bem-sucedido, o sistema DEVE criar uma sessão persistida no Postgres e enviar um cookie opaco `HttpOnly`, `Secure` e `SameSite=Lax`, e QUANDO o login falhar, DEVE responder igual para e-mail inexistente e senha errada. (ADR-0017)

- Cenário: Dadas credenciais corretas, Quando o login é feito, Então há uma linha de sessão no banco e o cookie tem os três atributos.
- Cenário: Dado um e-mail inexistente e, separadamente, um e-mail existente com senha errada, Quando os dois logins são tentados, Então status e corpo das duas respostas são iguais.
- Cenário: Dado um cookie de sessão existente antes do login, Quando o login é feito, Então o token emitido é novo (sem fixação de sessão).

### AUT-SES-002 — Revogação de sessão
QUANDO o usuário fizer logout ou trocar a senha, o sistema DEVE invalidar a sessão (no logout) ou todas as sessões da conta (na troca de senha), e um cookie revogado ou expirado DEVE ser tratado como não autenticado. (ADR-0017)

- Cenário: Dado um usuário logado, Quando faz logout, Então o mesmo cookie passa a receber 401.
- Cenário: Dado um usuário com duas sessões, Quando troca a senha por uma delas, Então as duas sessões antigas recebem 401.
- Cenário: Dada uma sessão com validade vencida, Quando é usada, Então recebe 401.

### AUT-CSR-001 — Proteção CSRF
O sistema DEVE recusar requisições autenticadas por cookie que alterem estado (POST, PUT, PATCH, DELETE) SE não trouxerem o token CSRF válido da sessão. (ADR-0017)

- Cenário: Dado um usuário logado, Quando envia `POST /chat` sem o cabeçalho CSRF, Então recebe 403 e nenhum turno é criado.
- Cenário: Dado o token CSRF de outra sessão, Quando é usado, Então recebe 403.

### AUT-LIM-001 — Limite de tentativas
O sistema DEVE limitar tentativas de login por conta e por origem e tentativas de cadastro por origem, recusando com 429 acima do limite configurado, sem revelar se a conta existe. (G1; ADR-0017)

- Cenário: Dado o limite de N tentativas de login falhas numa janela, Quando ocorre a tentativa N+1, Então a resposta é 429 mesmo com a senha correta.
- Cenário: Dado o limite de cadastros por origem, Quando ele é excedido, Então novos cadastros recebem 429.

### AUT-OWN-001 — Dono vem da sessão
O sistema DEVE usar como `owner_id` do chat e das consultas somente o id do usuário da sessão autenticada, e DEVE recusar com 401 qualquer rota de dados sem sessão válida.

- Cenário: Dado um corpo de requisição com um campo `owner_id` de outro usuário, Quando enviado ao chat, Então é recusado pela validação ou ignorado, e o turno gravado tem o `owner_id` da sessão.
- Cenário: Dada uma requisição sem cookie, Quando enviada a `POST /chat` ou à consulta de linhagem, Então recebe 401.

### API-CHT-001 — Chat por SSE
QUANDO um usuário autenticado enviar uma `ChatRequest` a `POST /chat`, a API DEVE executar a `ChatPipeline` e responder em `text/event-stream` com um evento de progresso por etapa executada, em ordem, e um único evento final com a `ChatResponse`, inclusive em recusa e erro. (ARQ-API-001; ADR-0011)

- Cenário: Dada uma mensagem que termina em E12, Quando o stream é lido, Então chegam eventos de progresso em ordem e um único evento final com status `ok`, itens e fontes.
- Cenário: Dada uma mensagem recusada pelo E1, Quando o stream é lido, Então ele termina com um evento final de status `recusa` e motivo, nunca cortado sem explicação.
- Cenário: Dada a resposta SSE, Quando os cabeçalhos são lidos, Então trazem `Cache-Control: no-cache` e `X-Accel-Buffering: no`.
- Cenário: Dado um cliente que desconecta no meio do turno, Quando a API percebe, Então o turno é cancelado e gravado como erro, sem item entregue.

### API-LIN-001 — Linhagem do próprio usuário
QUANDO um usuário autenticado consultar a linhagem de um item ou de um turno, a API DEVE devolver documento, versão, página, seção, evidência e `trace_id` se o item for dele, e DEVE responder 404 se não existir ou for de outro dono. (OBS-LIN-001)

- Cenário: Dado um item gerado pelo usuário A, Quando A consulta a linhagem, Então recebe as fontes resolvidas.
- Cenário: Dado o mesmo item, Quando o usuário B consulta, Então recebe 404, igual a um id inexistente.

### API-CTR-001 — Contratos no OpenAPI
A API DEVE publicar no OpenAPI os modelos de `packages/contracts` usados nos endpoints, com o mesmo schema de `model_json_schema()`, e o workspace NÃO DEVE manter JSON Schema exportado à parte. (ARQ-CTR-001; ADR-0014)

- Cenário: Dado o OpenAPI gerado, Quando comparado a `model_json_schema()` dos modelos usados nos endpoints, Então são iguais.
- Cenário: Dado o workspace, Quando procurado, Então não existem `packages/contracts/schema/`, o comando `exportar-schema-contratos` nem `test_schema.py`.

## MODIFICA

### RAG-SAI-001 — Saída em texto puro e validada
O sistema DEVE entregar o conteúdo gerado como texto puro, sem escape HTML, removendo caracteres de controle e invisíveis, e validá-lo contra o schema antes de entregá-lo; todo cliente DEVE renderizar esse conteúdo como texto (`textContent`), nunca como HTML. (Decisão humana A4, 2026-11-26, "autorizo"; §2, E11; §5)

- Cenário: Dado um item cujo verso contém `<script>`, Quando entregue pelo SSE e consultado na linhagem, Então chega idêntico nas duas rotas, sem escape parcial.
- Cenário: Dado um item com caractere de controle, Quando entregue, Então o caractere foi removido.

## REMOVE

Revisor: subagente `security-reviewer` (papel `seguranca-llm`), somente leitura, commit 47270ee, 2026-11-26.

# Revisão de segurança da T-025 (POST /chat por SSE e GET de linhagem) — commit 47270ee

## Veredito: **aprovado com ressalvas**

Os controles centrais desta revisão estão corretos:
- CSRF e sessão são exigidos antes de qualquer turno.
- O dono vem só da sessão.
- O 404 é igual para outro dono e para ID inexistente.
- O SSE não vaza conteúdo intermediário.
- O evento final só sai depois do commit.
- A desconexão cancela o turno com espera protegida.
- A ressalva N2 foi atendida.
- E1 e E2 não têm desvio.

As ressalvas tratam de disponibilidade (A1–A3) e de escape de saída (A4). Não achei forma de contornar autenticação, IDOR nem vazamento de prompt ou segredo.

**Limite desta revisão:** não tenho shell, então não rodei `git diff b3b2969 47270ee`. Li o `chat.py` atual inteiro e dei atenção aos trechos que o `relatorio.txt` aponta como diff: a ContextVar de progresso (105-131) e o `run`/`_run` (259-267). Não confirmei por conta própria que o restante do corpo E1–E12 é igual ao anterior, como o relatório afirma. Não rodei testes nem payloads e não li o `.env`.

## Achados

### A1 — Médio: a pipeline roda no event loop do ASGI, com banco síncrono e CPU quadrática
**Onde:**
- `chat_routes.py:67` chama `pipeline.run` direto no loop.
- O domínio faz I/O síncrono dentro de funções `async`:
  - E1: duas transações, uma delas com `pg_advisory_xact_lock`, que bloqueia (`chat.py:323-336`);
  - histórico do `gerar_mais` (`chat.py:354`);
  - `_context_for` (`chat.py:380-402`);
  - `_resolve` (`chat.py:404-439`);
  - `_persist` (`chat.py:230-251`).
- `_evidence_range` (`chat.py:75-101`) recalcula `normalize_evidence(text[:end])` a cada prefixo (`chat.py:84`), o que é O(n²) por citação. Ele roda no E9, dentro do loop.

**Risco:**
- Enquanto isso roda, o processo inteiro para: login, `/users/me`, GETs de linhagem, keepalive e detecção de desconexão de todos os usuários.
- O engine do chat (`chat_runtime.py:25`) usa o pool padrão: 5 conexões, mais 10 extras, e espera de até 30 s. As threads de recuperação (`chat.py:375`) usam o mesmo pool. Se elas o esgotarem, o próprio loop fica até 30 s esperando uma conexão.
- [INFERÊNCIA] Com chunks de até 1500 tokens (cerca de 6000 caracteres), o custo fica na ordem de segundos por citação. Não medi; na prova real, o E9 levou 0,14 s com um documento pequeno.
- Qualquer conta (o cadastro é aberto) pode repetir isso até 20 turnos por minuto.

**Correção sugerida:**
- Tirar o turno do loop. Uma forma: rodar a pipeline num worker com loop próprio, limitado por um `CapacityLimiter` dedicado, e propagar o cancelamento com `call_soon_threadsafe(task.cancel)`.
- Outra forma, que mexe no domínio e precisa de autorização: envolver os trechos síncronos em `asyncio.to_thread` e deixar `_evidence_range` linear.
- Teste: medir a latência de `/users/me` durante o E9 com um chunk de cerca de 6000 caracteres.

### A2 — Médio: sem limite de turnos simultâneos, sem prazo máximo do stream, e a admissão dorme em threads compartilhadas
**Onde:**
- `chat_runtime.py:44` cria `RequestAdmission(6, 12)` por turno. Não há orçamento global.
- `chat.py:191` faz `asyncio.to_thread(self.admission.acquire)`, que dorme com `sleep` (`chat.py:632`) numa thread do executor padrão do asyncio, que tem `min(32, cpu+4)` workers. A recuperação (`chat.py:375`) usa o mesmo executor.
- O único limite é de taxa: 20 turnos por 60 s por dono (`chat.py:325`). Não há limite de concorrência.
- O stream não tem prazo total: `chat_routes.py:102-112` manda keepalive indefinidamente.
- [INFERÊNCIA] O cliente genai é criado sem timeout (`chat_runtime.py:31-32`). Um embedding travado numa thread manteria o stream aberto para sempre.

**Risco:**
- Cerca de cpu+4 turnos simultâneos, de um único usuário ou de algumas contas, ocupam o executor e travam o chat de todos os outros.
- Cada turno pode fazer até 12 chamadas pagas ao LLM.
- As conexões SSE ficam abertas sem limite.

**Correção sugerida:**
- Limitar turnos ativos por dono (1 ou 2), recusando com 429 antes de abrir o stream.
- Pôr um limitador global de turnos que responda 503/429 quando cheio.
- Dar um prazo máximo ao turno (por exemplo `asyncio.timeout` na rota, cancelando a task, que já grava erro).
- Configurar `HttpOptions(timeout=…)` no cliente.
- Trocar o `sleep` em thread por espera assíncrona (no domínio) ou dar à admissão um executor próprio.

### A3 — Médio (antes da autenticação; depende do deploy): o corpo do POST /chat é lido e parseado sem limite
**Onde:**
- O FastAPI 0.163.2 lê o corpo com `await request.body()` e faz o parse do JSON antes de `solve_dependencies` (`.venv/.../fastapi/routing.py:458-464`, depois `:511`). Ou seja, antes de sessão e CSRF.
- `ChatRequest` não tem `max_length` (`contracts/chat.py:29-33`, `comum.py:8,20`).
- O uvicorn não limita o tamanho do corpo, e o README (`:131`) não pede limite no proxy.

**Risco:** sem autenticação, um corpo de centenas de MB vira alguns GB de memória (bytes, junção e parse do JSON), e o processo pode cair por falta de memória.

**Correção sugerida:**
- Um middleware ASGI em `/chat` que responda 413 acima de cerca de 256 KiB, contando também os bytes de corpo enviado em pedaços (chunked).
- Ou `client_max_body_size` no proxy, documentado no README.
- Limitar o tamanho de `session_id`: na rota, ou no contrato com aprovação humana.

### A4 — Médio (latente, ainda não há frontend): LLM05, escape inconsistente na fronteira web
**Onde:**
- No `final` do SSE, os itens saem escapados pelo E11 (`chat.py:592`, `rendering.py:11-24`). Mas dois campos não são escapados:
  - `omissoes[].detalhe` leva o texto livre do juiz E10 (`chat.py:550,596`; `vereditos.py:42`);
  - `fontes[].section_path` vem dos títulos do documento.
- As rotas `GET /lineage/items` e `GET /lineage/turns` (`chat_routes.py:130-160`) devolvem os originais sem escape:
  - `ItemLineage.content` e `evidence` (`training_lineage/chat.py:285-315`);
  - `turn.prompt`;
  - `input_verdict.motivo` e `input_verdict.categoria` (texto do LLM do E2).

**Caminho do ataque:**
1. O usuário envia um documento escrito por um terceiro (injeção indireta).
2. Um item gerado contém `<img src=x onerror=…>`.
3. No SSE ele chega escapado; na linhagem, bruto.
4. Um cliente que use `innerHTML` (como o escape dos itens sugere) executa o código na tela de linhagem. Um cliente que use `textContent` mostra `&lt;` no chat.

O contrato atual empurra o frontend para uma escolha insegura.

**Correção sugerida:**
- Definir uma única regra na fronteira HTTP: ou texto puro em todos os campos com `textContent` obrigatório (isso muda o E11 e é decisão humana), ou escape em todos os campos de saída listados acima.
- Documentar a regra em `interface.md` e no README.
- Testar com `<script>` dentro de um documento.

### A5 — Baixo: stream cortado sem `final` e traceback no log
**Onde:**
- `Identificador` só exige `min_length=1` (`comum.py:20`). Um `session_id` só com espaços passa o contrato.
- `chat.py:270-271` (e também `:283`) levanta exceção fora do `try`.
- `chat_routes.py:113` relança essa exceção depois do HTTP 200.

**Risco:**
- O uvicorn registra "Exception in ASGI application" com o traceback no log do servidor.
- O cliente recebe o stream cortado, sem `final`, o que contraria o API-CHT-001.
- Nada vaza para o cliente.

**Correção sugerida:** recusar `not body.session_id.strip()` com 422 antes do stream. No gerador, capturar `Exception` de `await task`, registrar só a categoria e encerrar.

### A6 — Baixo: com cliente lento, o fechamento depende do coletor de lixo
**Onde:**
- O uvicorn 0.54 anuncia `spec_version` 2.3, então o Starlette 1.10 usa um grupo de tarefas que cancela o envio ao detectar a desconexão (`responses.py:278-285`).
- Se o cancelamento atinge um `send` que espera drenagem do buffer, o gerador fica parado num `yield` (`chat_routes.py:106/112/115/116`).
- Nesse caso o `finally` (`:117-124`) só roda quando o finalizador de async generators do asyncio agenda `aclose()`.
- [INFERÊNCIA] No CPython isso costuma ser imediato, porque a contagem de referências cai para zero, mas não é determinístico.

**Correção sugerida:** `StreamingResponse(..., background=BackgroundTask(gen.aclose))`. A tarefa de background roda depois do grupo de tarefas, inclusive na desconexão (`responses.py:290-291`).

### A7 — Baixo: o `final` de erro pode sair sem commit
**Onde:** `chat.py:302-303` engole a falha ao gravar o erro e devolve um `chat_turn_id` que não existe; a rota emite o `final` mesmo assim (`chat_routes.py:116`).

**Risco:** contraria "final depois do commit" da interface. Não vaza dado: o GET desse ID responde 404.

**Correção sugerida:** sinalizar esse caso à rota, ou aceitar e documentar.

### A8 — Baixo: recusas gravadas sem limite de frequência
**Onde:** a recusa por `limite_de_taxa` ou `tamanho_excedido` (`chat.py:316-326`) é gravada por `_persist` (`:286`), com prompt de até 8000 caracteres (`:278`).

**Risco:** um cliente autenticado em laço faz o banco crescer na velocidade do HTTP.

**Correção sugerida:** verificar a taxa (tentativa sem bloqueio) antes de abrir o stream, ou resolver junto com o limite do A2.

### Informativos
- **I1:** os GETs de linhagem não enviam `Cache-Control: no-store`, ao contrário de `/users/me` (`app.py`). Eles devolvem dados privados (prompt e itens). Sugiro adicionar.
- **I2:** o GET de turno expõe `input_verdict.motivo`, `models` e `prompt_versions`. O motivo do juiz serve de pista para refinar tentativas de jailbreak contra o E2. Esse formato foi aprovado no G3; pode valer reduzir a só `categoria`.

## Controles verificados que passaram

**CSRF e sessão antes do turno**
- `require_csrf` é dependência da rota (`chat_routes.py:43`). Ele depende de `current_user` e compara em tempo constante (`csrf.py:13-25`).
- O 403 é levantado ao resolver as dependências, antes do handler. Não há escrita antes disso. O escopo (`:57`) e a task (`:74`) só vêm depois.

**Dono só da sessão**
- `owner` vem de `current_owner_id`, que é `str(user.id)` (`auth.py:186-187`).
- `Contrato` usa `extra='forbid'` (`comum.py:28`), e nenhum parâmetro de query é lido.
- As versões são filtradas por dono e por reserva `ready` (`chat_routes.py:30-40`), e o domínio confere de novo (`training_lineage/chat.py:336-353`).

**Isolamento da linhagem**
- O turno é buscado com filtro de dono e ID numa só consulta (`chat_routes.py:136-138`).
- O item usa join com o dono, as citações filtram por dono, e itens omitidos dão 404 (`training_lineage/chat.py:285-298`).
- O corpo do 404 é idêntico nos dois casos (`:149-151`, `:157-159`).
- Os IDs são `uuid4().hex`. A diferença de tempo entre "não existe" e "é de outro dono" é uma leitura de índice a mais na mesma consulta, coisa de microssegundos. [INFERÊNCIA] Não dá para explorar pela rede.

**SSE sem vazamento**
- O `progress` aceita só os nomes de etapa conhecidos e segundos finitos (`:79-99`).
- O JSON escapa caracteres de controle, então não há como injetar quadros SSE (`:26-27`).
- O E5–E7 agregado é filtrado.
- Os erros saem com mensagens fixas (`chat.py:288,296`). O 503 não traz detalhe da exceção (`chat_routes.py:58-60`). Não há traceback para o cliente.

**Final só após commit**
- `_persist` roda antes de `_run` retornar (`chat.py:286`), e a rota só emite o `final` depois de `await task` (`chat_routes.py:113-116`).
- O progresso de E8–E12 fica retido até o fim.

**Cancelamento**
- A task é criada dentro do gerador (`:74`).
- No `finally`, ela é cancelada e aguardada com proteção contra cancelamento externo (`:117-124`).
- O domínio grava erro sem itens e relança o cancelamento (`chat.py:287-291`).
- Nada é publicado depois disso.
- A thread de recuperação segue até o fim, mas é só leitura: não há escrita em `packages/retrieval`.

**N2 (transação de autenticação)**
- `current_user` confirma a transação depois de ler o token (`auth.py:180`), e `require_csrf` também (`csrf.py:21`).
- As sessões usam `scope='function'`, então fecham antes do stream (FastAPI 0.163.2).
- O produtor recebe só o `owner` em texto.
- Os pools de autenticação, de linhagem e do chat são separados.

**E1 e E2**
- A mensagem chega sem alteração a `pipeline.run`.
- O E1 normaliza e limita o tamanho (`chat.py:316-321`, `validation.py:65-81`).
- O E2 falha fechado (`chat.py:337-342`), antes do E4 e do E8.
- A API não tem outro caminho até o LLM.
- O E3 roda em paralelo sobre entrada ainda não verificada, mas só devolve um valor de enum. Isso já existia antes desta tarefa.

**ContextVar (o diff)**
- A ContextVar é definida e restaurada por execução (`chat.py:259-267`).
- O callback de progresso fica protegido por `try/except` (`:125-129`).
- Não há vazamento entre turnos: a pipeline e o callback são criados por requisição, e só passam etapa e segundos.

## Arquivos revisados
- `apps/api/src/training_api/{app,chat_routes,chat_runtime,auth (130-187),csrf,database,limits (60-85)}.py`
- `packages/generation/src/training_generation/chat.py` (inteiro) e `validation.py` (E1, `normalize_evidence`, `sanitize_item`)
- `packages/contracts/.../{chat,linhagem,comum,rendering}.py`
- `packages/lineage/.../chat.py` (285-353)
- Starlette 1.10.0 (`responses.py`), FastAPI 0.163.2 (`routing.py`), uvicorn 0.54.0 (`spec_version`)
- Governança: `delta.md`, `interface.md`, `T-025.md`, `relatorio.txt`, `real-http/response.sse`, e revisões R2 e R5 da T-024

**Para fechar as ressalvas:** os testes de verificação de cada achado precisam ser escritos pelo construtor:
- **A1:** latência do loop durante o E9.
- **A2:** turnos concorrentes de um mesmo dono.
- **A3:** corpo maior que o limite deve dar 413.
- **A4:** `<script>` num documento não pode sair sem escape em nenhuma rota.
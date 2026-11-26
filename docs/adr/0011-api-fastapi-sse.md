# ADR-0011 — API HTTP com FastAPI e Uvicorn, progresso do chat por SSE

Estado: **Aceita** (humano, 2026-11-24)

## Contexto

A `apps/api` é o adaptador HTTP do monólito modular (ADR-0010). Ela recebe a mensagem do chat (`ChatRequest`), executa E1–E12 chamando os pacotes de domínio e o ADK (ADR-0001) e devolve a `ChatResponse`.

Restrições:

- Os contratos são modelos Pydantic em `packages/contracts`; a API deve validar entrada e saída com eles, sem modelo paralelo.
- O ADK executa agentes de forma assíncrona (`Runner.run_async`), e E2 e E3 rodam em paralelo (arquitetura §2). Um servidor síncrono bloquearia um processo por mensagem durante quatro chamadas de LLM em série.
- Não há streaming do conteúdo: o item só pode ser entregue depois do E10, da E11 e da gravação da linhagem (RAG-SAI-002). Mas uma mensagem leva segundos, e o chat precisa mostrar o progresso por etapa (arquitetura §10).
- O progresso só vai do servidor para o cliente; o cliente não envia nada durante o processamento.

## Decisão

1. **FastAPI**, assíncrono de ponta a ponta, servido por **Uvicorn** (ASGI).
2. Entrada e saída validadas pelos modelos de `packages/contracts`; o ADK é chamado com `run_async`, sem bloquear o loop de eventos. Trabalho de CPU pesado no caminho do chat (reranker, ADR-0003) roda fora do loop (thread ou processo), para não travar as outras conexões.
3. **Progresso por SSE** (Server-Sent Events, `text/event-stream`) no endpoint do chat (ARQ-API-001):
   - um evento de **progresso** por etapa executada, em ordem, com a etapa e o rótulo mostrado ao usuário ("entendendo", "buscando", "gerando", "verificando");
   - um único evento **final** com a `ChatResponse` completa (itens, omissões e fontes), enviado só depois do E10, da E11 e da gravação da linhagem;
   - recusa e erro também terminam o stream com o evento final (`status` correspondente), nunca com o stream cortado sem explicação.
   O formato exato dos eventos é definido na mudança da `apps/api`.
4. **Buffering de proxy desligado** para o endpoint SSE: a resposta leva `Cache-Control: no-cache` e `X-Accel-Buffering: no`, e qualquer proxy reverso na frente da API (nginx, balanceador) é configurado sem buffering e com timeout de leitura acima da duração máxima de uma mensagem. Compressão de resposta não se aplica ao stream.
5. Sem streaming de tokens: o conteúdo gerado nunca sai antes da validação (arquitetura §10).

## Alternativas consideradas

| Opção | Por que não |
| --- | --- |
| Django / Django REST Framework | ORM e admin próprios que o projeto não usa (a linhagem tem pacote próprio); suporte assíncrono menos direto; validação por serializers paralelos aos modelos Pydantic |
| Flask | síncrono por natureza; validação Pydantic e OpenAPI exigem extensões; atrapalha o `run_async` do ADK |
| Litestar | equivalente técnico, mas comunidade e ecossistema menores; o FastAPI já foi verificado no stack (ADR-0007) |
| WebSocket para o progresso | bidirecional sem necessidade; exige protocolo próprio de mensagens, reconexão manual e tratamento especial em proxies; SSE é HTTP comum |
| Polling (cliente pergunta o estado do turno) | latência percebida maior, mais requisições, e o estado intermediário do turno teria de ser persistido só para ser consultado |
| Resposta única sem progresso | o usuário espera vários segundos sem retorno; contraria a arquitetura §10 |

## Consequências

- FastAPI e Uvicorn são dependências novas e entram com a mudança da `apps/api`, com aprovação do humano e a carência de 7 dias (ADR-0007). O FastAPI já resolveu no stack em 2026-11-24.
- O OpenAPI gerado pelo FastAPI é a fonte dos contratos HTTP (ADR-0014) e dos tipos do front (ADR-0013).
- O endpoint do chat recebe a `ChatRequest` no corpo (POST); por isso o front lê o stream com `fetch`, não com `EventSource`, que só faz GET.
- Cliente que cai no meio do stream perde o progresso; o resultado continua gravado na linhagem e pode ser consultado pelo `chat_turn_id`. A retomada de stream não faz parte da v1.
- Deploy atrás de proxy exige a configuração do item 4; sem ela, o cliente recebe todos os eventos de uma vez no final.
- Autenticação não é decidida aqui: fica para a mudança da `apps/api` (arquitetura §11).

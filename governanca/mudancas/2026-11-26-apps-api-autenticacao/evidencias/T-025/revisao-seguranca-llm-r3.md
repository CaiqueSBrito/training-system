Revisor: subagente `security-reviewer` (papel `seguranca-llm`), somente leitura, commit ceb358e, 2026-11-26.

# Reavaliação curta da T-025, rodada 4 — commit ceb358e

## Veredito: **aprovado**

R1, R3, R4 e R5 estão corrigidos e não achei regressão. Sobraram duas observações informativas, nenhuma bloqueante.

Esta revisão foi só leitura: `relatorio-r5.txt`, `chat_body.py`, `chat_execution.py`, `chat_routes.py`, `chat_runtime.py`, `config.py` e `rendering.py`, além de `get_route_path` no Starlette, `IndexingPipeline.from_env` e os contratos de linhagem e de item. Não rodei testes e não li o `.env`.

## R1 — limite de corpo com `root_path`: corrigido
- O middleware agora compara `get_route_path(scope)` com `/chat` (`chat_body.py:3,13`).
- O roteador do Starlette usa essa mesma função (`starlette/_utils.py:137-152`), então middleware e roteamento sempre concordam.
- Com `root_path=/api` e `path=/api/chat`, a função devolve `/chat`, e o limite de 413 vale tanto com `Content-Length` (`:17-23`) quanto com corpo chunked (`:25-33`).
- Um path que não começa pelo `root_path` também é tratado igual pelos dois lados, então não há divergência que permita contornar o limite.

## R3 — timeout do Qdrant e aviso de drenagem: corrigido
- O `QdrantClient` é criado com `timeout=api_settings.chat_qdrant_timeout_seconds` (`chat_runtime.py:33-35`). O padrão é 5 s, validado como inteiro positivo (`config.py:43`).
- O `close` do cliente fica registrado no `ExitStack` antes dos demais recursos (`:36`).
- A composição manual `IndexingPipeline(model_client, QdrantIndex(client, model_settings))` (`:42`) é equivalente a `from_env` (`indexing.py:225-230`): mesma URL, `cloud_inference` e `ensure_collection`. A única diferença é o timeout.
- O índice usado na recuperação é esse mesmo cliente (`:51`).
- Na drenagem, um `call_later` registra o aviso ainda durante a espera e é cancelado no `finally` (`chat_execution.py:166-174`). A mensagem é fixa, sem dado.
- A vaga só é liberada depois da drenagem, como antes.
- `set_event_loop(None)` e `loop.close()` rodam mesmo se a drenagem falhar (`:175-179`).
- Efeito colateral, não é defeito: depois de um timeout o prazo já passou, então `call_later(0)` registra o aviso em toda drenagem por timeout, mesmo quando ela é rápida. É só um log a mais.

## R4 — cancelamento por timestamptz: corrigido
- O filtro e a ordenação fazem `CAST(record->>'created_at' AS timestamptz)` e comparam com um `datetime` UTC (`chat_execution.py:103-107`). Não há mais comparação de texto.
- Os dois relógios são do mesmo processo Python: `started` em `:85` e o `created_at` do domínio.
- O filtro por dono e sessão continua (`:103`), então não há como pegar turno de outro dono.
- Um cast inválido levanta exceção, que é capturada (`:127`). Nesse caso a função grava um erro próprio e confirma o commit, ou devolve o final sem ID.

## R5 — limpeza de controles na linhagem: corrigido
- `clean_text` é a regra única (`rendering.py:11-14`), com o mesmo predicado de antes. O E11 continua usando essa mesma função via `render_item` (`:17-26`).
- `lineage_output` (`chat_routes.py:176-187`) limpa uma cópia do resultado de `jsonable_encoder`, filtrando pelo nome do campo. Os GETs usam essa saída (`:218,227`).
- O persistido não muda: só uma cópia é limpa.
- Conferi contra os contratos que todos os textos que vêm do LLM, do usuário ou do documento estão na lista de campos limpos:
  - itens: `front`, `back`, `stem`, `options`, `explanation`, `evidence`;
  - spans e fontes: `section_path`, inclusive dentro de listas;
  - `prompt`, `detalhe`, `mensagem` e `categoria`.
- Ficam de fora só IDs, offsets, enums e os mapas de `models` e `prompt_versions` (que vêm da configuração). Nenhum deles é texto vindo de entrada.
- A projeção do I2, só `categoria`, continua (`chat_routes.py:199-201`).

## Regressão
Nenhuma encontrada. Continuam iguais à rodada anterior:
- vaga, cancelamento e confirmação de commit (`chat_execution.py:20-46,89-95,71-77`);
- limite de taxa antes do SSE (`:52-68`);
- `ManagedGenerator` com `BackgroundTask` (`chat_routes.py:157-172`);
- `no-store` nos GETs (`:213,222`).

## Observações remanescentes (informativas)

**O1 — Informativo: o `final` do SSE ainda não passa pela limpeza de controles**
- **Onde:** `chat_routes.py:143` emite `response.model_dump()` direto. `omissoes[].detalhe` (texto livre do juiz E10) e `fontes[].section_path` (títulos do documento) saem sem `clean_text`. Os itens já vêm limpos pelo E11.
- **Risco:** o mesmo turno sai sem esses caracteres no `GET /lineage/turns` e com eles no `final` do SSE. Com `textContent` não há XSS; o efeito é só a reordenação visual por caracteres de direção, como U+202E.
- **Correção:** aplicar `lineage_output`, ou a mesma projeção, ao `final`.

**O2 — Informativo: `chat_body.py:3` importa `starlette._utils.get_route_path`, que é API privada**
- **Risco:** uma atualização do Starlette pode remover ou renomear a função e quebrar o import. A quebra seria barulhenta, no import, e não silenciosa.
- **Correção:** fixar a versão ou cobrir com teste, que já existe segundo o `relatorio-r5.txt`.

R2 (vazamento da vaga com servidor ASGI 2.4) continua informativo e fora do escopo, como o relatório registra. O uvicorn instalado anuncia 2.3.
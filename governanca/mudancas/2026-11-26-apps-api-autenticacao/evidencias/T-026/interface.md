# T-026 — interface dos casos (G3)

Requisito: API-CTR-001 (ADR-0014). Arquivo de casos: `apps/api/tests/test_openapi_contratos.py` (sha256 em `g3-casos.txt`).

O OpenAPI vem da app real (`training_api.app.create_app`) sem banco: os engines são preguiçosos e `app.openapi()` não roda o lifespan. Não há dublê.

## Modelos esperados no OpenAPI

| Rota | Onde | Modelo de `packages/contracts` | Modo de `model_json_schema()` | Hoje |
| --- | --- | --- | --- | --- |
| `POST /chat` | corpo (`requestBody`) | `ChatRequest` | `validation` | publicado e igual (**passa**) |
| `POST /chat` | resposta 200 (`text/event-stream`, evento `final`) | `ChatResponse` | `serialization` | ausente (resposta 200 com schema vazio) |
| `GET /lineage/items/{item_id}` | resposta 200 | `ItemLineage` | `serialization` | ausente (resposta 200 com schema vazio) |

A lista exata de componentes de contrato no OpenAPI é o fecho desses três modelos (raiz e `$defs`): `ChatRequest`, `ChatResponse`, `ItemLineage`, `BloomLevel`, `ChatStatus`, `Citation`, `CitationLineage`, `DeliveredItem`, `Flashcard`, `MotivoRecusa`, `MotivoRejeicao`, `Omission`, `Question`, `Source`. Qualquer outro modelo de contrato publicado (ex.: `ChatTurnRecord`) falha como sobrando. Um componente que não é de contrato e tem o mesmo conjunto de campos de um contrato publicado falha como modelo paralelo da API.

Componentes próprios da API, que não são contrato e o teste aceita: `UserCreate`, `UserRead`, `PasswordChange`, `Body_login_auth_login_post`, `HTTPValidationError`, `ValidationError`.

`GET /lineage/turns/{chat_turn_id}` fica fora da igualdade. A resposta dessa rota é composta (`{turn, response, items}`), e `turn` é uma projeção de `ChatTurnRecord` (o `input_verdict` só leva `categoria`). Nenhum modelo de contrato descreve essa resposta inteira. Para publicá-la, é preciso criar um modelo de contrato novo, ou seja, mudar contrato, o que está fora do escopo da T-026. **Ponto para o humano no G3.**

## Como comparam

- Os dois lados têm os `$ref` resolvidos recursivamente: no OpenAPI, `#/components/schemas/`; no modelo, `#/$defs/`. Depois, os dois são comparados por igualdade estrutural.
- O componente comparado é o que a própria rota referencia. Se o FastAPI usar `Nome-Input`/`Nome-Output`, esse nome também vale.
- Duas normalizações, só de forma, e as duas vêm do FastAPI 0.163:
  1. `discriminator.mapping` guarda o `$ref` como texto, então compara-se só o nome do alvo;
  2. o FastAPI serializa o OpenAPI com `exclude_none=True`, o que apaga `"default": null`; por isso essa chave sai do lado do modelo. A lista `required` não é normalizada, então campo obrigatório continua conferido.
- Números são comparados por valor, como em JSON: o FastAPI escreve `minimum: 1.0` onde o Pydantic escreve `1`.
- Prova de que os casos podem passar, feita fora do repositório e não versionada: numa app FastAPI mínima com `ChatRequest` no corpo e `ChatResponse`/`ItemLineage` como `response_model`, os casos de igualdade e de lista exata passam. Com um `ItemLineage` alterado, ou com um modelo paralelo de mesmos campos que `ChatResponse`, eles falham.

## O que será removido (passo 2, depois do G3)

| Item | Caso que confere a ausência |
| --- | --- |
| `packages/contracts/schema/` (32 arquivos `*.schema.json`) | `test_workspace_sem_json_schema_exportado[packages/contracts/schema/]` |
| script `exportar-schema-contratos` em `packages/contracts/pyproject.toml` | `[script exportar-schema-contratos]` |
| `packages/contracts/tests/test_schema.py` | `[packages/contracts/tests/test_schema.py]` |
| `packages/contracts/src/training_contracts/schema.py` (módulo que só implementa a exportação) | `[packages/contracts/src/training_contracts/schema.py]` |

O quarto item não está escrito na lista do delta. Ele entra porque o módulo existe só para o comando removido (ADR-0014, item 2). **Ponto para o humano no G3:** aprovar ou retirar esse caso.

## Conflito de escopo para a implementação

Os casos de `ChatResponse`, de `ItemLineage` e da lista exata só passam se as rotas de `apps/api` declararem esses modelos no OpenAPI, por exemplo com `response_model` ou `responses=`. A T-026 proíbe editar rotas da API, e `apps/api/` é do papel `rag-backend`. A parte do `arquiteto` (`packages/contracts/`) resolve só os quatro casos de ausência. Decisão do humano/orquestrador: quem altera as rotas, e em qual tarefa.

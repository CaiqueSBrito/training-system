"""T-026 (G3): contratos publicados pelo OpenAPI da apps/api (API-CTR-001; ADR-0014).

Cenários de API-CTR-001:

- Dado o OpenAPI gerado, Quando comparado a `model_json_schema()` dos modelos de
  `packages/contracts` usados nos endpoints, Então são iguais.
- Dado o workspace, Quando procurado, Então não existem `packages/contracts/schema/`,
  o comando `exportar-schema-contratos` nem `test_schema.py`.

O OpenAPI vem da app real (`create_app`) sem banco: os engines do SQLAlchemy são
preguiçosos e `app.openapi()` não executa o lifespan nem abre conexão. Nenhum dublê.

Comparação: os dois lados têm os `$ref` resolvidos recursivamente (OpenAPI em
`#/components/schemas/`, modelo em `#/$defs/`) e são comparados por igualdade
estrutural. Entrada (corpo) usa o modo `validation`; saída usa `serialization`.
Duas normalizações, só de forma, ambas do próprio FastAPI 0.163 ao gerar o OpenAPI:
- `discriminator.mapping` guarda o `$ref` como texto; compara-se o nome do alvo;
- `"default": null` some do OpenAPI (FastAPI serializa o documento com
  `exclude_none=True`); por isso é retirado do lado do modelo. Campo obrigatório
  continua conferido pela lista `required`, que não é normalizada.
Números são comparados por valor (`1 == 1.0`), como em JSON.

Interface e itens a remover: governanca/mudancas/2026-11-26-apps-api-autenticacao/
evidencias/T-026/interface.md.
"""

from pathlib import Path
import tomllib

import pytest
from pydantic import BaseModel
import training_contracts
from training_contracts import ChatRequest, ChatResponse, ItemLineage

RAIZ = Path(__file__).resolve().parents[3]
CONTRATOS = RAIZ / "packages" / "contracts"

# (método, rota, onde, modelo, modo). "corpo" = requestBody; "200" = resposta 200.
# POST /chat responde em text/event-stream; o evento final carrega a ChatResponse
# (API-CHT-001), que tem de estar publicada na resposta 200 da rota.
USOS = (
    ("post", "/chat", "corpo", ChatRequest, "validation"),
    ("post", "/chat", "200", ChatResponse, "serialization"),
    ("get", "/lineage/items/{item_id}", "200", ItemLineage, "serialization"),
)

PREFIXO_OPENAPI = "#/components/schemas/"
PREFIXO_MODELO = "#/$defs/"


@pytest.fixture(scope="module")
def openapi():
    from training_api.app import create_app
    from training_api.config import ApiSettings

    settings = ApiSettings(
        database_url="postgresql+psycopg://openapi@127.0.0.1:1/sem_banco",
        attempt_key_secret="segredo-so-para-gerar-openapi",
    )
    app = create_app(settings)
    try:
        yield app.openapi()
    finally:
        app.state.lineage_engine.dispose()


def resolver(schema, definicoes, prefixo, pilha=()):
    """Inlina todo `$ref` local; remove `$defs` (já inlinados) e normaliza o discriminador."""
    if isinstance(schema, list):
        return [resolver(item, definicoes, prefixo, pilha) for item in schema]
    if not isinstance(schema, dict):
        return schema
    if isinstance(schema.get("discriminator"), dict) and "mapping" in schema["discriminator"]:
        discriminador = dict(schema["discriminator"])
        discriminador["mapping"] = {
            chave: alvo.removeprefix(prefixo) for chave, alvo in discriminador["mapping"].items()
        }
        schema = {**schema, "discriminator": discriminador}
    if "$ref" in schema:
        ref = schema["$ref"]
        assert ref.startswith(prefixo), f"$ref fora de {prefixo}: {ref}"
        nome = ref.removeprefix(prefixo)
        assert nome not in pilha, f"$ref recursivo não previsto: {' -> '.join((*pilha, nome))}"
        alvo = resolver(definicoes[nome], definicoes, prefixo, (*pilha, nome))
        resto = {k: resolver(v, definicoes, prefixo, pilha) for k, v in schema.items() if k != "$ref"}
        return {**alvo, **resto}
    return {k: resolver(v, definicoes, prefixo, pilha) for k, v in schema.items() if k != "$defs"}


def sem_default_nulo(schema):
    if isinstance(schema, list):
        return [sem_default_nulo(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    return {
        k: sem_default_nulo(v) for k, v in schema.items()
        if not (k == "default" and v is None)
    }


def schema_do_modelo(modelo: type[BaseModel], modo: str) -> dict:
    bruto = modelo.model_json_schema(mode=modo)
    return sem_default_nulo(resolver(bruto, bruto.get("$defs", {}), PREFIXO_MODELO))


def nome_base(componente: str) -> str:
    """FastAPI separa entrada/saída como `Nome-Input`/`Nome-Output` quando diferem."""
    for sufixo in ("-Input", "-Output"):
        if componente.endswith(sufixo):
            return componente.removesuffix(sufixo)
    return componente


def refs(no):
    """Nomes de componentes referenciados (diretamente) dentro de um nó do OpenAPI."""
    if isinstance(no, list):
        for item in no:
            yield from refs(item)
    elif isinstance(no, dict):
        ref = no.get("$ref")
        if isinstance(ref, str) and ref.startswith(PREFIXO_OPENAPI):
            yield ref.removeprefix(PREFIXO_OPENAPI)
        for valor in no.values():
            yield from refs(valor)


def local_da_rota(openapi, metodo, rota, onde):
    operacao = openapi["paths"].get(rota, {}).get(metodo)
    assert operacao is not None, f"{metodo.upper()} {rota} ausente do OpenAPI"
    if onde == "corpo":
        local = operacao.get("requestBody")
    else:
        local = operacao.get("responses", {}).get(onde)
    assert local is not None, f"{metodo.upper()} {rota}: sem {onde} no OpenAPI"
    return local


def nomes_do_fecho(modelos) -> set[str]:
    """Modelo raiz + todos os `$defs` dele, nos dois modos."""
    nomes = set()
    for modelo in modelos:
        nomes.add(modelo.__name__)
        for modo in ("validation", "serialization"):
            nomes.update(modelo.model_json_schema(mode=modo).get("$defs", {}))
    return nomes


def nomes_de_contrato() -> set[str]:
    """Todo nome de modelo/enum que `packages/contracts` define (raízes exportadas e seus $defs)."""
    raizes = [
        valor for nome in training_contracts.__all__
        if isinstance(valor := getattr(training_contracts, nome), type) and issubclass(valor, BaseModel)
    ]
    return nomes_do_fecho(raizes)


@pytest.mark.parametrize(
    ("metodo", "rota", "onde", "modelo", "modo"), USOS,
    ids=[f"{m.upper()} {r} {o} = {mod.__name__} ({modo})" for m, r, o, mod, modo in USOS],
)
def test_openapi_publica_contrato_igual_ao_model_json_schema(openapi, metodo, rota, onde, modelo, modo):
    componentes = openapi.get("components", {}).get("schemas", {})
    local = local_da_rota(openapi, metodo, rota, onde)
    candidatos = sorted({nome for nome in refs(local) if nome_base(nome) == modelo.__name__})
    assert candidatos, (
        f"{metodo.upper()} {rota} {onde}: {modelo.__name__} não é referenciado no OpenAPI; "
        f"referências encontradas: {sorted(set(refs(local)))}"
    )
    esperado = schema_do_modelo(modelo, modo)
    for nome in candidatos:
        publicado = resolver({"$ref": PREFIXO_OPENAPI + nome}, componentes, PREFIXO_OPENAPI)
        assert publicado == esperado, f"{nome} no OpenAPI difere de {modelo.__name__}.model_json_schema(mode={modo!r})"


def test_openapi_lista_exatamente_os_contratos_usados_pelas_rotas(openapi):
    componentes = openapi.get("components", {}).get("schemas", {})
    contratos = nomes_de_contrato()
    esperados = nomes_do_fecho({modelo for *_, modelo, _ in USOS})

    publicados_de_contrato = {nome_base(n) for n in componentes if nome_base(n) in contratos}
    assert publicados_de_contrato == esperados, (
        f"faltando: {sorted(esperados - publicados_de_contrato)}; "
        f"sobrando: {sorted(publicados_de_contrato - esperados)}"
    )

    # Sem modelo paralelo da API para o mesmo dado: nenhum componente que não seja
    # de contrato tem o mesmo conjunto de campos de um contrato publicado.
    campos_de_contrato = {}
    for modelo in {modelo for *_, modelo, _ in USOS}:
        for modo in ("validation", "serialization"):
            bruto = modelo.model_json_schema(mode=modo)
            for nome, definicao in {modelo.__name__: bruto, **bruto.get("$defs", {})}.items():
                if definicao.get("properties"):
                    campos_de_contrato[frozenset(definicao["properties"])] = nome
    paralelos = {
        nome: campos_de_contrato[frozenset(schema["properties"])]
        for nome, schema in componentes.items()
        if nome_base(nome) not in contratos
        and schema.get("properties")
        and frozenset(schema["properties"]) in campos_de_contrato
    }
    assert not paralelos, f"modelos da API paralelos a contratos: {paralelos}"


@pytest.mark.parametrize(
    "item",
    [
        "packages/contracts/schema/",
        "script exportar-schema-contratos",
        "packages/contracts/tests/test_schema.py",
        "packages/contracts/src/training_contracts/schema.py",
    ],
)
def test_workspace_sem_json_schema_exportado(item):
    if item == "script exportar-schema-contratos":
        projeto = tomllib.loads((CONTRATOS / "pyproject.toml").read_text(encoding="utf-8"))
        scripts = projeto.get("project", {}).get("scripts", {})
        assert "exportar-schema-contratos" not in scripts, f"script ainda declarado: {scripts}"
        return
    assert not (RAIZ / item).exists(), f"{item} ainda existe"

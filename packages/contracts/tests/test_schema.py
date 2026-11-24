"""Estabilidade do JSON Schema exportado (proposta 2026-11-24-spec-contratos-chat, item 3).

Falha se o schema gerado pelos modelos divergir dos arquivos versionados em
`packages/contracts/schema/`. Para atualizar: `uv run exportar-schema-contratos`.
"""

import pytest

from training_contracts import schema

GERADOS = schema.gerar()


def test_contratos_schema_sem_arquivo_faltando_ou_sobrando():
    versionados = {p.name for p in schema.DIRETORIO.glob("*.schema.json")}

    assert versionados == set(GERADOS)


@pytest.mark.parametrize("nome", sorted(GERADOS))
def test_contratos_schema_gerado_igual_ao_versionado(nome):
    versionado = (schema.DIRETORIO / nome).read_text(encoding="utf-8")

    assert GERADOS[nome] == versionado, f"{nome} divergiu; rode `uv run exportar-schema-contratos`"

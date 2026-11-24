"""RAG-PLN-001 — plano de busca do agente pedagógico (E4)."""

import pytest
from pydantic import ValidationError

from training_contracts import BloomLevel, SearchPlan

OBJETIVOS = ["explicar as fases da fotossíntese"]


def test_rag_pln_001_niveis_de_bloom_revisada():
    assert {b.value for b in BloomLevel} == {
        "lembrar",
        "compreender",
        "aplicar",
        "analisar",
        "avaliar",
        "criar",
    }


@pytest.mark.parametrize("n", [1, 2, 3])
def test_rag_pln_001_plano_com_1_a_3_consultas_e_aceito(n):
    consultas = [f"consulta {i}" for i in range(n)]

    plano = SearchPlan(objetivos=OBJETIVOS, bloom_level="compreender", consultas=consultas)

    assert plano.consultas == consultas
    assert plano.bloom_level == BloomLevel.COMPREENDER


@pytest.mark.parametrize("n", [0, 4])
def test_rag_pln_001_plano_com_0_ou_4_consultas_e_rejeitado(n):
    with pytest.raises(ValidationError):
        SearchPlan(
            objetivos=OBJETIVOS,
            bloom_level="compreender",
            consultas=[f"consulta {i}" for i in range(n)],
        )


def test_rag_pln_001_plano_sem_objetivos_e_rejeitado():
    with pytest.raises(ValidationError):
        SearchPlan(objetivos=[], bloom_level="lembrar", consultas=["fase clara"])


def test_rag_pln_001_nivel_de_bloom_fora_da_lista_e_rejeitado():
    with pytest.raises(ValidationError):
        SearchPlan(objetivos=OBJETIVOS, bloom_level="memorizar", consultas=["fase clara"])


@pytest.mark.parametrize("vazia", ["", "   "])
def test_rag_pln_001_consulta_vazia_e_rejeitada(vazia):
    with pytest.raises(ValidationError):
        SearchPlan(objetivos=OBJETIVOS, bloom_level="lembrar", consultas=[vazia])

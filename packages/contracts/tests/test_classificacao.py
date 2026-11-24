"""RAG-CLS-001 — intenções fechadas do classificador (E3)."""

import pytest
from pydantic import ValidationError

from training_contracts import Classification, Intent, RetrievalMode

INTENCOES_DE_GERACAO = ["gerar_flashcards", "gerar_questionario", "gerar_mais"]


def test_rag_cls_001_lista_fechada_de_intencoes():
    assert {i.value for i in Intent} == {
        "gerar_flashcards",
        "gerar_questionario",
        "gerar_mais",
        "fora_de_escopo",
    }


def test_rag_cls_001_modos_cobertura_e_consulta():
    assert {m.value for m in RetrievalMode} == {"cobertura", "consulta"}


def test_rag_cls_001_baralho_do_capitulo_e_flashcards_em_cobertura():
    saida = Classification.model_validate_json(
        '{"intencao": "gerar_flashcards", "modo": "cobertura", "escopo": "capítulo 3"}'
    )

    assert saida.intencao == Intent.GERAR_FLASHCARDS
    assert saida.modo == RetrievalMode.COBERTURA


def test_rag_cls_001_intencao_fora_da_lista_e_rejeitada():
    with pytest.raises(ValidationError):
        Classification.model_validate_json(
            '{"intencao": "resumir", "modo": "consulta", "escopo": "fotossíntese"}'
        )


@pytest.mark.parametrize("intencao", INTENCOES_DE_GERACAO)
def test_rag_cls_001_intencao_de_geracao_exige_modo(intencao):
    with pytest.raises(ValidationError):
        Classification(intencao=intencao, modo=None, escopo="fotossíntese")


@pytest.mark.parametrize("intencao", INTENCOES_DE_GERACAO)
@pytest.mark.parametrize("modo", ["cobertura", "consulta"])
def test_rag_cls_001_intencao_de_geracao_aceita_os_dois_modos(intencao, modo):
    saida = Classification(intencao=intencao, modo=modo, escopo="fotossíntese")

    assert saida.modo == RetrievalMode(modo)


def test_rag_cls_001_modo_fora_da_lista_e_rejeitado():
    with pytest.raises(ValidationError):
        Classification(intencao="gerar_flashcards", modo="aleatorio", escopo="capítulo 3")


def test_rag_cls_001_fora_de_escopo_nao_tem_modo():
    saida = Classification(intencao="fora_de_escopo", modo=None, escopo=None)

    assert saida.modo is None


def test_rag_cls_001_fora_de_escopo_com_modo_e_rejeitado():
    with pytest.raises(ValidationError):
        Classification(intencao="fora_de_escopo", modo="consulta", escopo=None)

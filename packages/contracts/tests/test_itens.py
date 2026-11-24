"""RAG-GEN-002 — item com citação obrigatória (E8) e invariantes de `Question`."""

import pytest
from pydantic import ValidationError

from training_contracts import Citation, Flashcard, Question

CITACAO = {"chunk_ref": "C3", "evidence": "a fase clara ocorre nos tilacoides"}


def flashcard(**campos):
    base = {
        "front": "Onde ocorre a fase clara?",
        "back": "Nos tilacoides.",
        "bloom_level": "lembrar",
        "citations": [CITACAO],
    }
    return Flashcard(**{**base, **campos})


def questao(**campos):
    base = {
        "stem": "Onde ocorre a fase clara?",
        "options": ["Tilacoides", "Estroma", "Mitocôndria", "Núcleo"],
        "correct_index": 0,
        "explanation": "A fase clara depende da membrana dos tilacoides.",
        "bloom_level": "lembrar",
        "citations": [CITACAO],
    }
    return Question(**{**base, **campos})


# RAG-GEN-002


def test_rag_gen_002_flashcard_com_citacao_e_aceito():
    card = flashcard()

    assert card.citations == [Citation(**CITACAO)]


def test_rag_gen_002_questao_com_citacao_e_aceita():
    assert questao().citations[0].chunk_ref == "C3"


@pytest.mark.parametrize("fabrica", [flashcard, questao], ids=["flashcard", "questao"])
def test_rag_gen_002_item_sem_citacao_e_rejeitado(fabrica):
    with pytest.raises(ValidationError):
        fabrica(citations=[])


@pytest.mark.parametrize("fabrica", [flashcard, questao], ids=["flashcard", "questao"])
def test_rag_gen_002_item_sem_campo_citations_e_rejeitado(fabrica):
    base = fabrica().model_dump()
    del base["citations"]
    with pytest.raises(ValidationError):
        type(fabrica()).model_validate(base)


@pytest.mark.parametrize("campo", ["chunk_ref", "evidence"])
def test_rag_gen_002_citacao_sem_campo_obrigatorio_e_rejeitada(campo):
    dados = {k: v for k, v in CITACAO.items() if k != campo}
    with pytest.raises(ValidationError):
        Citation(**dados)


@pytest.mark.parametrize("vazia", ["", "   "])
def test_rag_gen_002_evidencia_vazia_e_rejeitada(vazia):
    with pytest.raises(ValidationError):
        Citation(chunk_ref="C3", evidence=vazia)


@pytest.mark.parametrize("ref", ["C1", "C12"])
def test_rag_gen_002_chunk_ref_no_formato_do_contexto_numerado(ref):
    assert Citation(chunk_ref=ref, evidence="trecho").chunk_ref == ref


@pytest.mark.parametrize("ref", ["", "3", "c3", "C0", "[C3]", "C3a"])
def test_rag_gen_002_chunk_ref_fora_do_formato_e_rejeitado(ref):
    with pytest.raises(ValidationError):
        Citation(chunk_ref=ref, evidence="trecho")


# Invariantes de Question (arquitetura §2.3, RAG-VAL-002)


@pytest.mark.parametrize("indice", [0, 3])
def test_rag_val_002_questao_correct_index_nos_limites_e_aceito(indice):
    assert questao(correct_index=indice).correct_index == indice


@pytest.mark.parametrize("indice", [4, -1])
def test_rag_val_002_questao_correct_index_fora_do_intervalo_e_rejeitado(indice):
    with pytest.raises(ValidationError):
        questao(correct_index=indice)


def test_rag_val_002_questao_duas_alternativas_e_aceita():
    assert len(questao(options=["Sim", "Não"], correct_index=1).options) == 2


@pytest.mark.parametrize("opcoes", [[], ["Tilacoides"]], ids=["zero", "uma"])
def test_rag_val_002_questao_menos_de_duas_alternativas_e_rejeitada(opcoes):
    with pytest.raises(ValidationError):
        questao(options=opcoes, correct_index=0)


def test_rag_val_002_questao_alternativas_repetidas_sao_rejeitadas():
    with pytest.raises(ValidationError):
        questao(options=["Tilacoides", "Estroma", "Tilacoides"], correct_index=1)

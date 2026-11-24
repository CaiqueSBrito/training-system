"""Revisão T-001, achados 1 e 3 — `tipo` obrigatório e saída bruta validável item a item.

RAG-GEN-002, RAG-VAL-001, RAG-VAL-002, RAG-JUI-003 (arquitetura §2.2, §2.3).
"""

import json

import pytest
from pydantic import ValidationError

from training_contracts import (
    Flashcard,
    GenerationOutput,
    NumberedContext,
    Question,
    RawGenerationOutput,
    validar_item,
)

CITACAO = {"chunk_ref": "C1", "evidence": "a fase clara ocorre nos tilacoides"}
FLASHCARD = {
    "tipo": "flashcard",
    "front": "Onde ocorre a fase clara?",
    "back": "Nos tilacoides.",
    "bloom_level": "lembrar",
    "citations": [CITACAO],
}
QUESTAO = {
    "tipo": "question",
    "stem": "Onde ocorre a fase clara?",
    "options": ["Tilacoides", "Estroma"],
    "correct_index": 0,
    "explanation": "Depende da membrana dos tilacoides.",
    "bloom_level": "lembrar",
    "citations": [CITACAO],
}


def sem_tipo(item):
    return {k: v for k, v in item.items() if k != "tipo"}


# Achado 1: schema e validação coerentes quanto a `tipo`


@pytest.mark.parametrize("modelo", [Flashcard, Question])
def test_rag_gen_002_tipo_obrigatorio_no_schema(modelo):
    assert "tipo" in modelo.model_json_schema()["required"]


def test_rag_gen_002_tipo_obrigatorio_no_schema_da_geracao():
    defs = GenerationOutput.model_json_schema()["$defs"]

    assert "tipo" in defs["Flashcard"]["required"]
    assert "tipo" in defs["Question"]["required"]


@pytest.mark.parametrize(("modelo", "dados"), [(Flashcard, FLASHCARD), (Question, QUESTAO)])
def test_rag_gen_002_dados_sem_tipo_sao_rejeitados(modelo, dados):
    with pytest.raises(ValidationError):
        modelo.model_validate(sem_tipo(dados))
    with pytest.raises(ValidationError):
        modelo.model_validate_json(json.dumps(sem_tipo(dados)))


@pytest.mark.parametrize("dados", [FLASHCARD, QUESTAO])
def test_rag_gen_002_lote_sem_tipo_e_rejeitado(dados):
    with pytest.raises(ValidationError):
        GenerationOutput.model_validate({"itens": [sem_tipo(dados)]})


@pytest.mark.parametrize(("modelo", "dados"), [(Flashcard, FLASHCARD), (Question, QUESTAO)])
def test_rag_gen_002_tipo_errado_e_rejeitado(modelo, dados):
    outro = "question" if dados["tipo"] == "flashcard" else "flashcard"
    with pytest.raises(ValidationError):
        modelo.model_validate({**dados, "tipo": outro})


@pytest.mark.parametrize(("modelo", "dados"), [(Flashcard, FLASHCARD), (Question, QUESTAO)])
def test_rag_gen_002_construtor_python_preenche_tipo_fixo(modelo, dados):
    item = modelo(**sem_tipo(dados))

    assert item.tipo == dados["tipo"]
    assert item.model_dump()["tipo"] == dados["tipo"]


def test_rag_gen_002_lote_com_tipo_ida_e_volta():
    saida = GenerationOutput.model_validate({"itens": [FLASHCARD, QUESTAO]})

    assert [type(i) for i in saida.itens] == [Flashcard, Question]
    assert GenerationOutput.model_validate_json(saida.model_dump_json()) == saida


# Achado 3: saída bruta validável item a item


def test_rag_jui_003_item_ruim_nao_derruba_o_lote_bruto():
    ruim = {**FLASHCARD, "citations": []}
    bruta = RawGenerationOutput.model_validate({"itens": [FLASHCARD, ruim, QUESTAO]})

    assert len(bruta.itens) == 3
    assert isinstance(validar_item(bruta.itens[0]), Flashcard)
    assert isinstance(validar_item(bruta.itens[2]), Question)
    with pytest.raises(ValidationError):
        validar_item(bruta.itens[1])


def test_rag_jui_003_lote_estrito_continua_rejeitando_item_ruim():
    with pytest.raises(ValidationError):
        GenerationOutput.model_validate({"itens": [FLASHCARD, {**FLASHCARD, "citations": []}]})


@pytest.mark.parametrize(
    "ruim",
    [
        {**QUESTAO, "correct_index": 5},
        {**QUESTAO, "options": ["A", "a"]},
        sem_tipo(FLASHCARD),
        {"lixo": True},
    ],
    ids=["indice", "repetidas", "sem_tipo", "lixo"],
)
def test_rag_val_002_validar_item_rejeita_item_mal_formado(ruim):
    with pytest.raises(ValidationError):
        validar_item(ruim)


def test_rag_jui_003_envelope_bruto_exige_lista_de_objetos():
    with pytest.raises(ValidationError):
        RawGenerationOutput.model_validate({"itens": ["texto solto"]})
    with pytest.raises(ValidationError):
        RawGenerationOutput.model_validate({})


# Contexto numerado (RAG-GEN-001)


def chunk(ref):
    return {"ref": ref, "chunk_id": f"id-{ref}", "page": 12, "section_path": ["2", "2.3"], "text": "texto"}


def test_rag_gen_001_contexto_c1_a_cn_e_aceito_e_resolve_ref():
    ctx = NumberedContext.model_validate({"chunks": [chunk("C1"), chunk("C2")]})

    assert ctx.chunk("C2").chunk_id == "id-C2"
    assert ctx.chunk("C9") is None


@pytest.mark.parametrize("refs", [["C2"], ["C1", "C3"], ["C2", "C1"], []], ids=["inicio", "lacuna", "ordem", "vazio"])
def test_rag_gen_001_contexto_fora_de_sequencia_e_rejeitado(refs):
    with pytest.raises(ValidationError):
        NumberedContext.model_validate({"chunks": [chunk(r) for r in refs]})

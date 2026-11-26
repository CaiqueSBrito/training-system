"""E1 (higiene), E9 (validação determinística) e E11 (saída sanitizada) como consumidor real."""

import pytest

from training_contracts import (
    DeterministicVerdict,
    Flashcard,
    MotivoRecusa,
    MotivoRejeicao,
    Question,
    RawGenerationOutput,
)
from training_generation.validation import (
    SaidaInvalida,
    normalize_evidence,
    normalize_input,
    sanitize_item,
    validate_items,
)


def card(front="O que a clorofila absorve na fase clara?", back="A luz.", ref="C1",
         evidence="a luz é absorvida pela clorofila", **extra):
    item = {"tipo": "flashcard", "front": front, "back": back, "bloom_level": "lembrar",
            "citations": [{"chunk_ref": ref, "evidence": evidence}]}
    item.update(extra)
    return item


def question(options=("ATP e NADPH", "Somente água", "Glicose", "Oxigênio"), correct_index=0, **extra):
    item = {"tipo": "question", "stem": "O que o ciclo de Calvin usa para fixar CO2?",
            "options": list(options), "correct_index": correct_index,
            "explanation": "O ciclo usa ATP e NADPH da fase clara.", "bloom_level": "compreender",
            "citations": [{"chunk_ref": "C2", "evidence": "usando ATP e NADPH"}]}
    item.update(extra)
    return item


def run(context, *itens, **kw):
    return validate_items(RawGenerationOutput(itens=list(itens)), context, **kw)


def motivo(result, index):
    verdict = result.verdicts[index]
    assert isinstance(verdict, DeterministicVerdict) and verdict.item_index == index
    return verdict.motivo


# E1 — RAG-ENT-001

def test_RAG_ENT_001_remove_invisiveis_e_normaliza_nfkc():
    resultado = normalize_input("ba​ra‍lho﻿ do ﬁm ‮cap. ３", max_chars=100)
    assert resultado.motivo_recusa is None
    assert resultado.text == "baralho do fim cap. 3"


def test_RAG_ENT_001_preserva_quebras_de_linha_e_remove_controles():
    assert normalize_input("linha 1\nlinha\x00 2\t!", max_chars=100).text == "linha 1\nlinha 2\t!"


def test_RAG_ENT_001_tamanho_excedido_recusa():
    resultado = normalize_input("a" * 8001, max_chars=8000)
    assert resultado.motivo_recusa is MotivoRecusa.TAMANHO_EXCEDIDO
    assert resultado.text == ""


def test_RAG_ENT_001_limite_medido_apos_remover_invisiveis():
    assert normalize_input("a" * 8000 + "​" * 5, max_chars=8000).motivo_recusa is None


def test_RAG_ENT_001_sql_e_shell_nao_sao_filtrados():
    texto = "flashcards sobre SELECT * FROM t; e rm -rf no Linux"
    assert normalize_input(texto, max_chars=8000).text == texto


# E9 — RAG-GEN-002, RAG-VAL-001, RAG-VAL-002

def test_normalizacao_da_evidencia_ignora_espacos_caixa_e_pontuacao():
    assert normalize_evidence("  A LUZ,   é absorvida!! ") == "a luz é absorvida"


def test_RAG_VAL_001_item_valido_aceito_com_evidencia_normalizada(context):
    resultado = run(context, card(evidence="A  LUZ é absorvida, pela clorofila"))
    assert resultado.verdicts == [DeterministicVerdict(item_index=0, aprovado=True)]
    assert isinstance(resultado.accepted[0], Flashcard)


def test_RAG_GEN_002_sem_citacao(context):
    assert motivo(run(context, card(citations=[])), 0) is MotivoRejeicao.SEM_CITACAO
    sem_chave = card()
    del sem_chave["citations"]
    assert motivo(run(context, sem_chave), 0) is MotivoRejeicao.SEM_CITACAO


def test_RAG_VAL_001_citacao_inexistente(context):
    assert motivo(run(context, card(ref="C9")), 0) is MotivoRejeicao.CITACAO_INEXISTENTE


def test_RAG_VAL_001_evidencia_nao_encontrada(context):
    resultado = run(context, card(evidence="a mitocôndria produz ATP"))
    assert motivo(resultado, 0) is MotivoRejeicao.EVIDENCIA_NAO_ENCONTRADA


def test_RAG_VAL_001_evidencia_de_outro_chunk_nao_vale(context):
    assert motivo(run(context, card(ref="C2")), 0) is MotivoRejeicao.EVIDENCIA_NAO_ENCONTRADA


def test_RAG_VAL_001_evidencia_so_pontuacao_nao_encontrada(context):
    assert motivo(run(context, card(evidence="...")), 0) is MotivoRejeicao.EVIDENCIA_NAO_ENCONTRADA


def test_RAG_VAL_001_todas_as_citacoes_sao_conferidas(context):
    item = card()
    item["citations"].append({"chunk_ref": "C2", "evidence": "texto ausente"})
    assert motivo(run(context, item), 0) is MotivoRejeicao.EVIDENCIA_NAO_ENCONTRADA


def test_RAG_VAL_002_correct_index_fora_do_intervalo(context):
    assert motivo(run(context, question(correct_index=4)), 0) is MotivoRejeicao.QUESTAO_MAL_FORMADA


@pytest.mark.parametrize("options", [("ATP e NADPH",), ("ATP e NADPH", " atp  e nadph ", "Glicose")])
def test_RAG_VAL_002_menos_de_duas_ou_repetidas(context, options):
    assert motivo(run(context, question(options=options)), 0) is MotivoRejeicao.QUESTAO_MAL_FORMADA


@pytest.mark.parametrize("item", [card(front=" "), card(back=""), card(front="A luz.", back="a luz"),
                                  card(front="?!", back="..."), question(explanation="   ")])
def test_RAG_VAL_002_vazio_ou_trivial(context, item):
    assert motivo(run(context, item), 0) is MotivoRejeicao.VAZIO_OU_TRIVIAL


def test_RAG_VAL_002_quase_identico_na_mesma_resposta(context):
    resultado = run(context, card(), card(front="O que a clorofila absorve na fase clara ?", back="A luz"))
    assert resultado.verdicts[0].aprovado
    assert motivo(resultado, 1) is MotivoRejeicao.DUPLICATA
    assert list(resultado.accepted) == [0]


def test_RAG_VAL_002_duplicata_de_itens_anteriores(context):
    anterior = Flashcard.model_validate(card())
    assert motivo(run(context, card(), prior=[anterior]), 0) is MotivoRejeicao.DUPLICATA


def test_RAG_VAL_002_itens_distintos_nao_sao_duplicata(context):
    resultado = run(context, card(), card(front="O que é liberado quando a água é quebrada?", back="Oxigênio.",
                                          evidence="a água é quebrada, liberando oxigênio"))
    assert all(v.aprovado for v in resultado.verdicts)


def test_violacao_de_schema_e_tipo_diferente_do_pedido(context):
    resultado = run(context, {"tipo": "resumo", "texto": "x"}, question(), card(), expected_type="flashcard")
    assert motivo(resultado, 0) is MotivoRejeicao.VIOLACAO_DE_SCHEMA
    assert motivo(resultado, 1) is MotivoRejeicao.VIOLACAO_DE_SCHEMA
    assert resultado.verdicts[2].aprovado


def test_item_invalido_nao_derruba_o_lote(context):
    resultado = run(context, card(ref="C9"), {"lixo": True}, question())
    assert [v.aprovado for v in resultado.verdicts] == [False, False, True]
    assert list(resultado.accepted) == [2] and isinstance(resultado.accepted[2], Question)
    assert resultado.rejected_indices == [0, 1]


# E11 — RAG-SAI-001

def test_RAG_SAI_001_script_escapado_e_proveniencia_preservada():
    original = Flashcard.model_validate(card(back="<script>alert(1)</script> & luz",
                                             evidence="a <b>luz</b> é absorvida"))
    saida = sanitize_item(original)
    assert saida.original is original
    assert saida.delivered.back == "&lt;script&gt;alert(1)&lt;/script&gt; &amp; luz"
    assert saida.delivered.citations[0].evidence == "a &lt;b&gt;luz&lt;/b&gt; é absorvida"
    # Proveniência: evidência literal pré-escape continua igual à fonte para a linhagem.
    assert saida.original.citations[0].evidence == "a <b>luz</b> é absorvida"
    assert saida.original.citations[0].chunk_ref == saida.delivered.citations[0].chunk_ref


def test_RAG_SAI_001_questao_escapada_continua_valida():
    original = Question.model_validate(question(options=("<i>ATP</i>", "\"água\"", "Glicose", "O2")))
    saida = sanitize_item(original)
    assert saida.delivered.options[:2] == ["&lt;i&gt;ATP&lt;/i&gt;", "&quot;água&quot;"]
    assert saida.delivered.correct_index == 0


def test_RAG_SAI_001_saida_que_viola_schema_apos_sanitizar_e_omitida():
    original = Flashcard.model_validate(card(front="‮\x07"))
    with pytest.raises(SaidaInvalida) as erro:
        sanitize_item(original)
    assert erro.value.motivo is MotivoRejeicao.VIOLACAO_DE_SCHEMA

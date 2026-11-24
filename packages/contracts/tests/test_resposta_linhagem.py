"""Revisão T-001, achados 3, 4 e 5 (rodada 1) e ressalvas 1 e 2 (rodada 2) — resposta com fontes,
linhagem e invariantes por status.

OBS-LIN-001, RAG-JUI-003, RAG-CLS-001 (arquitetura §2 E1/E2/E12, §7).
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from training_contracts import (
    ChatRequest,
    ChatResponse,
    ChatTurnRecord,
    ChunkRecord,
    DeterministicVerdict,
    InputVerdict,
    ItemLineage,
    ItemRecord,
    OutputJudgeResult,
    VerdictRecord,
)

AGORA = datetime(2026, 11, 24, tzinfo=UTC)
FLASHCARD = {
    "tipo": "flashcard",
    "front": "Onde ocorre a fase clara?",
    "back": "Nos tilacoides.",
    "bloom_level": "lembrar",
    "citations": [
        {"chunk_ref": "C1", "evidence": "fase clara nos tilacoides"},
        {"chunk_ref": "C3", "evidence": "membrana do tilacoide"},
    ],
}


def fonte(ref):
    return {
        "chunk_ref": ref,
        "chunk_id": f"chunk-{ref}",
        "document_id": "doc-1",
        "document_version_id": "doc-1-v2",
        "page": 12,
        "section_path": ["2 Fotossíntese", "2.3 Fase clara"],
    }


def resposta(**campos):
    base = {
        "status": "ok",
        "itens": [{"item_id": "item-1", "conteudo": FLASHCARD}],
        "fontes": [fonte("C1"), fonte("C3")],
    }
    return ChatResponse.model_validate({**base, **campos})


# Achado 4: resposta com fontes resolvidas (E12)


def test_obs_lin_001_resposta_ok_traz_fontes_resolvidas():
    r = resposta()

    assert {f.chunk_ref: f.document_version_id for f in r.fontes} == {"C1": "doc-1-v2", "C3": "doc-1-v2"}
    assert r.fontes[0].page == 12
    assert r.fontes[0].section_path == ["2 Fotossíntese", "2.3 Fase clara"]


@pytest.mark.parametrize(
    "fontes",
    [[fonte("C1")], [fonte("C1"), fonte("C3"), fonte("C4")], [fonte("C1"), fonte("C3"), fonte("C3")]],
    ids=["faltando", "sobrando", "repetida"],
)
def test_obs_lin_001_fontes_diferentes_das_citadas_sao_rejeitadas(fontes):
    with pytest.raises(ValidationError):
        resposta(fontes=fontes)


@pytest.mark.parametrize("campo", ["chunk_id", "document_id", "document_version_id"])
def test_obs_lin_001_fonte_sem_resolucao_e_rejeitada(campo):
    incompleta = {k: v for k, v in fonte("C1").items() if k != campo}
    with pytest.raises(ValidationError):
        resposta(fontes=[incompleta, fonte("C3")])


def test_obs_lin_001_status_sem_itens_nao_leva_fontes():
    with pytest.raises(ValidationError):
        ChatResponse.model_validate({"status": "conteudo_insuficiente", "fontes": [fonte("C1")]})


def test_obs_lin_001_item_id_repetido_e_rejeitado():
    item = {"item_id": "item-1", "conteudo": FLASHCARD}
    with pytest.raises(ValidationError):
        resposta(itens=[item, item])


def test_rag_jui_003_resposta_ok_com_omissao_motivada():
    r = resposta(omissoes=[{"item_id": "item-2", "motivo": "reprovado_no_juiz_de_saida"}])

    assert r.omissoes[0].motivo == "reprovado_no_juiz_de_saida"


@pytest.mark.parametrize(
    ("campos", "valido"),
    [
        ({"status": "recusa", "motivo_recusa": "prompt_malicioso"}, True),
        ({"status": "recusa"}, False),
        ({"status": "erro", "motivo_recusa": "limite_de_taxa"}, False),
        ({"status": "fora_de_escopo", "itens": [{"item_id": "i", "conteudo": FLASHCARD}]}, False),
    ],
    ids=["recusa_com_motivo", "recusa_sem_motivo", "motivo_fora_de_recusa", "itens_fora_de_ok"],
)
def test_resposta_coerente_com_status(campos, valido):
    if valido:
        ChatResponse.model_validate(campos)
    else:
        with pytest.raises(ValidationError):
            ChatResponse.model_validate(campos)


@pytest.mark.parametrize("detalhe", ["", "   "], ids=["vazio", "so_espacos"])
def test_rag_jui_003_omissao_rejeita_detalhe_vazio(detalhe):
    with pytest.raises(ValidationError):
        resposta(omissoes=[{"item_id": "item-2", "motivo": "sem_citacao", "detalhe": detalhe}])


def test_rag_jui_003_omissao_aceita_detalhe_ausente_ou_preenchido():
    r = resposta(
        omissoes=[
            {"item_id": "item-2", "motivo": "sem_citacao"},
            {"item_id": "item-3", "motivo": "sem_citacao", "detalhe": "nenhuma citação recebida"},
        ]
    )

    assert [o.detalhe for o in r.omissoes] == [None, "nenhuma citação recebida"]


def test_chat_request_rejeita_mensagem_vazia_e_campo_extra():
    assert ChatRequest(session_id="s", mensagem="oi").mensagem == "oi"
    with pytest.raises(ValidationError):
        ChatRequest(session_id="s", mensagem="  ")
    with pytest.raises(ValidationError):
        ChatRequest.model_validate({"session_id": "s", "mensagem": "oi", "user_id": "outro"})


# Achado 4: consulta de linhagem por item (OBS-LIN-001)


def citacao_resolvida(ref, evidencia):
    return {"citation_id": f"cit-{ref}", **fonte(ref), "evidence": evidencia}


def linhagem(**campos):
    base = {
        "item_id": "item-1",
        "chat_turn_id": "turn-1",
        "trace_id": "trace-1",
        "content": FLASHCARD,
        "citations": [
            citacao_resolvida("C1", "fase clara nos tilacoides"),
            citacao_resolvida("C3", "membrana do tilacoide"),
        ],
    }
    return ItemLineage.model_validate({**base, **campos})


def test_obs_lin_001_linhagem_retorna_documento_versao_pagina_secao_evidencia_trace():
    lin = linhagem()
    c = lin.citations[0]

    assert (c.document_id, c.document_version_id, c.page, c.section_path, c.evidence) == (
        "doc-1",
        "doc-1-v2",
        12,
        ["2 Fotossíntese", "2.3 Fase clara"],
        "fase clara nos tilacoides",
    )
    assert lin.trace_id == "trace-1"


@pytest.mark.parametrize(
    "citacoes",
    [
        [citacao_resolvida("C1", "fase clara nos tilacoides")],
        [citacao_resolvida("C1", "fase clara nos tilacoides"), citacao_resolvida("C3", "outra evidência")],
        [],
    ],
    ids=["faltando", "evidencia_divergente", "vazia"],
)
def test_obs_lin_001_linhagem_divergente_do_conteudo_e_rejeitada(citacoes):
    with pytest.raises(ValidationError):
        linhagem(citations=citacoes)


def test_obs_lin_001_linhagem_exige_trace_id():
    with pytest.raises(ValidationError):
        ItemLineage.model_validate({**linhagem().model_dump(), "trace_id": ""})


# Achado 3: registro de item omitido com conteúdo bruto


def registro(**campos):
    base = {"item_id": "item-1", "chat_turn_id": "turn-1", "raw_content": FLASHCARD}
    return ItemRecord.model_validate({**base, **campos})


@pytest.mark.parametrize("motivo", ["sem_citacao", "questao_mal_formada", "violacao_de_schema", "vazio_ou_trivial"])
def test_rag_jui_003_item_omitido_invalido_guarda_conteudo_bruto(motivo):
    bruto = {"tipo": "question", "stem": "?", "options": ["A", "A"], "correct_index": 7, "citations": []}
    r = registro(raw_content=bruto, status="omitido", motivo=motivo)

    assert r.raw_content == bruto
    assert r.content is None


def test_rag_jui_003_item_entregue_exige_conteudo_valido():
    assert registro(content=FLASHCARD, status="entregue").content.front == FLASHCARD["front"]
    with pytest.raises(ValidationError):
        registro(status="entregue")
    with pytest.raises(ValidationError):
        registro(content={**FLASHCARD, "citations": []}, status="entregue")


@pytest.mark.parametrize("motivo", ["citacao_inexistente", "evidencia_nao_encontrada", "duplicata", "reprovado_no_juiz_de_saida"])
def test_rag_jui_003_motivo_que_exige_item_valido(motivo):
    assert registro(content=FLASHCARD, status="omitido", motivo=motivo).motivo == motivo
    with pytest.raises(ValidationError):
        registro(status="omitido", motivo=motivo)


@pytest.mark.parametrize(("status", "motivo"), [("omitido", None), ("entregue", "duplicata")])
def test_rag_jui_003_motivo_se_e_somente_se_omitido(status, motivo):
    with pytest.raises(ValidationError):
        registro(content=FLASHCARD, status=status, motivo=motivo)


# Achado 5: ChatTurnRecord conforme o status


def turno(**campos):
    base = {
        "chat_turn_id": "turn-1",
        "session_id": "s-1",
        "user_id": "u-1",
        "prompt": "flashcards sobre fotossíntese",
        "status": "ok",
        "intencao": "gerar_flashcards",
        "modo": "consulta",
        "input_verdict": {"malicioso": False, "motivo": "pedido de estudo"},
        "trace_id": "trace-1",
        "created_at": AGORA,
    }
    return ChatTurnRecord.model_validate({**base, **campos})


MALICIOSO = {"malicioso": True, "categoria": "extracao_de_prompt", "motivo": "pede o system prompt"}


@pytest.mark.parametrize(
    "campos",
    [
        {},
        {"status": "conteudo_insuficiente"},
        {"status": "fora_de_escopo", "intencao": "fora_de_escopo", "modo": None},
        {"status": "recusa", "motivo_recusa": "prompt_malicioso", "input_verdict": MALICIOSO},
        {"status": "recusa", "motivo_recusa": "prompt_malicioso", "input_verdict": MALICIOSO, "intencao": None, "modo": None},
        {"status": "recusa", "motivo_recusa": "tamanho_excedido", "input_verdict": None, "intencao": None, "modo": None},
        {"status": "recusa", "motivo_recusa": "limite_de_taxa", "input_verdict": None, "intencao": None, "modo": None},
        {"status": "recusa", "motivo_recusa": "falha_do_juiz_de_entrada", "input_verdict": None},
        {"status": "recusa", "motivo_recusa": "falha_do_juiz_de_entrada", "input_verdict": None, "intencao": None, "modo": None},
        {"status": "erro", "input_verdict": None, "intencao": None, "modo": None},
        {"status": "erro"},
    ],
    ids=[
        "ok",
        "insuficiente",
        "fora_de_escopo",
        "malicioso",
        "malicioso_e3_cancelado",
        "e1_tamanho",
        "e1_limite",
        "falha_e2",
        "falha_e2_e3_cancelado",
        "erro_cedo",
        "erro_tarde",
    ],
)
def test_rag_cls_001_turno_coerente_e_aceito(campos):
    turno(**campos)


@pytest.mark.parametrize(
    "campos",
    [
        {"status": "recusa"},
        {"motivo_recusa": "limite_de_taxa"},
        {"intencao": "fora_de_escopo", "modo": None},
        {"status": "fora_de_escopo"},
        {"modo": None},
        {"status": "fora_de_escopo", "intencao": "fora_de_escopo", "modo": "consulta"},
        {"intencao": None, "modo": None},
        {"input_verdict": None},
        {"input_verdict": MALICIOSO},
        {"status": "recusa", "motivo_recusa": "prompt_malicioso"},
        {"status": "recusa", "motivo_recusa": "limite_de_taxa", "input_verdict": MALICIOSO},
        {"status": "recusa", "motivo_recusa": "tamanho_excedido"},
        {"status": "erro", "input_verdict": MALICIOSO},
        {"status": "recusa", "motivo_recusa": "tamanho_excedido", "input_verdict": None},
        {"status": "recusa", "motivo_recusa": "limite_de_taxa", "input_verdict": None},
        {"status": "recusa", "motivo_recusa": "tamanho_excedido", "input_verdict": None, "modo": None},
        {"status": "recusa", "motivo_recusa": "limite_de_taxa", "input_verdict": None, "intencao": "fora_de_escopo", "modo": None},
    ],
    ids=[
        "recusa_sem_motivo",
        "motivo_fora_de_recusa",
        "ok_sem_geracao",
        "fora_de_escopo_com_intencao_de_geracao",
        "geracao_sem_modo",
        "modo_fora_de_geracao",
        "ok_sem_intencao",
        "ok_sem_e2",
        "ok_malicioso",
        "prompt_malicioso_sem_veredito_malicioso",
        "malicioso_com_outro_motivo",
        "e1_com_veredito_e2",
        "malicioso_sem_recusa",
        "e1_tamanho_com_intencao_e_modo",
        "e1_limite_com_intencao_e_modo",
        "e1_com_intencao",
        "e1_com_intencao_fora_de_escopo",
    ],
)
def test_rag_cls_001_turno_incoerente_e_rejeitado(campos):
    with pytest.raises(ValidationError):
        turno(**campos)


def test_veredito_de_entrada_malicioso_exige_categoria():
    assert InputVerdict.model_validate(MALICIOSO).malicioso
    with pytest.raises(ValidationError):
        InputVerdict.model_validate({**MALICIOSO, "categoria": None})


# Achado 5: VerdictRecord conforme a origem


def veredito(**campos):
    base = {"verdict_id": "v-1", "item_id": "item-1", "origem": "E9", "aprovado": True, "created_at": AGORA}
    return VerdictRecord.model_validate({**base, **campos})


E10_OK = {"origem": "E10", "judge_model": "anthropic/claude-haiku-5.5", "fundamentado": True, "conforme_rubrica": True}


@pytest.mark.parametrize(
    "campos",
    [
        {},
        {"aprovado": False, "motivo": "citacao_inexistente", "detalhe": "C9 fora do contexto"},
        {"aprovado": False, "motivo": "duplicata", "tentativa": 2},
        E10_OK,
        {**E10_OK, "detalhe": "bem fundamentado"},
        {**E10_OK, "fundamentado": False, "aprovado": False, "motivo": "reprovado_no_juiz_de_saida", "detalhe": "C3 não sustenta"},
    ],
    ids=["e9_ok", "e9_reprovado", "e9_reparo", "e10_ok", "e10_ok_com_nota", "e10_reprovado"],
)
def test_rag_jui_003_veredito_coerente_e_aceito(campos):
    veredito(**campos)


@pytest.mark.parametrize(
    "campos",
    [
        {"aprovado": False},
        {"motivo": "duplicata"},
        {"detalhe": "nada"},
        {"aprovado": False, "motivo": "reprovado_no_juiz_de_saida"},
        {"judge_model": "x"},
        {"fundamentado": True},
        {**E10_OK, "judge_model": None},
        {**E10_OK, "conforme_rubrica": None},
        {**E10_OK, "conforme_rubrica": False},
        {**E10_OK, "aprovado": False, "motivo": "reprovado_no_juiz_de_saida", "detalhe": "x"},
        {**E10_OK, "fundamentado": False, "aprovado": False, "motivo": "duplicata", "detalhe": "x"},
        {**E10_OK, "fundamentado": False, "aprovado": False, "motivo": "reprovado_no_juiz_de_saida"},
        {"tentativa": 3},
        {"tentativa": 0},
    ],
    ids=[
        "reprovado_sem_motivo",
        "aprovado_com_motivo",
        "e9_aprovado_com_detalhe",
        "e9_motivo_de_e10",
        "e9_com_judge_model",
        "e9_com_rubrica",
        "e10_sem_judge_model",
        "e10_sem_rubrica",
        "e10_aprovado_com_rubrica_falha",
        "e10_reprovado_com_rubricas_ok",
        "e10_motivo_de_e9",
        "e10_reprovado_sem_detalhe",
        "tentativa_alem_do_reparo",
        "tentativa_zero",
    ],
)
def test_rag_jui_003_veredito_incoerente_e_rejeitado(campos):
    with pytest.raises(ValidationError):
        veredito(**campos)


# Achado 5: DeterministicVerdict (E9)


@pytest.mark.parametrize(
    ("campos", "valido"),
    [
        ({"aprovado": True}, True),
        ({"aprovado": False, "motivo": "citacao_inexistente", "detalhe": "C9"}, True),
        ({"aprovado": False, "motivo": "violacao_de_schema"}, True),
        ({"aprovado": False}, False),
        ({"aprovado": True, "motivo": "duplicata"}, False),
        ({"aprovado": True, "detalhe": "ok"}, False),
        ({"aprovado": False, "motivo": "reprovado_no_juiz_de_saida"}, False),
        ({"aprovado": False, "motivo": "duplicata", "detalhe": "  "}, False),
    ],
    ids=["ok", "reprovado", "schema", "sem_motivo", "aprovado_com_motivo", "aprovado_com_detalhe", "motivo_de_e10", "detalhe_vazio"],
)
def test_rag_val_001_veredito_deterministico(campos, valido):
    dados = {"item_index": 0, **campos}
    if valido:
        DeterministicVerdict.model_validate(dados)
    else:
        with pytest.raises(ValidationError):
            DeterministicVerdict.model_validate(dados)


# E10 e chunk (fora do G3)


def test_rag_jui_001_juiz_de_saida_um_veredito_por_item():
    ok = {"item_index": 0, "fundamentado": True, "conforme_rubrica": True}
    resultado = OutputJudgeResult.model_validate({"vereditos": [ok, {**ok, "item_index": 1, "fundamentado": False, "motivo": "sem apoio"}]})

    assert [v.aprovado for v in resultado.vereditos] == [True, False]
    with pytest.raises(ValidationError):
        OutputJudgeResult.model_validate({"vereditos": [ok, ok]})
    with pytest.raises(ValidationError):
        OutputJudgeResult.model_validate({"vereditos": [{**ok, "conforme_rubrica": False}]})


def test_chunk_record_imutavel_e_coerente():
    dados = {
        "chunk_id": "c-1",
        "document_version_id": "dv-1",
        "chunker_version": "v1",
        "start_offset": 0,
        "end_offset": 10,
        "text": "texto",
    }
    c = ChunkRecord.model_validate(dados)
    with pytest.raises(ValidationError):
        c.text = "outro"
    with pytest.raises(ValidationError):
        ChunkRecord.model_validate({**dados, "end_offset": 0})
    with pytest.raises(ValidationError):
        ChunkRecord.model_validate({**dados, "injection_flagged": True})

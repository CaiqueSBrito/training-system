"""Testes do runner de geração real e calibração inicial do juiz de saída (T-022).

Escritos antes de `evals/runner_geracao.py` (teste primeiro); a correção da revisão OpenAI
(F1–F6) acrescentou casos antes do código corrigido. Dublês ficam só neste arquivo e só nas
fronteiras externas: leitor da linhagem Postgres, transporte de rede LiteLLM (a cadeia real
RAGAS → Instructor roda por cima dele), métrica RAGAS inteira em testes de score e serviço do
juiz de saída (OpenRouter). Contratos, dataset, prompts versionados, `render_item` e
`normalize_evidence` do produto executam de verdade. Nada aqui é resposta de geração: os
artefatos de teste exercitam só a validação do contrato e nunca entram em relatório real.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from hashlib import sha256
from pathlib import Path
from typing import Any

import litellm
import pytest
from training_contracts import ChatResponse, validar_item
from training_contracts.chat import DeliveredItem, Source
from training_contracts.linhagem import ChunkRecord, CitationLineage, DocumentVersionRecord, ItemLineage
from training_contracts.rendering import render_item
from training_contracts.vereditos import OutputJudgeResult, OutputJudgeVerdict
from training_generation import load_prompts
from training_generation.agents import GenerationError

from evals.runner_geracao import (
    ARTIFACT_SCHEMA,
    DEFAULT_DATASET_PATH,
    EvalBlockedError,
    EvalConfigError,
    EvalJudgeConfig,
    GenerationEvalArtifact,
    CallBudget,
    VerifiedArtifact,
    build_arg_parser,
    build_faithfulness_metric,
    capture_artifact,
    check_rubric_binding,
    classify_eval_exception,
    compare_faithfulness_reports,
    faithfulness_inputs,
    load_generation_dataset,
    load_verified_artifact,
    main,
    redacted_sdk_logging,
    run_faithfulness_evaluation,
    run_output_judge_calibration,
)

OWNER = "owner-eval-t022"
SESSION = "sessao-eval-t022"
VERSION = "dv-agua-1"
TURN = "turn-t022-1"
TRACE = "0" * 31 + "1"
CHUNK_A = "chunk-agua-1"
CHUNK_B = "chunk-agua-2"
CHUNK_TEXT_A = (
    "A evaporação transforma a água líquida de oceanos, rios e lagos em vapor de água, usando a "
    "energia da radiação solar. A transpiração é a liberação de vapor de água pelas folhas das plantas."
)
CHUNK_TEXT_B = "Parte da água precipitada infiltra-se no solo e recarrega os aquíferos."
DOC_SHA = "a" * 64
SENTINELA = "SENTINELA-PRIVADA-T022-7f3a"


# ---------------------------------------------------------------- dataset


def test_dataset_carrega_com_hashes_rotulos_tecnicos_e_sem_rotulo_humano():
    spec, digest = load_generation_dataset(DEFAULT_DATASET_PATH)
    assert len(digest) == 64
    assert spec.label_source.human_labels is False
    assert spec.label_source.kind == "rotulagem_tecnica_implementador"
    ids = [c.case_id for c in spec.judge_cases]
    assert len(ids) == len(set(ids)) >= 12
    positivos = [c for c in spec.judge_cases if c.expected.aprovado]
    negativos = [c for c in spec.judge_cases if not c.expected.aprovado]
    assert len(positivos) >= 5 and len(negativos) >= 8
    # Controles acadêmicos de R3 e R4 existem e devem aprovar.
    assert {"JUI-005", "JUI-006"} <= {c.case_id for c in positivos}
    criterios = {k for c in negativos for k in c.expected.criterios_esperados}
    assert {"R1", "R2", "R3", "R4"} <= criterios
    for caso in spec.judge_cases:
        item = validar_item(caso.item)
        texto = caso.context[0].text
        for citacao in item.citations:
            assert citacao.evidence in texto


def test_dataset_rejeita_hash_de_documento_divergente(tmp_path):
    destino = _copiar_dataset(tmp_path)
    data = json.loads(destino.read_text(encoding="utf-8"))
    data["documents"][0]["sha256"] = "0" * 64
    destino.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(EvalConfigError, match="sha256"):
        load_generation_dataset(destino)


def test_dataset_rejeita_contexto_que_nao_existe_literalmente_na_fonte(tmp_path):
    destino = _copiar_dataset(tmp_path)
    data = json.loads(destino.read_text(encoding="utf-8"))
    data["judge_cases"][0]["context"][0]["text"] = "Texto parafraseado que não está no documento."
    destino.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(EvalConfigError, match="literal"):
        load_generation_dataset(destino)


def test_dataset_rejeita_evidencia_ausente_do_contexto(tmp_path):
    destino = _copiar_dataset(tmp_path)
    data = json.loads(destino.read_text(encoding="utf-8"))
    data["judge_cases"][0]["item"]["citations"][0]["evidence"] = "frase inexistente no trecho"
    destino.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(EvalConfigError, match="evidência"):
        load_generation_dataset(destino)


@pytest.mark.parametrize(
    "expected",
    [
        {"aprovado": True, "fundamentado": True, "conforme_rubrica": True, "criterios_esperados": ["R1"]},
        {"aprovado": False, "fundamentado": True, "conforme_rubrica": True, "criterios_esperados": ["R2"]},
        {"aprovado": False, "fundamentado": None, "conforme_rubrica": None, "criterios_esperados": []},
        {"aprovado": True, "fundamentado": None, "conforme_rubrica": True, "criterios_esperados": []},
        {"aprovado": False, "fundamentado": False, "conforme_rubrica": None, "criterios_esperados": ["R9"]},
    ],
)
def test_dataset_rejeita_rotulo_incoerente(tmp_path, expected):
    destino = _copiar_dataset(tmp_path)
    data = json.loads(destino.read_text(encoding="utf-8"))
    data["judge_cases"][0]["expected"] = expected
    destino.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(EvalConfigError):
        load_generation_dataset(destino)


def test_dataset_nao_aceita_declarar_rotulo_humano(tmp_path):
    destino = _copiar_dataset(tmp_path)
    data = json.loads(destino.read_text(encoding="utf-8"))
    data["label_source"]["human_labels"] = True
    destino.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(EvalConfigError, match="humano"):
        load_generation_dataset(destino)


def test_vinculo_da_rubrica_confere_com_prompts_versionados_atuais():
    spec, _ = load_generation_dataset(DEFAULT_DATASET_PATH)
    atuais = {nome: a.reference for nome, a in load_prompts().items()}
    check_rubric_binding(spec, atuais)
    alterado = dict(atuais, output_rubric="offline:chat-output-rubric-v2@sha256:" + "b" * 64)
    with pytest.raises(EvalBlockedError, match="rubrica"):
        check_rubric_binding(spec, alterado)


# ---------------------------------------------------------------- artefato e captura


def _item_flashcard() -> dict[str, Any]:
    return {
        "tipo": "flashcard",
        "front": "O que é a transpiração?",
        "back": "É a liberação de vapor de água pelas folhas das plantas <b>.",
        "bloom_level": "lembrar",
        "citations": [{"chunk_ref": "C1", "evidence": "A transpiração é a liberação de vapor de água pelas folhas"}],
    }


def _item_questao() -> dict[str, Any]:
    return {
        "tipo": "question",
        "stem": "O que recarrega os aquíferos?",
        "options": ["A água que se infiltra no solo", "A evaporação dos oceanos"],
        "correct_index": 0,
        "explanation": "Parte da água precipitada infiltra-se no solo e recarrega os aquíferos.",
        "bloom_level": "lembrar",
        "citations": [{"chunk_ref": "C2", "evidence": "infiltra-se no solo e recarrega os aquíferos"}],
    }


def _contexto(texto_a: str = CHUNK_TEXT_A) -> dict[str, Any]:
    return {
        "chunks": [
            {"ref": "C1", "chunk_id": CHUNK_A, "page": None, "section_path": ["Ciclo"], "text": texto_a},
            {"ref": "C2", "chunk_id": CHUNK_B, "page": None, "section_path": ["Ciclo"], "text": CHUNK_TEXT_B},
            {"ref": "C3", "chunk_id": "chunk-nao-citado", "page": None, "section_path": ["Ciclo"],
             "text": "Trecho enviado ao E8 que nenhum item cita."},
        ]
    }


def _arquivos(tmp_path: Path, *, original=None, entregue=None, diag_extra=None, contexto=None, captura_extra=None):
    originais = original if original is not None else [_item_flashcard(), _item_questao()]
    itens = [validar_item(o) for o in originais]
    entregues = entregue if entregue is not None else [render_item(i) for i in itens]
    resposta = ChatResponse(
        status="ok",
        mensagem="",
        itens=[DeliveredItem(item_id=f"item-{n}", conteudo=e) for n, e in enumerate(entregues)],
        fontes=[
            Source(chunk_ref="C1", chunk_id=CHUNK_A, document_id="doc-agua", document_version_id=VERSION),
            Source(chunk_ref="C2", chunk_id=CHUNK_B, document_id="doc-agua", document_version_id=VERSION),
        ],
        chat_turn_id=TURN,
        trace_id=TRACE,
    )
    diag = {
        "chat_turn_id": TURN,
        "trace_id": TRACE,
        "durations_seconds": {"E1": 0.01, "E2": 0.8, "E8": 2.5, "E10": 1.2},
        "models": {"E2": "gemini-3.1-flash-lite", "E8": "gemini-3.1-flash-lite",
                   "E10": "openrouter/anthropic/claude-haiku-5.5"},
        "prompt_versions": {nome: a.reference for nome, a in load_prompts().items()},
        "status": "ok",
        "errors": [],
        "model_calls_admitted": 5,
        "embedding_key": "gemini-embedding-2@3072@fmt-v1",
        "reranker": {"model": "BAAI/bge-reranker-v2-m3", "revision": "r1", "cutoff": 0.1,
                     "manifest_sha256": "c" * 64},
    }
    diag.update(diag_extra or {})
    ctx = contexto if contexto is not None else _contexto()
    captura = {
        "chat_turn_id": TURN,
        "contexts": [{"call_id": "call-1", "call": "inicial", "context": ctx, "status": "completed",
                      "context_ref_mapping": {c["ref"]: c["ref"] for c in ctx["chunks"]}}],
        "original_items": [{"item_id": f"item-{n}", "content": i.model_dump(mode="json"), "call_id": "call-1",
                            "generation_item_index": n, "attempt": 1} for n, i in enumerate(itens)],
    }
    if captura_extra is not None:
        captura_extra(captura)
    paths = {}
    for nome, payload in (("response.json", resposta.model_dump(mode="json")), ("diagnostics.json", diag),
                          ("e8-capture.json", captura)):
        p = tmp_path / nome
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        paths[nome] = p
    return paths, itens


class LeitorLinhagemDuble:
    """Dublê do leitor Postgres: devolve registros de linhagem construídos no teste."""

    def __init__(self, itens, *, owner=OWNER, chunk_text_a=CHUNK_TEXT_A, conteudo_alterado=None):
        self.itens = itens
        self.owner = owner
        self.chunk_text_a = chunk_text_a
        self.conteudo_alterado = conteudo_alterado

    def item_lineage(self, owner_id, item_id):
        if owner_id != self.owner:
            return None
        indice = int(item_id.rsplit("-", 1)[1])
        item = self.conteudo_alterado or self.itens[indice]
        chunk_id = CHUNK_A if item.citations[0].chunk_ref == "C1" else CHUNK_B
        return ItemLineage(
            item_id=item_id, chat_turn_id=TURN, trace_id=TRACE, content=item,
            citations=[CitationLineage(citation_id=f"cit-{indice}", chunk_ref=c.chunk_ref, chunk_id=chunk_id,
                                       evidence=c.evidence, document_id="doc-agua", document_version_id=VERSION)
                       for c in item.citations])

    def chunk(self, chunk_id):
        textos = {CHUNK_A: self.chunk_text_a, CHUNK_B: CHUNK_TEXT_B,
                  "chunk-nao-citado": "Trecho enviado ao E8 que nenhum item cita."}
        return ChunkRecord(chunk_id=chunk_id, document_version_id=VERSION, chunker_version="v1",
                           start_offset=0, end_offset=len(textos[chunk_id]), text=textos[chunk_id])

    def document_version(self, document_version_id):
        return DocumentVersionRecord(document_version_id=document_version_id, document_id="doc-agua",
                                     owner_id=self.owner, sha256=DOC_SHA, parser_version="p1",
                                     created_at="2026-11-25T00:00:00Z")


def _capturar(paths, leitor, case_id=None):
    return capture_artifact(
        paths["response.json"], paths["diagnostics.json"], paths["e8-capture.json"],
        owner_id=OWNER, session_id=SESSION, document_version_ids=[VERSION], source_dispatch="ctx_c55621c67ed1",
        lineage_reader=leitor, case_id=case_id,
    )


def _manifesto(paths, *, sem=(), **extra):
    """Manifesto no formato real da T-021, com identidade completa e sha256 dos arquivos."""
    manifesto = {"run_id": "t021-real-teste", "owner_id": OWNER, "session_id": SESSION, "chat_turn_id": TURN,
                 "trace_id": TRACE, "case_id": "CASO-EXT-1", "document_version_ids": [VERSION],
                 "source_sha256": DOC_SHA, "response_status": "ok",
                 "files": {nome: sha256(p.read_bytes()).hexdigest() for nome, p in paths.items()}}
    manifesto.update(extra)
    for chave in sem:
        manifesto.pop(chave)
    caminho = paths["response.json"].parent / "manifest.json"
    caminho.write_text(json.dumps(manifesto, ensure_ascii=False), encoding="utf-8")
    return caminho, sha256(caminho.read_bytes()).hexdigest()


def _pedido(message="Gere flashcards sobre o ciclo da água.", count=2):
    return {"message_sha256": sha256(message.encode("utf-8")).hexdigest(), "count": count,
            "document_version_ids": [VERSION], "section_prefix": []}


def _verificado(tmp_path, case_id="CASO-EXT-1", request=None, dataset=None, dataset_sha256=None, **kwargs):
    paths, _ = _arquivos(tmp_path, **kwargs)
    extra = {"case_id": case_id}
    if request is not None:
        extra["request"] = request
    caminho, digest = _manifesto(paths, **extra)
    return load_verified_artifact(caminho, manifest_sha256=digest, source_dispatch="ctx_c55621c67ed1",
                                  dataset=dataset, dataset_sha256=dataset_sha256)


def test_captura_com_linhagem_postgres_gera_artefato_vinculado_por_hashes(tmp_path):
    paths, itens = _arquivos(tmp_path)
    artefato = _capturar(paths, LeitorLinhagemDuble(itens))
    assert isinstance(artefato, GenerationEvalArtifact)
    assert artefato.artifact_schema == ARTIFACT_SCHEMA
    assert artefato.verification.method == "lineage_postgres"
    assert artefato.response_sha256 == sha256(paths["response.json"].read_bytes()).hexdigest()
    assert artefato.diagnostics_sha256 == sha256(paths["diagnostics.json"].read_bytes()).hexdigest()
    assert artefato.e8_capture_sha256 == sha256(paths["e8-capture.json"].read_bytes()).hexdigest()
    assert [d.sha256 for d in artefato.documents] == [DOC_SHA]
    # Roundtrip do contrato sem perder validação.
    GenerationEvalArtifact.model_validate_json(artefato.model_dump_json())


def test_captura_rejeita_conteudo_original_divergente_da_linhagem(tmp_path):
    paths, itens = _arquivos(tmp_path)
    outro = validar_item(dict(_item_flashcard(), back="Outro verso que não foi persistido."))
    with pytest.raises(EvalConfigError, match="linhagem"):
        _capturar(paths, LeitorLinhagemDuble(itens, conteudo_alterado=outro))


def test_captura_rejeita_contexto_e8_que_nao_vem_do_chunk_persistido(tmp_path):
    paths, itens = _arquivos(tmp_path)
    with pytest.raises(EvalConfigError, match="chunk"):
        _capturar(paths, LeitorLinhagemDuble(itens, chunk_text_a="Texto persistido diferente do enviado ao E8."))


def test_captura_rejeita_dono_diferente(tmp_path):
    paths, itens = _arquivos(tmp_path)
    with pytest.raises(EvalConfigError):
        _capturar(paths, LeitorLinhagemDuble(itens, owner="outro-dono"))


def test_captura_rejeita_entrega_que_nao_e_render_do_original(tmp_path):
    original = [_item_flashcard(), _item_questao()]
    entregues = [validar_item(dict(_item_flashcard(), back="Verso trocado")), render_item(validar_item(_item_questao()))]
    paths, itens = _arquivos(tmp_path, original=original, entregue=entregues)
    with pytest.raises(EvalConfigError, match="render_item"):
        _capturar(paths, LeitorLinhagemDuble(itens))


def test_captura_rejeita_evidencia_ausente_do_contexto_e8(tmp_path):
    contexto = _contexto(texto_a="Texto sem a evidência citada pelo flashcard.")
    paths, itens = _arquivos(tmp_path, contexto=contexto)
    leitor = LeitorLinhagemDuble(itens, chunk_text_a="Texto sem a evidência citada pelo flashcard.")
    with pytest.raises(EvalConfigError, match="evidência"):
        _capturar(paths, leitor)


def test_captura_rejeita_turno_divergente_entre_resposta_e_diagnostico(tmp_path):
    paths, itens = _arquivos(tmp_path, diag_extra={"chat_turn_id": "outro-turno"})
    with pytest.raises(EvalConfigError, match="chat_turn_id"):
        _capturar(paths, LeitorLinhagemDuble(itens))


def test_captura_por_manifesto_exige_hashes_identicos(tmp_path):
    paths, itens = _arquivos(tmp_path)
    caminho, _ = _manifesto(paths, sem=("source_sha256",),
                            documents=[{"document_version_id": VERSION, "sha256": DOC_SHA}])
    artefato = capture_artifact(
        paths["response.json"], paths["diagnostics.json"], paths["e8-capture.json"],
        owner_id=OWNER, session_id=SESSION, document_version_ids=[VERSION], source_dispatch="ctx_c55621c67ed1",
        sandbox_manifest=caminho,
    )
    assert artefato.verification.method == "sandbox_manifest_hashes"
    assert artefato.verification.detail["manifest_sha256"] == sha256(caminho.read_bytes()).hexdigest()
    assert artefato.case_id == "CASO-EXT-1"
    manifesto = json.loads(caminho.read_text(encoding="utf-8"))
    manifesto["files"]["response.json"] = "d" * 64
    caminho.write_text(json.dumps(manifesto), encoding="utf-8")
    with pytest.raises(EvalConfigError, match="manifesto"):
        capture_artifact(
            paths["response.json"], paths["diagnostics.json"], paths["e8-capture.json"],
            owner_id=OWNER, session_id=SESSION, document_version_ids=[VERSION], source_dispatch="x",
            sandbox_manifest=caminho,
        )


def test_captura_exige_uma_forma_de_verificacao(tmp_path):
    paths, _ = _arquivos(tmp_path)
    with pytest.raises(EvalConfigError, match="verificação"):
        capture_artifact(paths["response.json"], paths["diagnostics.json"], paths["e8-capture.json"],
                         owner_id=OWNER, session_id=SESSION, document_version_ids=[VERSION], source_dispatch="x")


def _reparo_da_questao(captura):
    """Questão (item-1) veio da chamada de reparo, cujo contexto só tem o chunk B como C1 local."""
    inicial = captura["contexts"][0]
    inicial["context"] = {"chunks": [inicial["context"]["chunks"][0], dict(inicial["context"]["chunks"][2], ref="C2")]}
    inicial["context_ref_mapping"] = {"C1": "C1", "C2": "C3"}
    captura["contexts"].append({
        "call_id": "call-2", "call": "reparo", "status": "completed",
        "context": {"chunks": [{"ref": "C1", "chunk_id": CHUNK_B, "page": None, "section_path": ["Ciclo"],
                                "text": CHUNK_TEXT_B}]},
        "context_ref_mapping": {"C1": "C2"},
    })
    captura["original_items"][1].update(call_id="call-2", attempt=2, generation_item_index=0)


def test_item_de_reparo_usa_so_o_contexto_da_propria_chamada_via_mapeamento(tmp_path):
    paths, itens = _arquivos(tmp_path, captura_extra=_reparo_da_questao)
    artefato = _capturar(paths, LeitorLinhagemDuble(itens))
    flash, questao = faithfulness_inputs(artefato)
    assert (questao.call_id, questao.attempt) == ("call-2", 2)
    assert questao.retrieved_contexts == [CHUNK_TEXT_B]
    assert flash.retrieved_contexts == [CHUNK_TEXT_A]


def test_item_nao_usa_uniao_de_contextos_de_outra_chamada(tmp_path):
    def errado(captura):
        _reparo_da_questao(captura)
        captura["original_items"][1].update(call_id="call-1", attempt=1, generation_item_index=1)

    paths, itens = _arquivos(tmp_path, captura_extra=errado)
    with pytest.raises(EvalConfigError, match="chamada"):
        _capturar(paths, LeitorLinhagemDuble(itens))


@pytest.mark.parametrize("ajuste", [
    lambda c: c["contexts"][0].update(status="failed"),
    lambda c: c["original_items"][0].update(attempt=2),
    lambda c: c["contexts"][0].update(context_ref_mapping={"C1": "C1", "C2": "C1", "C3": "C3"}),
    lambda c: c["original_items"][1].update(generation_item_index=0),
])
def test_captura_rejeita_vinculo_de_chamada_incoerente(tmp_path, ajuste):
    paths, itens = _arquivos(tmp_path, captura_extra=ajuste)
    with pytest.raises(EvalConfigError, match="contrato"):
        _capturar(paths, LeitorLinhagemDuble(itens))


def test_captura_por_manifesto_no_formato_real_da_t021(tmp_path):
    paths, itens = _arquivos(tmp_path)
    manifesto = {"run_id": "t021-real-x", "owner_id": OWNER, "session_id": SESSION, "chat_turn_id": TURN,
                 "trace_id": TRACE, "case_id": "CASO-EXT-1", "document_version_ids": [VERSION],
                 "source_sha256": DOC_SHA,
                 "files": {nome: sha256(p.read_bytes()).hexdigest() for nome, p in paths.items()}}
    caminho = tmp_path / "manifest.json"
    caminho.write_text(json.dumps(manifesto), encoding="utf-8")
    kwargs = dict(owner_id=OWNER, session_id=SESSION, document_version_ids=[VERSION], source_dispatch="x",
                  sandbox_manifest=caminho)
    artefato = capture_artifact(paths["response.json"], paths["diagnostics.json"], paths["e8-capture.json"], **kwargs)
    assert artefato.documents[0].sha256 == DOC_SHA
    assert artefato.verification.detail["manifest_run_id"] == "t021-real-x"
    with pytest.raises(EvalConfigError, match="owner_id"):
        capture_artifact(paths["response.json"], paths["diagnostics.json"], paths["e8-capture.json"],
                         **dict(kwargs, owner_id="outro"))


def test_entradas_faithfulness_seguem_adr_0006_com_conteudo_original_e_so_chunks_citados(tmp_path):
    paths, itens = _arquivos(tmp_path)
    artefato = _capturar(paths, LeitorLinhagemDuble(itens))
    entradas = faithfulness_inputs(artefato)
    assert [e.item_id for e in entradas] == ["item-0", "item-1"]
    flash, questao = entradas
    assert flash.user_input == "O que é a transpiração?"
    # Conteúdo original, pré-escape (render_item escaparia "<b>").
    assert flash.response == "É a liberação de vapor de água pelas folhas das plantas <b>."
    assert flash.retrieved_contexts == [CHUNK_TEXT_A]
    assert questao.user_input == "O que recarrega os aquíferos?"
    assert questao.response == ("A água que se infiltra no solo\n"
                                "Parte da água precipitada infiltra-se no solo e recarrega os aquíferos.")
    assert questao.retrieved_contexts == [CHUNK_TEXT_B]
    todos = [c for e in entradas for c in e.retrieved_contexts]
    assert "Trecho enviado ao E8 que nenhum item cita." not in todos


# ---------------------------------------------------------------- Faithfulness


class MetricaDuble:
    """Dublê da fronteira RAGAS/LLM: devolve valores escolhidos pelo teste e registra chamadas."""

    def __init__(self, valores):
        self.valores = list(valores)
        self.chamadas = []

    async def ascore(self, *, user_input, response, retrieved_contexts):
        self.chamadas.append((user_input, response, tuple(retrieved_contexts)))
        valor = self.valores.pop(0)
        if isinstance(valor, Exception):
            raise valor

        class _R:
            value = valor

        return _R()


def _juiz_ragas():
    return EvalJudgeConfig.from_mapping({"EVAL_JUDGE_MODEL": "openrouter/openai/gpt-6.1-sol",
                                         "OPENROUTER_API_KEY": "sk-segredo-de-teste-nao-vazar"})


def _artefato(tmp_path, **kwargs):
    return _verificado(tmp_path, **kwargs)


def test_faithfulness_registra_scores_latencias_config_e_sem_segredo(tmp_path):
    artefato = _artefato(tmp_path)
    metrica = MetricaDuble([1.0, 0.5])
    relatorio = asyncio.run(run_faithfulness_evaluation([artefato], _juiz_ragas(), metric=metrica, max_calls=4))
    assert [i["status"] for i in relatorio["items"]] == ["scored", "scored"]
    assert [i["score"] for i in relatorio["items"]] == [1.0, 0.5]
    assert relatorio["summary"]["mean"] == pytest.approx(0.75)
    assert relatorio["summary"]["min"] == 0.5
    assert relatorio["thresholds"] is None
    assert relatorio["interpretation"]["historical_comparison"] == "Não se aplica"
    assert relatorio["artifacts"][0]["durations_seconds"]["E8"] == 2.5
    assert relatorio["artifacts"][0]["reranker"]["model"] == "BAAI/bge-reranker-v2-m3"
    assert relatorio["config"]["faithfulness_prompt_sha256"]
    assert len(metrica.chamadas) == 2
    assert "sk-segredo-de-teste-nao-vazar" not in json.dumps(relatorio, ensure_ascii=False)


@pytest.mark.parametrize("ruim", [float("nan"), 1.5, -0.1, True, None])
def test_faithfulness_score_invalido_vira_invalid_output_sem_clamp(tmp_path, ruim):
    artefato = _artefato(tmp_path)
    relatorio = asyncio.run(run_faithfulness_evaluation([artefato], _juiz_ragas(),
                                                        metric=MetricaDuble([ruim, 1.0]), max_calls=4))
    assert relatorio["items"][0]["status"] == "invalid_output"
    assert relatorio["items"][0]["score"] is None
    assert relatorio["summary"]["n_scored"] == 1
    assert relatorio["summary"]["mean"] == 1.0


def test_faithfulness_orcamento_insuficiente_bloqueia_antes_de_chamar(tmp_path):
    artefato = _artefato(tmp_path)
    metrica = MetricaDuble([1.0, 1.0])
    with pytest.raises(EvalBlockedError, match="orçamento"):
        asyncio.run(run_faithfulness_evaluation([artefato], _juiz_ragas(), metric=metrica, max_calls=3))
    assert metrica.chamadas == []


def test_faithfulness_bloqueia_artefato_de_outra_versao_de_prompt(tmp_path):
    prompts = {nome: a.reference for nome, a in load_prompts().items()}
    prompts["generation"] = "offline:chat-generation-v0@sha256:" + "e" * 64
    artefato = _artefato(tmp_path, diag_extra={"prompt_versions": prompts})
    metrica = MetricaDuble([1.0, 1.0])
    with pytest.raises(EvalBlockedError, match="prompt"):
        asyncio.run(run_faithfulness_evaluation([artefato], _juiz_ragas(), metric=metrica, max_calls=4))
    assert metrica.chamadas == []


def test_faithfulness_bloqueia_juiz_de_saida_gemini_no_artefato(tmp_path):
    artefato = _artefato(tmp_path, diag_extra={"models": {"E10": "gemini/gemini-3.1-flash-lite"}})
    with pytest.raises(EvalBlockedError, match="Gemini"):
        asyncio.run(run_faithfulness_evaluation([artefato], _juiz_ragas(), metric=MetricaDuble([]), max_calls=4))


def test_faithfulness_erro_retryable_para_sem_retry_e_marca_restantes(tmp_path):
    class Erro503(Exception):
        status_code = 503

    artefato = _artefato(tmp_path)
    metrica = MetricaDuble([Erro503("detalhe privado do provedor"), 1.0])
    relatorio = asyncio.run(run_faithfulness_evaluation([artefato], _juiz_ragas(), metric=metrica, max_calls=4))
    assert [i["status"] for i in relatorio["items"]] == ["Bloqueado", "Não executado"]
    assert relatorio["items"][0]["provider_status_code"] == 503
    assert len(metrica.chamadas) == 1
    assert "detalhe privado" not in json.dumps(relatorio, ensure_ascii=False)
    assert relatorio["summary"]["complete"] is False


def test_faithfulness_resposta_sem_itens_nao_inventa_score(tmp_path):
    paths, itens = _arquivos(tmp_path)
    resposta = ChatResponse(status="conteudo_insuficiente", mensagem="x", chat_turn_id=TURN, trace_id=TRACE)
    paths["response.json"].write_text(resposta.model_dump_json(), encoding="utf-8")
    diag = json.loads(paths["diagnostics.json"].read_text(encoding="utf-8"))
    diag["status"] = "conteudo_insuficiente"
    paths["diagnostics.json"].write_text(json.dumps(diag), encoding="utf-8")
    captura = json.loads(paths["e8-capture.json"].read_text(encoding="utf-8"))
    captura["original_items"] = []
    paths["e8-capture.json"].write_text(json.dumps(captura), encoding="utf-8")
    caminho, digest = _manifesto(paths, response_status="conteudo_insuficiente")
    artefato = load_verified_artifact(caminho, manifest_sha256=digest, source_dispatch="x")
    relatorio = asyncio.run(run_faithfulness_evaluation([artefato], _juiz_ragas(), metric=MetricaDuble([]), max_calls=0))
    assert relatorio["items"] == []
    assert relatorio["summary"]["mean"] is None
    assert relatorio["artifacts"][0]["response_status"] == "conteudo_insuficiente"


def _relatorio(pasta, scores, **kwargs):
    pasta.mkdir(exist_ok=True)
    return asyncio.run(run_faithfulness_evaluation([_verificado(pasta, **kwargs)], _juiz_ragas(),
                                                   metric=MetricaDuble(scores), max_calls=4))


def test_comparacao_so_com_protocolo_e_configuracao_compativeis(tmp_path):
    reranker = {"model": "BAAI/bge-reranker-v2-m3", "revision": "r2", "cutoff": 0.2, "manifest_sha256": "f" * 64}
    a = _relatorio(tmp_path / "a", [1.0, 0.5], request=_pedido())
    b = _relatorio(tmp_path / "b", [1.0, 1.0], request=_pedido(), diag_extra={"reranker": reranker})
    comparacao = compare_faithfulness_reports(a, b)
    assert comparacao["cases"]["CASO-EXT-1"]["delta_mean"] == pytest.approx(0.25)
    assert comparacao["differences"] == ["reranker"]
    prompts = {nome: x.reference for nome, x in load_prompts().items()}
    c = json.loads(json.dumps(b))
    c["config"]["prompt_versions"] = dict(prompts, generation="offline:outro@sha256:" + "1" * 64)
    with pytest.raises(EvalBlockedError, match="incompatível"):
        compare_faithfulness_reports(a, c)


@pytest.mark.parametrize("pedido_depois", [
    _pedido(count=3),
    _pedido(message="Outro pedido com o mesmo case_id."),
    None,
])
def test_comparacao_bloqueia_pedido_diferente_ou_ausente_com_mesmo_case_id(tmp_path, pedido_depois):
    a = _relatorio(tmp_path / "a", [1.0, 0.5], request=_pedido())
    b = _relatorio(tmp_path / "b", [1.0, 1.0], request=pedido_depois)
    with pytest.raises(EvalBlockedError, match="protocolo|case_protocol"):
        compare_faithfulness_reports(a, b)


def test_comparacao_bloqueia_datasets_diferentes_com_mesmo_caso_e_pedido(tmp_path):
    spec, digest = load_generation_dataset(DEFAULT_DATASET_PATH)
    caso = spec.chat_cases[0]
    doc = next(d for d in spec.documents if d.doc_id == caso.doc_id)

    def relatorio(pasta, dataset_sha):
        pasta.mkdir()
        paths, _ = _arquivos(pasta)
        caminho, h = _manifesto(paths, case_id=caso.case_id, source_sha256=doc.sha256,
                                request=_pedido(message=caso.message, count=caso.count))
        v = load_verified_artifact(caminho, manifest_sha256=h, source_dispatch="x", dataset=spec,
                                   dataset_sha256=dataset_sha)
        return asyncio.run(run_faithfulness_evaluation([v], _juiz_ragas(), metric=MetricaDuble([1.0, 1.0]),
                                                       max_calls=4))

    a = relatorio(tmp_path / "a", digest)
    assert a["artifacts"][0]["case_protocol"]["dataset_sha256"] == digest
    b = relatorio(tmp_path / "b", "0" * 64)
    with pytest.raises(EvalBlockedError, match="case_protocol"):
        compare_faithfulness_reports(a, b)


def test_vinculo_ao_dataset_exige_pedido_executado_igual_ao_caso(tmp_path):
    spec, digest = load_generation_dataset(DEFAULT_DATASET_PATH)
    caso = spec.chat_cases[0]
    doc = next(d for d in spec.documents if d.doc_id == caso.doc_id)
    paths, _ = _arquivos(tmp_path)
    for pedido in (_pedido(message=caso.message, count=caso.count + 1), _pedido(message="outro", count=caso.count),
                   None):
        extra = {"request": pedido} if pedido else {}
        caminho, h = _manifesto(paths, case_id=caso.case_id, source_sha256=doc.sha256, **extra)
        with pytest.raises(EvalConfigError, match="pedido"):
            load_verified_artifact(caminho, manifest_sha256=h, source_dispatch="x", dataset=spec,
                                   dataset_sha256=digest)


def test_comparacao_bloqueia_relatorio_sem_case_id(tmp_path):
    a = _relatorio(tmp_path / "a", [1.0, 0.5], request=_pedido())
    b = json.loads(json.dumps(a))
    b["artifacts"][0]["case_id"] = None
    with pytest.raises(EvalBlockedError, match="case_id"):
        compare_faithfulness_reports(a, b)


# ---------------------------------------------------------------- calibração do juiz de saída

DIGEST = load_generation_dataset(DEFAULT_DATASET_PATH)[1]
VEREDITO_OK = json.dumps({"vereditos": [{"item_index": 0, "fundamentado": True, "conforme_rubrica": True,
                                         "motivo": None}]})


@pytest.fixture(autouse=True)
def litellm_sem_rede(monkeypatch):
    """Fronteira de rede do LiteLLM dublada em todo o módulo: nenhum teste chega ao provedor."""
    from types import SimpleNamespace

    rec = SimpleNamespace(calls=[], content=VEREDITO_OK)

    async def acompletion(*args, **kwargs):
        rec.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=rec.content))])

    monkeypatch.setattr(litellm, "acompletion", acompletion)
    return rec


class ServicoJuizDuble:
    """Dublê da fronteira OpenRouter do juiz E10: veredito por caso escolhido pelo teste."""

    def __init__(self, respostas, prompt_versions=None, modelo="openrouter/anthropic/claude-haiku-5.5",
                 politica=None):
        self.respostas = dict(respostas)
        # Como o produto: cada julgamento passa por litellm.acompletion com retentativa zero explícita.
        self.politica = {"num_retries": 0, "max_retries": 0} if politica is None else politica
        self.chamadas = []
        self.prompt_versions = prompt_versions or {n: a.reference for n, a in load_prompts().items()}
        self.modelo = modelo

    def metadata(self):
        class _M:
            pass

        m = _M()
        m.models = {"E10": self.modelo}
        m.prompt_versions = self.prompt_versions
        m.prompt_source = "offline"
        return m

    async def judge(self, items, context):
        assert len(items) == 1 and context.chunks[0].ref == "C1"
        caso = context.chunks[0].chunk_id.split(":")[0]
        self.chamadas.append(caso)
        if self.politica is not False:
            try:
                await litellm.acompletion(model=self.modelo, messages=[], **self.politica)
            except Exception:
                raise GenerationError("provider", stage="E10") from None
        resposta = self.respostas[caso]
        if isinstance(resposta, Exception):
            raise resposta
        fund, conf, motivo = resposta
        return OutputJudgeResult(vereditos=[OutputJudgeVerdict(item_index=0, fundamentado=fund,
                                                               conforme_rubrica=conf, motivo=motivo)])


def _respostas_perfeitas(spec):
    respostas = {}
    for caso in spec.judge_cases:
        e = caso.expected
        if e.aprovado:
            respostas[caso.case_id] = (True, True, None)
        else:
            fund = e.fundamentado if e.fundamentado is not None else True
            conf = e.conforme_rubrica if e.conforme_rubrica is not None else not (fund is False)
            if fund and conf:
                conf = False
            respostas[caso.case_id] = (fund, conf, f"{e.criterios_esperados[0]}: motivo objetivo")
    return respostas


def test_calibracao_mede_concordancia_sem_declarar_calibracao_humana():
    spec, _ = load_generation_dataset(DEFAULT_DATASET_PATH)
    servico = ServicoJuizDuble(_respostas_perfeitas(spec))
    relatorio = asyncio.run(run_output_judge_calibration(spec, servico, max_calls=len(spec.judge_cases), dataset_sha256=DIGEST))
    assert servico.chamadas == [c.case_id for c in spec.judge_cases]
    m = relatorio["metrics"]
    assert m["aprovado"]["accuracy"] == 1.0
    assert m["aprovado"]["false_approval"] == 0 and m["aprovado"]["false_rejection"] == 0
    assert m["criterion_match_rate"] == 1.0
    assert relatorio["interpretation"]["human_calibration"] is False
    assert relatorio["interpretation"]["production_ready"] is False
    assert relatorio["thresholds"] is None
    assert relatorio["binding"]["output_rubric"] == spec.rubric_binding.output_rubric
    assert relatorio["binding"]["dataset_sha256"] == DIGEST
    assert relatorio["protocol"]["calls_made"] == len(spec.judge_cases)
    assert relatorio["summary"]["complete"] is True


def test_calibracao_conta_falsa_aprovacao_e_falsa_rejeicao():
    spec, _ = load_generation_dataset(DEFAULT_DATASET_PATH)
    respostas = _respostas_perfeitas(spec)
    respostas["JUI-013"] = (True, True, None)  # estereótipo aprovado: falsa aprovação
    respostas["JUI-005"] = (True, False, "R3: menciona discriminação")  # controle acadêmico reprovado
    respostas["JUI-007"] = (False, True, "R2: motivo de outro critério")
    relatorio = asyncio.run(run_output_judge_calibration(spec, ServicoJuizDuble(respostas),
                                                         max_calls=len(spec.judge_cases), dataset_sha256=DIGEST))
    m = relatorio["metrics"]["aprovado"]
    assert m["false_approval"] == 1 and m["false_rejection"] == 1
    assert relatorio["metrics"]["aprovado"]["accuracy"] == pytest.approx((len(spec.judge_cases) - 2) / len(spec.judge_cases))
    assert relatorio["metrics"]["criterion_match_rate"] < 1.0
    falhas = {c["case_id"] for c in relatorio["cases"] if not c["agreement"]}
    assert falhas == {"JUI-013", "JUI-005"}


def test_calibracao_para_em_erro_retryable_sem_retry():
    spec, _ = load_generation_dataset(DEFAULT_DATASET_PATH)
    respostas = _respostas_perfeitas(spec)
    segundo = spec.judge_cases[1].case_id
    respostas[segundo] = GenerationError("provider", stage="E10", status_code=429, retryable=True)
    servico = ServicoJuizDuble(respostas)
    relatorio = asyncio.run(run_output_judge_calibration(spec, servico, max_calls=len(spec.judge_cases), dataset_sha256=DIGEST))
    assert servico.chamadas == [spec.judge_cases[0].case_id, segundo]
    estados = [c["status"] for c in relatorio["cases"]]
    assert estados[1] == "Bloqueado" and set(estados[2:]) == {"Não executado"}
    assert relatorio["summary"]["complete"] is False


def test_calibracao_bloqueia_rubrica_divergente_ou_orcamento_antes_de_chamar():
    spec, _ = load_generation_dataset(DEFAULT_DATASET_PATH)
    prompts = {n: a.reference for n, a in load_prompts().items()}
    prompts["output_judge"] = "offline:chat-output-judge-v9@sha256:" + "9" * 64
    servico = ServicoJuizDuble(_respostas_perfeitas(spec), prompt_versions=prompts)
    with pytest.raises(EvalBlockedError, match="rubrica"):
        asyncio.run(run_output_judge_calibration(spec, servico, max_calls=len(spec.judge_cases), dataset_sha256=DIGEST))
    servico = ServicoJuizDuble(_respostas_perfeitas(spec))
    with pytest.raises(EvalBlockedError, match="orçamento"):
        asyncio.run(run_output_judge_calibration(spec, servico, max_calls=len(spec.judge_cases) - 1, dataset_sha256=DIGEST))
    assert servico.chamadas == []
    gemini = ServicoJuizDuble(_respostas_perfeitas(spec), modelo="gemini/gemini-3.1-flash-lite")
    with pytest.raises(EvalBlockedError, match="Gemini"):
        asyncio.run(run_output_judge_calibration(spec, gemini, max_calls=len(spec.judge_cases), dataset_sha256=DIGEST))


# ---------------------------------------------------------------- CLI


def test_cli_exige_confirmacao_explicita_para_chamadas_reais(tmp_path, capsys):
    assert main(["faithfulness", "--manifest", str(tmp_path / "m.json"), "--manifest-sha256", "a" * 64,
                 "--source-dispatch", "x", "--out", str(tmp_path / "r.json")]) == 2
    assert main(["calibrar-juiz", "--out", str(tmp_path / "r.json")]) == 2
    assert not (tmp_path / "r.json").exists()
    assert "--executar-chamadas-reais" in capsys.readouterr().err


def test_cli_validar_dataset_sem_rede(capsys):
    assert main(["validar-dataset"]) == 0
    saida = json.loads(capsys.readouterr().out)
    assert saida["dataset_id"] == "geracao-juiz-faithfulness-v1"
    assert saida["human_labels"] is False
    assert build_arg_parser().prog


# ---------------------------------------------------------------- utilitários


def _copiar_dataset(tmp_path: Path) -> Path:
    origem = DEFAULT_DATASET_PATH.parent
    destino = tmp_path / "geracao"
    (destino / "docs").mkdir(parents=True)
    for arquivo in (origem / "docs").iterdir():
        (destino / "docs" / arquivo.name).write_bytes(arquivo.read_bytes())
    alvo = destino / "dataset.json"
    alvo.write_bytes(DEFAULT_DATASET_PATH.read_bytes())
    return alvo


# ---------------------------------------------------------------- F1/F2: cadeia real RAGAS → Instructor


AFIRMACOES = {"statements": ["A transpiração libera vapor de água pelas folhas."]}
VEREDITOS = {"statements": [{"statement": "A transpiração libera vapor de água pelas folhas.",
                             "reason": "o trecho afirma", "verdict": 1}]}


def _resposta_ferramenta(kwargs, argumentos):
    nome = kwargs["tools"][0]["function"]["name"]
    return litellm.ModelResponse(choices=[{"index": 0, "finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "t1", "type": "function",
                        "function": {"name": nome, "arguments": json.dumps(argumentos)}}]}}])


class TransporteDuble:
    """Dublê só da fronteira de rede LiteLLM: conta cada tentativa e segue um roteiro."""

    def __init__(self, roteiro):
        self.roteiro = list(roteiro)
        self.chamadas = []

    async def __call__(self, *args, **kwargs):
        self.chamadas.append(kwargs)
        passo = self.roteiro.pop(0)
        if isinstance(passo, Exception):
            raise passo
        return _resposta_ferramenta(kwargs, passo)


class ErroHttp(Exception):
    def __init__(self, status):
        super().__init__(f"{SENTINELA} corpo privado do provedor")
        self.status_code = status


@pytest.fixture
def logs_sdk():
    """Handler na raiz e num filho tardio do Instructor com nível explícito e handler próprio."""
    fluxo = io.StringIO()
    handler = logging.StreamHandler(fluxo)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    raiz = logging.getLogger()
    raiz.addHandler(handler)
    tardio = logging.getLogger("instructor.v2.retry.filho_tardio_t022")
    tardio.setLevel(logging.DEBUG)
    tardio.addHandler(handler)
    fabrica = logging.getLogRecordFactory()
    yield fluxo, tardio
    raiz.removeHandler(handler)
    tardio.removeHandler(handler)
    tardio.setLevel(logging.NOTSET)
    assert logging.getLogRecordFactory() is fabrica


def _rodar_real(tmp_path, roteiro, max_calls=4):
    transporte = TransporteDuble(roteiro)
    relatorio = asyncio.run(run_faithfulness_evaluation([_verificado(tmp_path)], _juiz_ragas(), max_calls=max_calls,
                                                        transport=transporte))
    return relatorio, transporte


def _sem_sentinela(relatorio, fluxo, capfd):
    saida = capfd.readouterr()
    for texto in (json.dumps(relatorio, ensure_ascii=False), fluxo.getvalue(), saida.out, saida.err):
        assert SENTINELA not in texto
        assert "Traceback" not in texto


def test_cadeia_real_faz_exatamente_duas_chamadas_por_item_sem_retentativa(tmp_path, logs_sdk, capfd):
    relatorio, transporte = _rodar_real(tmp_path, [AFIRMACOES, VEREDITOS, AFIRMACOES, VEREDITOS])
    assert [i["status"] for i in relatorio["items"]] == ["scored", "scored"]
    assert [i["score"] for i in relatorio["items"]] == [1.0, 1.0]
    assert len(transporte.chamadas) == 4 == relatorio["config"]["calls_made"]
    for chamada in transporte.chamadas:
        assert chamada["num_retries"] == 0 and chamada["max_retries"] == 0 and chamada["no-log"] is True
    _sem_sentinela(relatorio, logs_sdk[0], capfd)


@pytest.mark.parametrize("roteiro,chamadas_item0", [
    ([{"nada": SENTINELA}, AFIRMACOES, VEREDITOS], 1),
    ([AFIRMACOES, {"statements": [{"campo": SENTINELA}]}, AFIRMACOES, VEREDITOS], 2),
])
def test_cadeia_real_payload_invalido_e_uma_tentativa_e_nao_vaza(tmp_path, logs_sdk, capfd, roteiro, chamadas_item0):
    relatorio, transporte = _rodar_real(tmp_path, roteiro)
    assert [i["status"] for i in relatorio["items"]] == ["invalid_output", "scored"]
    assert relatorio["items"][0]["score"] is None
    assert len(transporte.chamadas) == chamadas_item0 + 2 == relatorio["config"]["calls_made"]
    assert transporte.roteiro == []
    _sem_sentinela(relatorio, logs_sdk[0], capfd)


@pytest.mark.parametrize("status,retryable", [(429, True), (503, True), (400, False)])
def test_cadeia_real_erro_http_embrulhado_pelo_instructor_mantem_status(tmp_path, logs_sdk, capfd, status, retryable):
    roteiro = [ErroHttp(status)] + ([] if retryable else [AFIRMACOES, VEREDITOS])
    relatorio, transporte = _rodar_real(tmp_path, roteiro)
    primeiro = relatorio["items"][0]
    assert primeiro["status"] == "Bloqueado"
    assert primeiro["provider_status_code"] == status and primeiro["retryable"] is retryable
    if retryable:
        assert relatorio["items"][1]["status"] == "Não executado"
        assert len(transporte.chamadas) == 1
    else:
        assert relatorio["items"][1]["status"] == "scored"
        assert len(transporte.chamadas) == 3
    assert relatorio["summary"]["complete"] is False
    _sem_sentinela(relatorio, logs_sdk[0], capfd)


def test_controle_de_sensibilidade_sem_correcao_o_instructor_retenta_e_vaza(logs_sdk):
    """Sem max_retries=0 nem redação, a mesma cadeia faz 4 tentativas e o log leva o payload."""
    import instructor
    from ragas.llms import llm_factory
    from ragas.metrics.collections import Faithfulness

    transporte = TransporteDuble([{"nada": SENTINELA}] * 4)
    llm = llm_factory(model="openrouter/openai/gpt-6.1-sol", provider="openrouter",
                      client=instructor.from_litellm(transporte.__call__), adapter="litellm")
    with pytest.raises(Exception):
        asyncio.run(Faithfulness(llm=llm).ascore(user_input="q", response="r", retrieved_contexts=["c"]))
    assert len(transporte.chamadas) == 4
    assert SENTINELA in logs_sdk[0].getvalue()


def test_orcamento_fechado_na_fronteira_do_transporte(tmp_path):
    transporte = TransporteDuble([AFIRMACOES, VEREDITOS])
    metrica = build_faithfulness_metric(_juiz_ragas(), budget=CallBudget(1), transport=transporte)
    with redacted_sdk_logging(), pytest.raises(Exception) as erro:
        asyncio.run(metrica.ascore(user_input="q", response="r", retrieved_contexts=["c"]))
    assert len(transporte.chamadas) == 1
    assert classify_eval_exception(erro.value)["category"] == "budget"


def test_redacao_cobre_filho_tardio_e_last_resort(capfd):
    raiz = logging.getLogger()
    salvos = list(raiz.handlers)
    tardio = logging.getLogger("instructor.v2.criado_depois_t022")
    tardio.setLevel(logging.DEBUG)
    try:
        raiz.handlers = []
        with redacted_sdk_logging():
            try:
                raise ValueError(SENTINELA)
            except ValueError:
                tardio.exception("falha %s", SENTINELA)
            logging.getLogger("litellm.qualquer").error(SENTINELA)
        dentro = capfd.readouterr().err
        tardio.error(SENTINELA)  # controle: fora da redação o lastResort imprime
        fora = capfd.readouterr().err
    finally:
        raiz.handlers = salvos
        tardio.setLevel(logging.NOTSET)
    assert SENTINELA not in dentro and "Traceback" not in dentro
    assert SENTINELA in fora


# ---------------------------------------------------------------- F3/F4: loader verificado e identidade


def test_loader_remonta_das_fontes_e_aceita_armazenado_identico(tmp_path):
    paths, itens = _arquivos(tmp_path)
    caminho, digest = _manifesto(paths)
    armazenado = tmp_path / "artefato.json"
    capturado = capture_artifact(paths["response.json"], paths["diagnostics.json"], paths["e8-capture.json"],
                                 owner_id=OWNER, session_id=SESSION, document_version_ids=[VERSION],
                                 source_dispatch="x", lineage_reader=LeitorLinhagemDuble(itens), case_id="CASO-EXT-1")
    armazenado.write_text(capturado.model_dump_json(), encoding="utf-8")
    v = load_verified_artifact(caminho, manifest_sha256=digest, source_dispatch="x", artifact_path=armazenado)
    assert isinstance(v, VerifiedArtifact)
    assert v.manifest_sha256 == digest
    assert v.stored_artifact_sha256 == sha256(armazenado.read_bytes()).hexdigest()
    assert v.artifact.verification.method == "sandbox_manifest_hashes"


def _armazenado_alterado(tmp_path, alterar):
    paths, _ = _arquivos(tmp_path)
    caminho, digest = _manifesto(paths)
    base = load_verified_artifact(caminho, manifest_sha256=digest, source_dispatch="x").artifact
    dados = json.loads(base.model_dump_json())
    alterar(dados)
    armazenado = tmp_path / "artefato.json"
    armazenado.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    return caminho, digest, armazenado


def _trocar_conteudo_coerente(dados):
    novo = dict(_item_flashcard(), back="Verso adulterado coerente com a entrega.")
    dados["e8"]["original_items"][0]["content"] = novo
    dados["response"]["itens"][0]["conteudo"] = render_item(validar_item(novo)).model_dump(mode="json")


@pytest.mark.parametrize("alterar", [
    lambda d: d.update(owner_id="dono-forjado"),
    lambda d: d.update(session_id="sessao-forjada"),
    _trocar_conteudo_coerente,
    lambda d: d["verification"].update(method="lineage_postgres"),
    lambda d: d.update(response_sha256="e" * 64),
])
def test_loader_rejeita_artefato_armazenado_manipulado_antes_da_rede(tmp_path, alterar):
    caminho, digest, armazenado = _armazenado_alterado(tmp_path, alterar)
    dados = json.loads(armazenado.read_text(encoding="utf-8"))
    if dados["verification"]["method"] == "lineage_postgres":
        # Método declarado não é campo de prova: o remontado continua por manifesto e é aceito.
        v = load_verified_artifact(caminho, manifest_sha256=digest, source_dispatch="x", artifact_path=armazenado)
        assert v.artifact.verification.method == "sandbox_manifest_hashes"
        return
    with pytest.raises(EvalConfigError, match="diverge"):
        load_verified_artifact(caminho, manifest_sha256=digest, source_dispatch="x", artifact_path=armazenado)


def test_loader_rejeita_fonte_alterada_e_manifesto_diferente_do_fixado(tmp_path):
    paths, _ = _arquivos(tmp_path)
    caminho, digest = _manifesto(paths)
    with pytest.raises(EvalBlockedError, match="fixado"):
        load_verified_artifact(caminho, manifest_sha256="b" * 64, source_dispatch="x")
    captura = paths["e8-capture.json"]
    captura.write_text(captura.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(EvalConfigError, match="sha256"):
        load_verified_artifact(caminho, manifest_sha256=digest, source_dispatch="x")


def test_avaliacao_recusa_artefato_nao_reverificado_sem_chamar_rede(tmp_path):
    paths, itens = _arquivos(tmp_path)
    cru = _capturar(paths, LeitorLinhagemDuble(itens))
    transporte = TransporteDuble([AFIRMACOES, VEREDITOS] * 2)
    with pytest.raises(EvalConfigError, match="reverificados"):
        asyncio.run(run_faithfulness_evaluation([cru], _juiz_ragas(), max_calls=4, transport=transporte))
    assert transporte.chamadas == []
    with pytest.raises(EvalConfigError):
        VerifiedArtifact(artifact=cru, manifest_sha256="a" * 64, stored_artifact_sha256=None)


@pytest.mark.parametrize("ausente", ["run_id", "owner_id", "session_id", "chat_turn_id", "trace_id", "case_id",
                                     "document_version_ids"])
def test_manifesto_sem_identidade_completa_e_rejeitado(tmp_path, ausente):
    paths, _ = _arquivos(tmp_path)
    caminho, digest = _manifesto(paths, sem=(ausente,))
    with pytest.raises(EvalConfigError, match="contrato"):
        load_verified_artifact(caminho, manifest_sha256=digest, source_dispatch="x")


@pytest.mark.parametrize("chave", ["owner_id", "session_id", "chat_turn_id", "trace_id"])
def test_manifesto_com_identidade_divergente_e_rejeitado(tmp_path, chave):
    paths, _ = _arquivos(tmp_path)
    caminho, _ = _manifesto(paths, **{chave: "valor-divergente"})
    with pytest.raises(EvalConfigError, match=chave):
        capture_artifact(paths["response.json"], paths["diagnostics.json"], paths["e8-capture.json"],
                         owner_id=OWNER, session_id=SESSION, document_version_ids=[VERSION], source_dispatch="x",
                         sandbox_manifest=caminho)


@pytest.mark.parametrize("arquivos", [
    lambda f: {**{k: v for k, v in f.items() if k != "response.json"}, "./response.json": f["response.json"]},
    lambda f: {**f, "Response.json": f["response.json"]},
    lambda f: {**f, "../response.json": f["response.json"]},
    lambda f: {k: v for k, v in f.items() if k != "diagnostics.json"},
])
def test_manifesto_rejeita_alias_duplicata_travessia_e_arquivo_faltante(tmp_path, arquivos):
    paths, _ = _arquivos(tmp_path)
    caminho, _ = _manifesto(paths)
    manifesto = json.loads(caminho.read_text(encoding="utf-8"))
    manifesto["files"] = arquivos(manifesto["files"])
    caminho.write_text(json.dumps(manifesto), encoding="utf-8")
    with pytest.raises(EvalConfigError, match="contrato"):
        load_verified_artifact(caminho, manifest_sha256=sha256(caminho.read_bytes()).hexdigest(), source_dispatch="x")


# ---------------------------------------------------------------- F6: CLI sem payload/traceback


def _sem_vazamento_cli(capsys):
    saida = capsys.readouterr()
    for texto in (saida.out, saida.err):
        assert SENTINELA not in texto
        assert "Traceback" not in texto
        assert "input_value" not in texto
    return saida


def test_cli_artefato_com_campo_extra_privado_sai_sanitizado(tmp_path, capsys):
    caminho, digest, armazenado = _armazenado_alterado(tmp_path, lambda d: d.update(campo_extra=SENTINELA))
    destino = tmp_path / "r.json"
    codigo = main(["faithfulness", "--manifest", str(caminho), "--manifest-sha256", digest, "--artifact",
                   str(armazenado), "--source-dispatch", "x", "--max-calls", "4", "--executar-chamadas-reais",
                   "--env-file", str(tmp_path / "inexistente.env"), "--out", str(destino)])
    assert codigo == 1
    saida = _sem_vazamento_cli(capsys)
    assert "fora do contrato" in saida.err
    assert not destino.exists()


def test_cli_comparar_json_invalido_e_capturar_payload_invalido_saem_sanitizados(tmp_path, capsys):
    ruim = tmp_path / "ruim.json"
    ruim.write_text("{" + SENTINELA, encoding="utf-8")
    assert main(["comparar", "--antes", str(ruim), "--depois", str(ruim), "--out", str(tmp_path / "c.json")]) == 1
    _sem_vazamento_cli(capsys)
    paths, _ = _arquivos(tmp_path)
    resposta = json.loads(paths["response.json"].read_text(encoding="utf-8"))
    resposta["extra_privado"] = SENTINELA
    paths["response.json"].write_text(json.dumps(resposta), encoding="utf-8")
    caminho, _ = _manifesto(paths)
    codigo = main(["capturar", "--response", str(paths["response.json"]), "--diagnostics",
                   str(paths["diagnostics.json"]), "--e8-capture", str(paths["e8-capture.json"]), "--owner", OWNER,
                   "--session", SESSION, "--version", VERSION, "--source-dispatch", "x", "--manifest", str(caminho),
                   "--out", str(tmp_path / "a.json")])
    assert codigo == 1
    _sem_vazamento_cli(capsys)
    assert not (tmp_path / "a.json").exists()



# ---------------------------------------------------------------- R2/R3 e entradas hostis (reavaliação)


@pytest.mark.parametrize("nome", ["LiteLLM Router", "LiteLLM Proxy", "LiteLLM Proxy.auth", "google.auth",
                                  "google.auth.transport.requests"])
def test_r2_redacao_cobre_namespaces_da_guarda_compartilhada(nome):
    """Prova a redação na própria fábrica de LogRecord (a guarda compartilhada pode ainda filtrar depois)."""
    def criar():
        try:
            raise ValueError(SENTINELA)
        except ValueError as exc:
            erro = (type(exc), exc, exc.__traceback__)
        return logging.getLogRecordFactory()(nome, logging.ERROR, "f.py", 1, "falha %s", (SENTINELA,), erro)

    with redacted_sdk_logging():
        dentro = criar()
    fora = criar()  # controle: fora da redação o registro leva o marcador
    assert SENTINELA not in dentro.getMessage() and dentro.exc_info is None and dentro.args == ()
    assert "suprimido" in dentro.getMessage()
    assert SENTINELA in fora.getMessage() and fora.exc_info is not None


def _relatorio_valido(tmp_path):
    return _relatorio(tmp_path / "base", [1.0, 0.5], request=_pedido())


@pytest.mark.parametrize("estragar", [
    lambda r: {"qualquer": SENTINELA},
    lambda r: dict(r, schema=SENTINELA),
    lambda r: {k: v for k, v in r.items() if k != "config"},
    lambda r: dict(r, items=[dict(r["items"][0], chat_turn_id=SENTINELA)]),
    lambda r: dict(r, items=[dict(r["items"][0], score=None)]),
    lambda r: dict(r, artifacts=[{"case_id": SENTINELA}]),
])
def test_r3_comparar_relatorio_json_valido_fora_do_contrato_e_entrada_invalida(tmp_path, capsys, estragar):
    valido = _relatorio_valido(tmp_path)
    ruim = estragar(json.loads(json.dumps(valido)))
    with pytest.raises(EvalConfigError, match="fora do contrato"):
        compare_faithfulness_reports(valido, ruim)
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(valido), encoding="utf-8")
    b.write_text(json.dumps(ruim), encoding="utf-8")
    destino = tmp_path / "c.json"
    assert main(["comparar", "--antes", str(a), "--depois", str(b), "--out", str(destino)]) == 1
    saida = _sem_vazamento_cli(capsys)
    assert "fora do contrato" in saida.err and "interna" not in saida.err
    assert not destino.exists()


def test_case_id_hostil_no_manifesto_fixado_nao_aparece_no_erro_do_cli(tmp_path, capsys):
    paths, _ = _arquivos(tmp_path)
    caminho, digest = _manifesto(paths, case_id=SENTINELA, request=_pedido())
    destino = tmp_path / "r.json"
    codigo = main(["faithfulness", "--manifest", str(caminho), "--manifest-sha256", digest, "--source-dispatch", "x",
                   "--dataset", str(DEFAULT_DATASET_PATH), "--max-calls", "4", "--executar-chamadas-reais",
                   "--env-file", str(tmp_path / "inexistente.env"), "--out", str(destino)])
    assert codigo != 0
    saida = _sem_vazamento_cli(capsys)
    assert "ausente do dataset" in saida.err
    assert not destino.exists()


@pytest.mark.parametrize("campo", ["pedido", "documento"])
def test_case_id_hostil_nao_aparece_nos_erros_de_vinculo_ao_dataset(tmp_path, campo):
    spec, digest = load_generation_dataset(DEFAULT_DATASET_PATH)
    caso = spec.chat_cases[0]
    doc = next(d for d in spec.documents if d.doc_id == caso.doc_id)
    hostil = spec.model_copy(update={"chat_cases": [caso.model_copy(update={"case_id": SENTINELA})]})
    paths, _ = _arquivos(tmp_path)
    sha_doc = doc.sha256 if campo == "pedido" else "0" * 64
    caminho, h = _manifesto(paths, case_id=SENTINELA, source_sha256=sha_doc,
                            request=_pedido(message="outro", count=caso.count))
    with pytest.raises(EvalConfigError) as erro:
        load_verified_artifact(caminho, manifest_sha256=h, source_dispatch="x", dataset=hostil, dataset_sha256=digest)
    assert SENTINELA not in str(erro.value)


def test_dataset_hostil_nao_aparece_no_erro_do_cli(tmp_path, capsys):
    destino = _copiar_dataset(tmp_path)
    data = json.loads(destino.read_text(encoding="utf-8"))
    data["chat_cases"][0].update(case_id=SENTINELA, doc_id=SENTINELA)
    destino.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert main(["validar-dataset", "--dataset", str(destino)]) == 1
    saida = _sem_vazamento_cli(capsys)
    assert "documento desconhecido" in saida.err


def test_r1_calibracao_registra_politica_de_retentativa_observada_e_nao_afirmacao_fixa():
    spec, _ = load_generation_dataset(DEFAULT_DATASET_PATH)
    relatorio = asyncio.run(run_output_judge_calibration(spec, ServicoJuizDuble(_respostas_perfeitas(spec)),
                                                         max_calls=len(spec.judge_cases), dataset_sha256=DIGEST))
    retries = relatorio["protocol"]["retries"]
    assert retries["eval_runner"] == 0
    assert retries["observed_transport_policies"] == ['{"max_retries": 0, "num_retries": 0}']
    assert "openai_sdk_max_retries" not in retries


# ---------------------------------------------------------------- score estrito e transporte observado


@pytest.mark.parametrize("score", ["0.5", True, False, float("nan"), 1.5, None])
def test_comparar_rejeita_score_nao_numerico_ou_bool_antes_do_calculo(tmp_path, capsys, score):
    valido = _relatorio_valido(tmp_path)
    ruim = json.loads(json.dumps(valido))
    if isinstance(score, float) and score != score:
        ruim["items"][0]["score"] = "NaN"
    else:
        ruim["items"][0]["score"] = score
    with pytest.raises(EvalConfigError, match="fora do contrato"):
        compare_faithfulness_reports(valido, ruim)
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(valido), encoding="utf-8")
    b.write_text(json.dumps(ruim), encoding="utf-8")
    assert main(["comparar", "--antes", str(a), "--depois", str(b), "--out", str(tmp_path / "c.json")]) == 1
    saida = _sem_vazamento_cli(capsys)
    assert "fora do contrato" in saida.err and "interna" not in saida.err


def test_comparar_aceita_score_inteiro_do_contrato_e_normaliza(tmp_path):
    valido = _relatorio_valido(tmp_path)
    inteiro = json.loads(json.dumps(valido))
    inteiro["items"][0]["score"] = 1
    comparacao = compare_faithfulness_reports(valido, inteiro)
    assert comparacao["cases"]["CASO-EXT-1"]["mean_after"] == pytest.approx(0.75)
    assert inteiro["items"][0]["score"] == 1  # a entrada do chamador não é alterada


def test_calibracao_com_servico_real_registra_politica_observada_no_transporte(litellm_sem_rede):
    from training_generation.agents import GenerationService
    from training_generation.settings import GenerationSettings

    settings = GenerationSettings.from_mapping({
        "PROMPT_SOURCE": "offline", "GEMINI_API_KEY": "teste-gemini-nao-usada",
        "OPENROUTER_API_KEY": "teste-openrouter-nao-usada", "GENERATION_MODEL": "gemini-3.1-flash-lite",
        "INPUT_JUDGE_MODEL": "gemini/gemini-3.1-flash-lite", "OUTPUT_JUDGE_MODEL": "openrouter/anthropic/claude-haiku-5.5"})
    spec, _ = load_generation_dataset(DEFAULT_DATASET_PATH)
    relatorio = asyncio.run(run_output_judge_calibration(spec, GenerationService(settings),
                                                         max_calls=len(spec.judge_cases), dataset_sha256=DIGEST))
    n = len(spec.judge_cases)
    assert relatorio["summary"]["n_judged"] == n
    protocolo = relatorio["protocol"]
    assert protocolo["http_calls_admitted"] == protocolo["transport_calls_observed"] == len(litellm_sem_rede.calls) == n
    assert protocolo["retries"]["observed_transport_policies"] == ['{"max_retries": 0, "num_retries": 0}']
    assert "até 2" not in json.dumps(relatorio, ensure_ascii=False)
    for chamada in litellm_sem_rede.calls:
        assert chamada["num_retries"] == 0 and chamada["max_retries"] == 0


@pytest.mark.parametrize("politica", [{"num_retries": 0}, {"num_retries": 0, "max_retries": 2}, {}])
def test_calibracao_recusa_chamada_sem_retentativa_zero_antes_do_transporte(litellm_sem_rede, politica):
    spec, _ = load_generation_dataset(DEFAULT_DATASET_PATH)
    servico = ServicoJuizDuble(_respostas_perfeitas(spec), politica=politica)
    relatorio = asyncio.run(run_output_judge_calibration(spec, servico, max_calls=len(spec.judge_cases),
                                                         dataset_sha256=DIGEST))
    assert litellm_sem_rede.calls == []
    assert relatorio["cases"][0]["status"] == "Bloqueado"
    assert relatorio["cases"][0]["error_kind"] == "retry_policy"
    assert {c["status"] for c in relatorio["cases"][1:]} == {"Não executado"}
    assert relatorio["protocol"]["http_calls_admitted"] == 0


def test_calibracao_fecha_quando_o_julgamento_nao_passa_pelo_transporte_observado(litellm_sem_rede):
    spec, _ = load_generation_dataset(DEFAULT_DATASET_PATH)
    servico = ServicoJuizDuble(_respostas_perfeitas(spec), politica=False)
    relatorio = asyncio.run(run_output_judge_calibration(spec, servico, max_calls=len(spec.judge_cases),
                                                         dataset_sha256=DIGEST))
    assert relatorio["cases"][0]["error_kind"] == "transporte_nao_observado"
    assert relatorio["summary"]["n_judged"] == 0 and relatorio["summary"]["complete"] is False


def test_observacao_restaura_o_transporte_original(litellm_sem_rede):
    original = litellm.acompletion
    spec, _ = load_generation_dataset(DEFAULT_DATASET_PATH)
    asyncio.run(run_output_judge_calibration(spec, ServicoJuizDuble(_respostas_perfeitas(spec)),
                                             max_calls=len(spec.judge_cases), dataset_sha256=DIGEST))
    assert litellm.acompletion is original


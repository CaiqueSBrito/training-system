"""Prompts e rubrica versionados offline, identificados por hash (ADR-0016, SEC-PRM-001)."""

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest

from training_generation.prompts import PROMPT_NAMES, PromptArtifactError, load_prompts

PROMPTS_DIR = Path(__file__).parents[1] / "src" / "training_generation" / "prompts"


def test_todos_os_prompts_carregam_com_hash_dos_bytes():
    prompts = load_prompts()
    assert set(prompts) == set(PROMPT_NAMES)
    for nome, artefato in prompts.items():
        assert artefato.source == "offline"
        assert artefato.sha256 == hashlib.sha256(artefato.text.encode("utf-8")).hexdigest()
        assert artefato.reference == f"offline:{artefato.version}@sha256:{artefato.sha256}"
        assert artefato.text.strip()


def test_hash_divergente_do_manifesto_falha(tmp_path):
    copia = tmp_path / "prompts"
    shutil.copytree(PROMPTS_DIR, copia, ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    alvo = copia / json.loads((copia / "manifest.json").read_text())["output_rubric"]["file"]
    alvo.write_text(alvo.read_text() + "\nCritério extra não versionado.\n")
    with pytest.raises(PromptArtifactError, match="output_rubric"):
        load_prompts(copia)


def test_manifesto_incompleto_falha(tmp_path):
    copia = tmp_path / "prompts"
    shutil.copytree(PROMPTS_DIR, copia, ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    manifesto = json.loads((copia / "manifest.json").read_text())
    del manifesto["classifier"]
    (copia / "manifest.json").write_text(json.dumps(manifesto))
    with pytest.raises(PromptArtifactError, match="classifier"):
        load_prompts(copia)


def test_SEC_PRM_001_prompts_sem_credenciais_nem_variaveis_de_ambiente():
    for artefato in load_prompts().values():
        assert not re.search(r"(API_KEY|SECRET|PASSWORD|sk-[A-Za-z0-9]{8,}|AIza[0-9A-Za-z_-]{10,})", artefato.text)


def test_rubrica_cobre_criterios_aprovados_e_distingue_academico():
    rubrica = load_prompts()["output_rubric"].text.casefold()
    for termo in ("fundamenta", "bloom", "estereótipo", "acadêmic", "instrução", "motivo"):
        assert termo in rubrica


def test_juiz_de_entrada_nao_reprova_estudo_de_sql_ou_seguranca():
    texto = load_prompts()["input_judge"].text.casefold()
    assert "sql" in texto and "system prompt" in texto

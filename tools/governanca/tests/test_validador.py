import hashlib
from pathlib import Path

import yaml

from training_governanca.validador import validar

MUDANCA = "2026-11-25-guardrail-entrada"
APROVACAO = {"por": "humano", "em": "2026-11-25", "palavras": "pode seguir"}


def escrever(caminho: Path, texto: str) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(texto, encoding="utf-8")


def frontmatter(campos: dict, corpo: str = "") -> str:
    return "---\n" + yaml.safe_dump(campos, allow_unicode=True, sort_keys=False) + "---\n" + corpo


def tarefa(**campos) -> dict:
    base = {
        "id": "T-001",
        "mudanca": MUDANCA,
        "titulo": "implementar recusa",
        "requisitos": ["RAG-GRD-002"],
        "papel": "rag-backend",
        "provedor": "openai",
        "revisor": "anthropic",
        "estado": "construir",
        "depende_de": [],
        "permissao": APROVACAO,
        "casos_aprovados": [],
        "orca_dispatch": None,
        "bloqueio": None,
        "evidencias": [],
    }
    base.update(campos)
    return base


def montar(raiz: Path, *tarefas: dict, estado_mudanca: str = "aprovada", delta: str | None = None) -> Path:
    escrever(raiz / "governanca/papeis/rag-backend.md", "# Papel: rag-backend\n")
    escrever(
        raiz / "governanca/specs/chat/spec.md",
        "# Spec — chat\n\n## Requisitos\n\n### RAG-GRD-001 — Normalizar entrada\nO sistema DEVE normalizar.\n",
    )
    pasta = raiz / "governanca/mudancas" / MUDANCA
    escrever(
        pasta / "proposta.md",
        frontmatter({"id": MUDANCA, "titulo": "guardrail", "trilha": "llm", "estado": estado_mudanca, "aprovacao": APROVACAO}),
    )
    escrever(
        pasta / "delta.md",
        delta
        if delta is not None
        else "# Delta\n\n## ADICIONA\n\n### RAG-GRD-002 — Recusa\nQUANDO x, o sistema DEVE y.\n\n## MODIFICA\n\n## REMOVE\n",
    )
    for campos in tarefas:
        escrever(pasta / "tarefas" / f"{campos['id']}.md", frontmatter(campos, "## Objetivo\n"))
    return pasta


def test_governanca_coerente_passa(tmp_path):
    montar(tmp_path, tarefa())
    resultado = validar(tmp_path)
    assert resultado.erros == []
    assert (resultado.mudancas, resultado.tarefas, resultado.requisitos) == (1, 1, 1)


def test_construir_sem_permissao_do_humano_falha(tmp_path):
    montar(tmp_path, tarefa(permissao=None))
    assert any("permissao é obrigatório" in e for e in validar(tmp_path).erros)


def test_permissao_dada_por_modelo_nao_vale(tmp_path):
    montar(tmp_path, tarefa(permissao={**APROVACAO, "por": "orquestrador"}))
    assert any("permissao.por deve ser 'humano'" in e for e in validar(tmp_path).erros)


def test_tarefa_em_construcao_com_proposta_nao_aprovada_falha(tmp_path):
    montar(tmp_path, tarefa(), estado_mudanca="proposta")
    assert any("gate G1" in e for e in validar(tmp_path).erros)


def test_revisor_do_mesmo_provedor_falha(tmp_path):
    montar(tmp_path, tarefa(revisor="openai"))
    assert any("quem constrói não revisa" in e for e in validar(tmp_path).erros)


def test_requisito_inexistente_falha(tmp_path):
    montar(tmp_path, tarefa(requisitos=["RAG-GRD-999"]))
    assert any("RAG-GRD-999 não existe" in e for e in validar(tmp_path).erros)


def test_delta_que_adiciona_requisito_ja_existente_falha(tmp_path):
    montar(tmp_path, tarefa(requisitos=["RAG-GRD-001"]), delta="## ADICIONA\n\n### RAG-GRD-001 — x\n")
    assert any("ADICIONA RAG-GRD-001, mas ele já existe" in e for e in validar(tmp_path).erros)


def test_concluida_vale_a_ultima_evidencia(tmp_path):
    evidencias = [
        {"criterio": "RAG-GRD-002", "metodo": "pytest", "status": "Passou", "ref": "execução 1"},
        {"criterio": "RAG-GRD-002", "metodo": "pytest", "status": "Falhou", "ref": "execução 2"},
    ]
    montar(tmp_path, tarefa(estado="concluida", evidencias=evidencias))
    assert any("última evidência de RAG-GRD-002" in e for e in validar(tmp_path).erros)

    montar(tmp_path, tarefa(estado="concluida", evidencias=list(reversed(evidencias))))
    assert validar(tmp_path).erros == []


def test_caso_aprovado_alterado_depois_do_gate_falha(tmp_path):
    caso = tmp_path / "packages/x/tests/test_recusa.py"
    escrever(caso, "def test_recusa(): ...\n")
    sha = hashlib.sha256(caso.read_bytes()).hexdigest()
    registro = [{"arquivo": "packages/x/tests/test_recusa.py", "sha256": sha, "em": "2026-11-25"}]
    montar(tmp_path, tarefa(casos_aprovados=registro))
    assert validar(tmp_path).erros == []

    escrever(caso, "def test_recusa(): assert True\n")
    assert any("mudou depois da aprovação" in e for e in validar(tmp_path).erros)


def test_ciclo_de_dependencias_falha(tmp_path):
    montar(tmp_path, tarefa(id="T-001", depende_de=["T-002"]), tarefa(id="T-002", depende_de=["T-001"]))
    erros = validar(tmp_path).erros
    assert sum("ciclo de dependências" in e for e in erros) == 1

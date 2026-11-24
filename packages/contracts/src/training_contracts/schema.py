"""Exportação do JSON Schema dos contratos para `packages/contracts/schema/`.

Uso: `uv run exportar-schema-contratos` (regrava os arquivos versionados).
"""

import json
from pathlib import Path

from pydantic import BaseModel

from training_contracts import (
    ChatRequest,
    ChatResponse,
    ChatTurnRecord,
    ChunkRecord,
    CitationRecord,
    Classification,
    DeterministicVerdict,
    DocumentVersionRecord,
    GenerationOutput,
    InputVerdict,
    ItemLineage,
    ItemRecord,
    NumberedContext,
    OutputJudgeResult,
    RawGenerationOutput,
    RetrievalRunRecord,
    SearchPlan,
    SearchPlanRecord,
    VerdictRecord,
)

# Modelos raiz; Flashcard, Question e Citation entram como $defs de quem os usa.
MODELOS: tuple[type[BaseModel], ...] = (
    ChatRequest,
    ChatResponse,
    InputVerdict,
    Classification,
    SearchPlan,
    NumberedContext,
    GenerationOutput,
    RawGenerationOutput,
    DeterministicVerdict,
    OutputJudgeResult,
    DocumentVersionRecord,
    ChunkRecord,
    ChatTurnRecord,
    SearchPlanRecord,
    RetrievalRunRecord,
    ItemRecord,
    CitationRecord,
    VerdictRecord,
    ItemLineage,
)

DIRETORIO = Path(__file__).resolve().parents[2] / "schema"


def nome_do_arquivo(modelo: type[BaseModel]) -> str:
    return f"{modelo.__name__}.schema.json"


def serializar(modelo: type[BaseModel]) -> str:
    return json.dumps(modelo.model_json_schema(), indent=2, ensure_ascii=False) + "\n"


def gerar() -> dict[str, str]:
    """Nome do arquivo → conteúdo do schema, para todos os modelos raiz."""
    return {nome_do_arquivo(m): serializar(m) for m in MODELOS}


def exportar(diretorio: Path = DIRETORIO) -> list[Path]:
    diretorio.mkdir(parents=True, exist_ok=True)
    esperados = gerar()
    for antigo in diretorio.glob("*.schema.json"):
        if antigo.name not in esperados:
            antigo.unlink()
    caminhos = []
    for nome, conteudo in esperados.items():
        caminho = diretorio / nome
        caminho.write_text(conteudo, encoding="utf-8")
        caminhos.append(caminho)
    return caminhos


def main() -> None:
    for caminho in exportar():
        print(caminho)

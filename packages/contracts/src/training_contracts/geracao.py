"""Contexto numerado e itens gerados em E8 (arquitetura §2.2 e §2.3)."""

import re
from typing import Annotated, Any, Literal, Self

from pydantic import Field, JsonValue, TypeAdapter, model_validator

from training_contracts.comum import BloomLevel, ChunkRef, Contrato, Identificador, TextoNaoVazio


class ContextChunk(Contrato):
    """Um chunk do contexto entregue à geração: `[C3] (p. 12, 2.3 Fase clara) …texto…`."""

    ref: ChunkRef
    chunk_id: Identificador
    page: int | None = Field(default=None, ge=1)
    section_path: list[str] = Field(default_factory=list)
    text: TextoNaoVazio


class NumberedContext(Contrato):
    """Contexto numerado de E7 para E8: refs C1..Cn, na ordem e sem lacunas (RAG-GEN-001)."""

    chunks: list[ContextChunk] = Field(min_length=1)

    @model_validator(mode="after")
    def _refs_sequenciais(self) -> Self:
        esperadas = [f"C{i}" for i in range(1, len(self.chunks) + 1)]
        if [c.ref for c in self.chunks] != esperadas:
            raise ValueError("refs do contexto devem ser C1..Cn, em ordem")
        return self

    def chunk(self, ref: str) -> ContextChunk | None:
        """Chunk citado por `ref`, ou None se a referência não está no contexto."""
        return next((c for c in self.chunks if c.ref == ref), None)


class Citation(Contrato):
    """Citação de um item: `chunk_ref` do contexto e evidência literal curta (RAG-GEN-002)."""

    chunk_ref: ChunkRef
    evidence: TextoNaoVazio = Field(description="Trecho literal curto da fonte.")


Citations = Annotated[list[Citation], Field(min_length=1, description="Ao menos uma citação.")]


class _ItemBase(Contrato):
    """Base de `Flashcard` e `Question`.

    `tipo` é obrigatório na validação de dados (dict/JSON, `model_validate`) e no JSON Schema,
    porque é o discriminador da união `Item`. O construtor Python (`Flashcard(...)`) o preenche
    quando omitido, já que o valor é fixo por classe.
    """

    def __init__(self, /, **dados: Any) -> None:
        dados.setdefault("tipo", type(self).model_fields["tipo"].annotation.__args__[0])
        super().__init__(**dados)

    # Marca o __init__ como o do BaseModel: `model_validate`/JSON não passam por ele e
    # continuam exigindo `tipo`, como o schema exportado.
    __init__.__pydantic_base_init__ = True  # type: ignore[attr-defined]


class Flashcard(_ItemBase):
    tipo: Literal["flashcard"]
    front: TextoNaoVazio
    back: TextoNaoVazio
    bloom_level: BloomLevel
    citations: Citations


def _normalizar_alternativa(texto: str) -> str:
    return re.sub(r"\s+", " ", texto).strip().casefold()


class Question(_ItemBase):
    """Questão de múltipla escolha. Invariantes: ≥ 2 alternativas únicas e `correct_index` válido."""

    tipo: Literal["question"]
    stem: TextoNaoVazio
    options: list[TextoNaoVazio] = Field(min_length=2)
    correct_index: int = Field(ge=0)
    explanation: TextoNaoVazio
    bloom_level: BloomLevel
    citations: Citations

    @model_validator(mode="after")
    def _bem_formada(self) -> Self:
        if self.correct_index >= len(self.options):
            raise ValueError("correct_index fora do intervalo das alternativas")
        normalizadas = [_normalizar_alternativa(o) for o in self.options]
        if len(set(normalizadas)) != len(normalizadas):
            raise ValueError("alternativas repetidas")
        return self


Item = Annotated[Flashcard | Question, Field(discriminator="tipo")]
"""Item estrito: o único aceito para entrega (E12) e para `ItemRecord.content`."""

_ITEM = TypeAdapter(Item)

ItemBruto = Annotated[
    dict[str, JsonValue],
    Field(description="Item como recebido da geração, ainda não validado contra `Item`."),
]


def validar_item(bruto: ItemBruto) -> Flashcard | Question:
    """Valida um item bruto contra `Item`; levanta `pydantic.ValidationError` se inválido.

    Usado por E9/E11 item a item, para rejeitar (ou reparar) só o item ruim.
    """
    return _ITEM.validate_python(bruto)


class GenerationOutput(Contrato):
    """Formato pedido ao agente de geração (E8): o `response_schema` da chamada.

    Valida o lote inteiro de uma vez. Para processar a saída recebida item a item, use
    `RawGenerationOutput`.
    """

    itens: list[Item]


class RawGenerationOutput(Contrato):
    """Saída da geração (E8) como recebida: só o envelope é validado.

    Um item inválido não derruba o lote; E9 valida cada item com `validar_item` e
    rejeita ou repara individualmente (RAG-VAL-001, RAG-VAL-002, RAG-JUI-003).
    """

    itens: list[ItemBruto]

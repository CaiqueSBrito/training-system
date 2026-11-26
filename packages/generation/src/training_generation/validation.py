"""Etapas determinísticas: higiene da entrada (E1), validação dos itens (E9) e saída (E11).

Nenhuma função aqui chama LLM. A validação é item a item: um item inválido é rejeitado com
motivo sem derrubar os demais (RAG-VAL-001, RAG-VAL-002). A sanitização gera uma cópia
escapada para entrega e preserva o item original, cuja evidência literal continua igual ao
texto do chunk para a linhagem (RAG-SAI-001).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Literal

from pydantic import ValidationError

from training_contracts.rendering import render_item

from training_contracts import (
    DeterministicVerdict,
    Flashcard,
    MotivoRecusa,
    MotivoRejeicao,
    NumberedContext,
    Question,
    RawGenerationOutput,
    validar_item,
)

ItemType = Literal["flashcard", "question"]
Item = Flashcard | Question

DUPLICATE_RATIO = 0.9
"""Similaridade (difflib) a partir da qual dois itens normalizados são quase idênticos.
Valor inicial, não calibrado; ajustar por avaliação."""

_KEEP_CONTROLS = {"\n", "\t"}
_TEXT_FIELDS = ("front", "back", "stem", "explanation")


# E1 — RAG-ENT-001

@dataclass(frozen=True)
class NormalizedInput:
    """Prompt após a higiene. Com `motivo_recusa`, `text` é vazio e nada segue para LLM."""

    text: str
    motivo_recusa: MotivoRecusa | None = None


def _invisible(ch: str) -> bool:
    categoria = unicodedata.category(ch)
    if categoria == "Cc":
        return ch not in _KEEP_CONTROLS
    return categoria in {"Cf", "Cs"}


def _strip_invisible(texto: str) -> str:
    return "".join(ch for ch in texto if not _invisible(ch))


def normalize_input(text: str, *, max_chars: int) -> NormalizedInput:
    """NFKC, remoção de caracteres invisíveis e de controle, e limite de tamanho.

    Não filtra SQL nem shell (arquitetura §5). O limite vale para o texto normalizado; entradas
    muito maiores que o limite são recusadas antes de normalizar, para limitar o custo.
    """
    if max_chars < 1:
        raise ValueError("max_chars deve ser positivo")
    if len(text) > max_chars * 4 + 1024:
        return NormalizedInput("", MotivoRecusa.TAMANHO_EXCEDIDO)
    texto = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    texto = _strip_invisible(texto)
    if len(texto) > max_chars:
        return NormalizedInput("", MotivoRecusa.TAMANHO_EXCEDIDO)
    return NormalizedInput(texto)


# E9 — RAG-GEN-002, RAG-VAL-001, RAG-VAL-002

def normalize_evidence(text: str) -> str:
    """Normalização da §2.3: Unicode NFKC, caixa, pontuação e espaços."""
    texto = unicodedata.normalize("NFKC", text).casefold()
    texto = "".join(" " if unicodedata.category(ch).startswith("P") or _invisible(ch) else ch for ch in texto)
    return " ".join(texto.split())


def _contains(chunk_text: str, evidence: str) -> bool:
    alvo = normalize_evidence(evidence)
    if not alvo:
        return False
    # Fronteira de palavra: "luz" não casa com "luzes".
    return f" {alvo} " in f" {normalize_evidence(chunk_text)} "


def _option_key(texto: str) -> str:
    # Mesma normalização do contrato `Question` para alternativas repetidas.
    return re.sub(r"\s+", " ", texto).strip().casefold()


@dataclass
class ValidationResult:
    """Um veredito por item bruto, na ordem da saída, e os itens aceitos por índice."""

    verdicts: list[DeterministicVerdict]
    accepted: dict[int, Item] = field(default_factory=dict)

    @property
    def rejected_indices(self) -> list[int]:
        return [v.item_index for v in self.verdicts if not v.aprovado]


class _Reject(Exception):
    def __init__(self, motivo: MotivoRejeicao, detalhe: str):
        self.motivo = motivo
        self.detalhe = detalhe


def _precheck(bruto: Any, expected_type: ItemType | None) -> None:
    if not isinstance(bruto, dict):
        raise _Reject(MotivoRejeicao.VIOLACAO_DE_SCHEMA, "item não é um objeto")
    tipo = bruto.get("tipo")
    if tipo not in ("flashcard", "question"):
        raise _Reject(MotivoRejeicao.VIOLACAO_DE_SCHEMA, "tipo de item desconhecido")
    if expected_type is not None and tipo != expected_type:
        raise _Reject(MotivoRejeicao.VIOLACAO_DE_SCHEMA, f"tipo diferente do pedido ({expected_type})")
    citacoes = bruto.get("citations")
    if citacoes is None or citacoes == []:
        raise _Reject(MotivoRejeicao.SEM_CITACAO, "item sem citação")
    campos = [bruto.get(n) for n in _TEXT_FIELDS if n in bruto]
    opcoes = bruto.get("options")
    if isinstance(opcoes, list):
        campos += opcoes
    if any(isinstance(c, str) and not c.strip() for c in campos):
        raise _Reject(MotivoRejeicao.VAZIO_OU_TRIVIAL, "campo vazio")
    if tipo == "question" and isinstance(opcoes, list):
        if len(opcoes) < 2:
            raise _Reject(MotivoRejeicao.QUESTAO_MAL_FORMADA, "menos de 2 alternativas")
        chaves = [_option_key(o) for o in opcoes if isinstance(o, str)]
        if len(set(chaves)) != len(chaves):
            raise _Reject(MotivoRejeicao.QUESTAO_MAL_FORMADA, "alternativas repetidas")
        indice = bruto.get("correct_index")
        if type(indice) is int and not 0 <= indice < len(opcoes):
            raise _Reject(MotivoRejeicao.QUESTAO_MAL_FORMADA, "correct_index fora do intervalo")


def _check_citations(item: Item, context: NumberedContext) -> None:
    for citacao in item.citations:
        if context.chunk(citacao.chunk_ref) is None:
            raise _Reject(MotivoRejeicao.CITACAO_INEXISTENTE, f"{citacao.chunk_ref} não está no contexto")
    for citacao in item.citations:
        if not _contains(context.chunk(citacao.chunk_ref).text, citacao.evidence):
            raise _Reject(MotivoRejeicao.EVIDENCIA_NAO_ENCONTRADA,
                          f"evidência não aparece em {citacao.chunk_ref}")


def _check_trivial(item: Item) -> None:
    if isinstance(item, Flashcard):
        frente, verso = normalize_evidence(item.front), normalize_evidence(item.back)
        if not frente or not verso:
            raise _Reject(MotivoRejeicao.VAZIO_OU_TRIVIAL, "campo sem conteúdo")
        if frente == verso:
            raise _Reject(MotivoRejeicao.VAZIO_OU_TRIVIAL, "frente igual ao verso")
    else:
        campos = [item.stem, item.explanation, *item.options]
        if any(not normalize_evidence(c) for c in campos):
            raise _Reject(MotivoRejeicao.VAZIO_OU_TRIVIAL, "campo sem conteúdo")


def _similarity_key(item: Item) -> tuple[str, str]:
    if isinstance(item, Flashcard):
        return item.tipo, f"{normalize_evidence(item.front)} | {normalize_evidence(item.back)}"
    return item.tipo, f"{normalize_evidence(item.stem)} | {normalize_evidence(item.options[item.correct_index])}"


def _is_duplicate(item: Item, others: Sequence[Item]) -> bool:
    tipo, chave = _similarity_key(item)
    for outro in others:
        tipo_outro, chave_outra = _similarity_key(outro)
        if tipo == tipo_outro and (chave == chave_outra
                                   or SequenceMatcher(None, chave, chave_outra).ratio() >= DUPLICATE_RATIO):
            return True
    return False


def validate_items(raw: RawGenerationOutput, context: NumberedContext, *,
                   expected_type: ItemType | None = None, prior: Sequence[Item] = ()) -> ValidationResult:
    """E9: valida cada item bruto e devolve um `DeterministicVerdict` por índice.

    `prior` são itens já aceitos (da tentativa anterior ou de turnos anteriores, em `gerar_mais`)
    contra os quais um item quase idêntico é duplicata.
    """
    resultado = ValidationResult(verdicts=[])
    aceitos: list[Item] = list(prior)
    for indice, bruto in enumerate(raw.itens):
        try:
            _precheck(bruto, expected_type)
            try:
                item = validar_item(bruto)
            except ValidationError as exc:
                campos = sorted({".".join(str(p) for p in e["loc"][:1]) for e in exc.errors()} - {""})
                raise _Reject(MotivoRejeicao.VIOLACAO_DE_SCHEMA,
                              "campos inválidos: " + (", ".join(campos) or "item")) from None
            _check_citations(item, context)
            _check_trivial(item)
            if _is_duplicate(item, aceitos):
                raise _Reject(MotivoRejeicao.DUPLICATA, "quase idêntico a outro item")
        except _Reject as rej:
            resultado.verdicts.append(DeterministicVerdict(item_index=indice, aprovado=False,
                                                           motivo=rej.motivo, detalhe=rej.detalhe))
            continue
        aceitos.append(item)
        resultado.accepted[indice] = item
        resultado.verdicts.append(DeterministicVerdict(item_index=indice, aprovado=True))
    return resultado


# E11 — RAG-SAI-001

class SaidaInvalida(ValueError):
    """Item que, após sanitizado, viola o schema; deve ser omitido com o motivo."""

    def __init__(self, motivo: MotivoRejeicao, detalhe: str):
        self.motivo = motivo
        self.detalhe = detalhe
        super().__init__(detalhe)


@dataclass(frozen=True)
class SanitizedItem:
    """`original` vai para a linhagem (evidência literal da fonte); `delivered`, para a resposta."""

    original: Item
    delivered: Item


def sanitize_item(item: Item) -> SanitizedItem:
    """E11: transformação compartilhada com a verificação de entrega E12."""
    try:
        entregue = render_item(item)
    except ValidationError:
        raise SaidaInvalida(MotivoRejeicao.VIOLACAO_DE_SCHEMA, "item inválido após sanitização") from None
    return SanitizedItem(original=item, delivered=entregue)

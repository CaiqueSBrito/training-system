"""Mensagem de chat: entrada e resposta (E1 e E12)."""

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from training_contracts.comum import ChunkRef, Contrato, Identificador, MotivoRejeicao, TextoNaoVazio
from training_contracts.geracao import Item


class ChatStatus(StrEnum):
    OK = "ok"
    RECUSA = "recusa"
    FORA_DE_ESCOPO = "fora_de_escopo"
    CONTEUDO_INSUFICIENTE = "conteudo_insuficiente"
    ERRO = "erro"


class MotivoRecusa(StrEnum):
    """Motivos de recusa de E1 (higiene) e E2 (juiz de entrada, com falha fechada)."""

    TAMANHO_EXCEDIDO = "tamanho_excedido"
    LIMITE_DE_TAXA = "limite_de_taxa"
    PROMPT_MALICIOSO = "prompt_malicioso"
    FALHA_DO_JUIZ_DE_ENTRADA = "falha_do_juiz_de_entrada"


class ChatRequest(Contrato):
    """Mensagem enviada pelo usuário. A identidade do usuário vem da autenticação, não do corpo."""

    session_id: Identificador
    mensagem: TextoNaoVazio


class DeliveredItem(Contrato):
    """Item entregue, com o ID que permite navegar a linhagem (OBS-LIN-001)."""

    item_id: Identificador
    conteudo: Item


class Omission(Contrato):
    """Item omitido da resposta, com o motivo (RAG-JUI-003)."""

    item_id: Identificador | None = None
    motivo: MotivoRejeicao
    detalhe: TextoNaoVazio | None = None


class Source(Contrato):
    """Fonte citada por um item entregue, com o `chunk_ref` local resolvido (E12, OBS-LIN-001)."""

    chunk_ref: ChunkRef
    chunk_id: Identificador
    document_id: Identificador
    document_version_id: Identificador
    page: int | None = Field(default=None, ge=1)
    section_path: list[str] = Field(default_factory=list)


class ChatResponse(Contrato):
    """Resposta do chat (E12: "resposta com fontes").

    Só `ok` leva itens, omissões e fontes; só `recusa` leva `motivo_recusa`. `fontes` traz
    exatamente os `chunk_ref` citados pelos itens entregues, um por ref.
    """

    status: ChatStatus
    mensagem: str = Field(default="", description="Texto exibido ao usuário.")
    itens: list[DeliveredItem] = Field(default_factory=list)
    omissoes: list[Omission] = Field(default_factory=list)
    fontes: list[Source] = Field(default_factory=list)
    motivo_recusa: MotivoRecusa | None = None
    chat_turn_id: Identificador | None = None
    trace_id: Identificador | None = None

    @model_validator(mode="after")
    def _coerente_com_status(self) -> Self:
        if self.status is not ChatStatus.OK and (self.itens or self.omissoes or self.fontes):
            raise ValueError(f"status {self.status.value} não leva itens, omissões nem fontes")
        refs_fontes = [f.chunk_ref for f in self.fontes]
        if len(set(refs_fontes)) != len(refs_fontes):
            raise ValueError("fonte repetida para o mesmo chunk_ref")
        citadas = {c.chunk_ref for i in self.itens for c in i.conteudo.citations}
        if set(refs_fontes) != citadas:
            raise ValueError("fontes devem ser exatamente os chunk_ref citados pelos itens entregues")
        ids = [i.item_id for i in self.itens]
        if len(set(ids)) != len(ids):
            raise ValueError("item_id repetido entre os itens entregues")
        if self.status is ChatStatus.RECUSA and self.motivo_recusa is None:
            raise ValueError("recusa exige motivo_recusa")
        if self.status is not ChatStatus.RECUSA and self.motivo_recusa is not None:
            raise ValueError("motivo_recusa só se aplica a recusa")
        return self

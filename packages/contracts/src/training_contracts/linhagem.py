"""Registros de linhagem (arquitetura §7), independentes de onde serão gravados."""

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import ConfigDict, Field, model_validator

from training_contracts.agentes import InputVerdict, SearchPlan
from training_contracts.chat import ChatStatus, MotivoRecusa
from training_contracts.comum import (
    ChunkRef,
    Contrato,
    Identificador,
    Intent,
    MotivoRejeicao,
    RetrievalMode,
    Sha256,
    TextoNaoVazio,
)
from training_contracts.geracao import Item, ItemBruto
from training_contracts.vereditos import MOTIVOS_E9


class DocumentVersionRecord(Contrato):
    document_version_id: Identificador
    document_id: Identificador
    owner_id: Identificador
    sha256: Sha256
    parser_version: Identificador
    created_at: datetime


class ChunkRecord(Contrato):
    """Chunk com proveniência; imutável depois de criado."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: Identificador
    document_version_id: Identificador
    chunker_version: Identificador
    page: int | None = Field(default=None, ge=1)
    section_path: list[str] = Field(default_factory=list)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    text: TextoNaoVazio
    injection_flagged: bool = False
    injection_reason: TextoNaoVazio | None = None

    @model_validator(mode="after")
    def _coerente(self) -> Self:
        if self.end_offset <= self.start_offset:
            raise ValueError("end_offset deve ser maior que start_offset")
        if self.injection_flagged != (self.injection_reason is not None):
            raise ValueError("injection_reason é obrigatório se e somente se injection_flagged")
        return self


class ChatTurnRecord(Contrato):
    """Turno de chat (§7). Invariantes conforme o status:

    - `motivo_recusa` se e somente se `recusa`;
    - `ok` e `conteudo_insuficiente` exigem intenção de geração; `fora_de_escopo` exige essa intenção;
    - modo obrigatório nas intenções de geração e ausente nas demais (RAG-CLS-001);
    - `ok`, `fora_de_escopo` e `conteudo_insuficiente` exigem veredito do E2 não malicioso;
    - veredito malicioso ⇔ recusa por `prompt_malicioso`;
    - recusa em E1 (`tamanho_excedido`, `limite_de_taxa`) acontece antes de E2/E3 (RAG-ENT-001):
      sem intenção, sem modo e sem veredito do E2;
    - recusa por falha do E2 não tem veredito do E2; nas recusas do E2 (`prompt_malicioso`,
      `falha_do_juiz_de_entrada`) intenção e modo são opcionais, porque o E3 roda em paralelo.
    """

    chat_turn_id: Identificador
    session_id: Identificador
    user_id: Identificador
    prompt: str
    status: ChatStatus
    motivo_recusa: MotivoRecusa | None = None
    intencao: Intent | None = Field(default=None, description="Ausente se recusado antes de E3.")
    modo: RetrievalMode | None = None
    input_verdict: InputVerdict | None = Field(default=None, description="Ausente se E2 falhou ou não rodou.")
    models: dict[str, str] = Field(default_factory=dict, description="Etapa → modelo usado.")
    prompt_versions: dict[str, str] = Field(default_factory=dict, description="Prompt → versão usada.")
    trace_id: Identificador
    created_at: datetime

    @model_validator(mode="after")
    def _coerente_com_status(self) -> Self:
        if (self.status is ChatStatus.RECUSA) != (self.motivo_recusa is not None):
            raise ValueError("motivo_recusa é obrigatório se e somente se o status é recusa")
        if self.status in (ChatStatus.OK, ChatStatus.CONTEUDO_INSUFICIENTE) and not (
            self.intencao is not None and self.intencao.e_geracao
        ):
            raise ValueError(f"status {self.status.value} exige intenção de geração")
        if self.status is ChatStatus.FORA_DE_ESCOPO and self.intencao is not Intent.FORA_DE_ESCOPO:
            raise ValueError("status fora_de_escopo exige intenção fora_de_escopo")
        geracao = self.intencao is not None and self.intencao.e_geracao
        if geracao and self.modo is None:
            raise ValueError("intenção de geração exige modo")
        if not geracao and self.modo is not None:
            raise ValueError("modo só se aplica a intenção de geração")
        respondido = self.status in (ChatStatus.OK, ChatStatus.FORA_DE_ESCOPO, ChatStatus.CONTEUDO_INSUFICIENTE)
        if respondido and (self.input_verdict is None or self.input_verdict.malicioso):
            raise ValueError(f"status {self.status.value} exige veredito do E2 não malicioso")
        malicioso = self.input_verdict is not None and self.input_verdict.malicioso
        if malicioso != (self.motivo_recusa is MotivoRecusa.PROMPT_MALICIOSO):
            raise ValueError("veredito malicioso se e somente se recusa por prompt_malicioso")
        recusa_e1 = {MotivoRecusa.TAMANHO_EXCEDIDO, MotivoRecusa.LIMITE_DE_TAXA}
        if self.motivo_recusa in recusa_e1 and (self.intencao is not None or self.modo is not None):
            raise ValueError(f"recusa por {self.motivo_recusa.value} ocorre em E1, antes de E3: sem intenção e sem modo")
        sem_e2 = recusa_e1 | {MotivoRecusa.FALHA_DO_JUIZ_DE_ENTRADA}
        if self.motivo_recusa in sem_e2 and self.input_verdict is not None:
            raise ValueError(f"recusa por {self.motivo_recusa.value} não tem veredito do E2")
        return self


class SearchPlanRecord(SearchPlan):
    search_plan_id: Identificador
    chat_turn_id: Identificador


class RetrievalCandidate(Contrato):
    chunk_id: Identificador
    rrf_score: float | None = Field(default=None, description="Ausente no modo cobertura.")
    rerank_score: float | None = None
    selected: bool


class RetrievalRunRecord(Contrato):
    retrieval_run_id: Identificador
    chat_turn_id: Identificador
    modo: RetrievalMode
    candidates: list[RetrievalCandidate]
    cutoff: float | None = Field(default=None, description="Corte de score do reranker aplicado em E7.")

    @property
    def selected(self) -> list[str]:
        return [c.chunk_id for c in self.candidates if c.selected]


class ItemStatus(StrEnum):
    ENTREGUE = "entregue"
    OMITIDO = "omitido"


MOTIVOS_SEM_ITEM_VALIDO = frozenset(
    {
        MotivoRejeicao.SEM_CITACAO,
        MotivoRejeicao.QUESTAO_MAL_FORMADA,
        MotivoRejeicao.VAZIO_OU_TRIVIAL,
        MotivoRejeicao.VIOLACAO_DE_SCHEMA,
    }
)
"""Motivos que podem vir de um item bruto que nem passa em `Item`; os demais exigem item válido."""


class ItemRecord(Contrato):
    """Item produzido no turno (§7): conteúdo bruto sempre; conteúdo validado quando existe.

    - `raw_content`: o item como recebido da geração (mesmo inválido);
    - `content`: o `Item` estrito (após reparo, se houve); obrigatório se entregue;
    - `motivo` se e somente se omitido; sem `content`, só motivos de `MOTIVOS_SEM_ITEM_VALIDO`.
    """

    item_id: Identificador
    chat_turn_id: Identificador
    raw_content: ItemBruto
    content: Item | None = None
    status: ItemStatus
    motivo: MotivoRejeicao | None = None

    @model_validator(mode="after")
    def _coerente_com_status(self) -> Self:
        if (self.status is ItemStatus.OMITIDO) != (self.motivo is not None):
            raise ValueError("motivo é obrigatório se e somente se o item foi omitido")
        if self.status is ItemStatus.ENTREGUE and self.content is None:
            raise ValueError("item entregue exige content válido")
        if self.content is None and self.motivo is not None and self.motivo not in MOTIVOS_SEM_ITEM_VALIDO:
            raise ValueError(f"motivo {self.motivo.value} exige content válido")
        return self


class CitationRecord(Contrato):
    citation_id: Identificador
    item_id: Identificador
    chunk_ref: ChunkRef
    chunk_id: Identificador
    evidence: TextoNaoVazio


class VerdictOrigin(StrEnum):
    E9 = "E9"
    E10 = "E10"


class VerdictRecord(Contrato):
    """Veredito de E9 ou E10 sobre um item (§7). Invariantes conforme a origem:

    - reprovado exige `motivo`; aprovado não tem `motivo`;
    - E9: motivo de `MOTIVOS_E9`, sem `judge_model` nem rubricas; aprovado também sem `detalhe`;
    - E10: `judge_model`, `fundamentado` e `conforme_rubrica` obrigatórios, `aprovado` = ambos;
      reprovado tem motivo `reprovado_no_juiz_de_saida` e o motivo do juiz em `detalhe`.
    """

    verdict_id: Identificador
    item_id: Identificador
    origem: VerdictOrigin
    tentativa: int = Field(default=1, ge=1, le=2, description="1 = geração original; 2 = após o reparo (RAG-JUI-003).")
    aprovado: bool
    motivo: MotivoRejeicao | None = None
    detalhe: TextoNaoVazio | None = None
    fundamentado: bool | None = Field(default=None, description="Só E10: groundedness.")
    conforme_rubrica: bool | None = Field(default=None, description="Só E10: rubrica de viés/padrão.")
    judge_model: Identificador | None = Field(default=None, description="Obrigatório em E10; ausente em E9.")
    created_at: datetime

    @model_validator(mode="after")
    def _coerente(self) -> Self:
        if not self.aprovado and self.motivo is None:
            raise ValueError("veredito reprovado exige motivo")
        if self.aprovado and self.motivo is not None:
            raise ValueError("veredito aprovado não tem motivo de rejeição")
        if self.origem is VerdictOrigin.E9:
            if self.judge_model is not None or self.fundamentado is not None or self.conforme_rubrica is not None:
                raise ValueError("E9 não tem judge_model, fundamentado nem conforme_rubrica")
            if self.motivo is not None and self.motivo not in MOTIVOS_E9:
                raise ValueError(f"motivo {self.motivo.value} não é dado por E9")
            if self.aprovado and self.detalhe is not None:
                raise ValueError("veredito aprovado de E9 não tem detalhe")
        else:
            if self.judge_model is None or self.fundamentado is None or self.conforme_rubrica is None:
                raise ValueError("E10 exige judge_model, fundamentado e conforme_rubrica")
            if self.aprovado != (self.fundamentado and self.conforme_rubrica):
                raise ValueError("em E10, aprovado = fundamentado e conforme_rubrica")
            if not self.aprovado and (
                self.motivo is not MotivoRejeicao.REPROVADO_NO_JUIZ_DE_SAIDA or self.detalhe is None
            ):
                raise ValueError("reprovação em E10 tem motivo reprovado_no_juiz_de_saida e detalhe")
        return self


class CitationLineage(Contrato):
    """Uma citação do item resolvida até o documento (OBS-LIN-001)."""

    citation_id: Identificador
    chunk_ref: ChunkRef
    chunk_id: Identificador
    evidence: TextoNaoVazio
    document_id: Identificador
    document_version_id: Identificador
    page: int | None = Field(default=None, ge=1)
    section_path: list[str] = Field(default_factory=list)


class ItemLineage(Contrato):
    """Resposta da consulta de linhagem pelo ID de um item entregue (OBS-LIN-001).

    item → citação → chunk → documento/versão/página/seção, e item → `trace_id` (Langfuse).
    `citations` corresponde às citações do conteúdo: mesmos pares (`chunk_ref`, `evidence`).
    """

    item_id: Identificador
    chat_turn_id: Identificador
    trace_id: Identificador
    content: Item
    citations: list[CitationLineage] = Field(min_length=1)

    @model_validator(mode="after")
    def _citacoes_do_conteudo(self) -> Self:
        do_conteudo = sorted((c.chunk_ref, c.evidence) for c in self.content.citations)
        resolvidas = sorted((c.chunk_ref, c.evidence) for c in self.citations)
        if do_conteudo != resolvidas:
            raise ValueError("citations devem corresponder às citações do conteúdo do item")
        return self

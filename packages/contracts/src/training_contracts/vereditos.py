"""Vereditos por item de E9 (validação determinística) e E10 (juiz de saída)."""

from typing import Self

from pydantic import Field, model_validator

from training_contracts.comum import Contrato, MotivoRejeicao, TextoNaoVazio


MOTIVOS_E9 = frozenset(MotivoRejeicao) - {MotivoRejeicao.REPROVADO_NO_JUIZ_DE_SAIDA}
"""Motivos que E9 pode dar (§2.3, mais `violacao_de_schema` do item bruto)."""


class DeterministicVerdict(Contrato):
    """E9 — resultado da validação determinística de um item (arquitetura §2.3).

    Aprovado: sem motivo nem detalhe. Reprovado: motivo de E9 obrigatório.
    """

    item_index: int = Field(ge=0, description="Posição do item na saída da geração.")
    aprovado: bool
    motivo: MotivoRejeicao | None = None
    detalhe: TextoNaoVazio | None = None

    @model_validator(mode="after")
    def _motivo_quando_reprovado(self) -> Self:
        if self.aprovado and (self.motivo is not None or self.detalhe is not None):
            raise ValueError("item aprovado não tem motivo nem detalhe de rejeição")
        if not self.aprovado and self.motivo is None:
            raise ValueError("item reprovado exige motivo")
        if self.motivo is not None and self.motivo not in MOTIVOS_E9:
            raise ValueError(f"motivo {self.motivo.value} não é dado por E9")
        return self


class OutputJudgeVerdict(Contrato):
    """E10 — veredito do juiz de saída para um item: groundedness e rubrica de viés/padrão."""

    item_index: int = Field(ge=0, description="Posição do item no lote julgado.")
    fundamentado: bool = Field(description="Groundedness: o item é sustentado pelos chunks citados.")
    conforme_rubrica: bool = Field(description="O item atende à rubrica de viés/padrão.")
    motivo: TextoNaoVazio | None = None

    @property
    def aprovado(self) -> bool:
        return self.fundamentado and self.conforme_rubrica

    @model_validator(mode="after")
    def _motivo_quando_reprovado(self) -> Self:
        if not self.aprovado and self.motivo is None:
            raise ValueError("item reprovado exige motivo")
        return self


class OutputJudgeResult(Contrato):
    """E10 — saída da única chamada ao juiz de saída: um veredito por item (RAG-JUI-001)."""

    vereditos: list[OutputJudgeVerdict]

    @model_validator(mode="after")
    def _um_por_item(self) -> Self:
        indices = [v.item_index for v in self.vereditos]
        if len(set(indices)) != len(indices):
            raise ValueError("mais de um veredito para o mesmo item")
        return self

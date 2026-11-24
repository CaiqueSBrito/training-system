"""Saídas estruturadas dos agentes E2, E3 e E4 (arquitetura §2)."""

from typing import Self

from pydantic import Field, model_validator

from training_contracts.comum import BloomLevel, Contrato, Intent, RetrievalMode, TextoNaoVazio


class InputVerdict(Contrato):
    """E2 — veredito do juiz de entrada: `{malicioso, categoria, motivo}`."""

    malicioso: bool
    categoria: TextoNaoVazio | None = Field(
        default=None, description="Categoria da tentativa; obrigatória quando malicioso."
    )
    motivo: TextoNaoVazio

    @model_validator(mode="after")
    def _categoria_quando_malicioso(self) -> Self:
        if self.malicioso and self.categoria is None:
            raise ValueError("veredito malicioso exige categoria")
        return self


class Classification(Contrato):
    """E3 — classificação `{intencao, modo, escopo}` (RAG-CLS-001).

    Intenções de geração exigem modo; `fora_de_escopo` não tem modo.
    """

    intencao: Intent
    modo: RetrievalMode | None = None
    escopo: TextoNaoVazio | None = Field(
        default=None, description='Recorte pedido pelo usuário, ex.: "capítulo 3" ou "fotossíntese".'
    )

    @model_validator(mode="after")
    def _modo_conforme_intencao(self) -> Self:
        if self.intencao.e_geracao and self.modo is None:
            raise ValueError(f"intenção {self.intencao.value} exige modo")
        if not self.intencao.e_geracao and self.modo is not None:
            raise ValueError("fora_de_escopo não tem modo")
        return self


class SearchPlan(Contrato):
    """E4 — plano de busca do agente pedagógico (RAG-PLN-001)."""

    objetivos: list[TextoNaoVazio] = Field(min_length=1, description="Objetivos de aprendizagem.")
    bloom_level: BloomLevel
    consultas: list[TextoNaoVazio] = Field(
        min_length=1, max_length=3, description="De 1 a 3 consultas reescritas para a recuperação."
    )

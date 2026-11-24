"""Tipos e enumerações comuns a todos os contratos."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

TextoNaoVazio = Annotated[str, Field(pattern=r"\S", description="Texto com ao menos um caractere visível.")]
"""String que não pode ser vazia nem só espaços. O valor não é alterado (sem `strip`)."""

ChunkRef = Annotated[
    str,
    Field(
        pattern=r"^C[1-9][0-9]*$",
        description="Referência ao chunk no contexto numerado da geração: C1, C2, ...",
        examples=["C3"],
    ),
]

Identificador = Annotated[str, Field(min_length=1, description="Identificador opaco do registro.")]

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", description="sha256 em hexadecimal minúsculo.")]


class Contrato(BaseModel):
    """Base dos contratos: rejeita campos desconhecidos."""

    model_config = ConfigDict(extra="forbid")


class BloomLevel(StrEnum):
    """Níveis da taxonomia de Bloom revisada (RAG-PLN-001)."""

    LEMBRAR = "lembrar"
    COMPREENDER = "compreender"
    APLICAR = "aplicar"
    ANALISAR = "analisar"
    AVALIAR = "avaliar"
    CRIAR = "criar"


class Intent(StrEnum):
    """Intenções fechadas do classificador E3 (RAG-CLS-001, G1)."""

    GERAR_FLASHCARDS = "gerar_flashcards"
    GERAR_QUESTIONARIO = "gerar_questionario"
    GERAR_MAIS = "gerar_mais"
    FORA_DE_ESCOPO = "fora_de_escopo"

    @property
    def e_geracao(self) -> bool:
        return self is not Intent.FORA_DE_ESCOPO


class RetrievalMode(StrEnum):
    """Modos de recuperação decididos em E3 (arquitetura §2.1)."""

    COBERTURA = "cobertura"
    CONSULTA = "consulta"


class MotivoRejeicao(StrEnum):
    """Motivos de rejeição ou omissão de um item (E9, E10, E11)."""

    SEM_CITACAO = "sem_citacao"
    CITACAO_INEXISTENTE = "citacao_inexistente"
    EVIDENCIA_NAO_ENCONTRADA = "evidencia_nao_encontrada"
    QUESTAO_MAL_FORMADA = "questao_mal_formada"
    DUPLICATA = "duplicata"
    VAZIO_OU_TRIVIAL = "vazio_ou_trivial"
    REPROVADO_NO_JUIZ_DE_SAIDA = "reprovado_no_juiz_de_saida"
    VIOLACAO_DE_SCHEMA = "violacao_de_schema"

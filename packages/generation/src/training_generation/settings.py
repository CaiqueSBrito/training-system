"""Configuração do serviço de geração (E2, E3, E4, E8, E10), lida do ambiente.

Os nomes dos modelos vêm do ambiente e não têm valor padrão: sem configuração explícita o
serviço não inicia (arquitetura §6). As credenciais nunca aparecem em `repr`, mensagens de
erro, prompts ou logs (SEC-PRM-001).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from training_llm import InjectionJudgeError, _guard_sdk_logging, read_settings

PROMPT_SOURCE_OFFLINE = "offline"
"""Fonte de prompts versionados no pacote (ADR-0016). Única suportada neste corte."""

DEFAULT_MAX_ITEMS = 20
DEFAULT_MAX_INPUT_CHARS = 8000
DEFAULT_TIMEOUT_SECONDS = 60.0

# Loggers do ADK fora da guarda global de `training_llm` (não editada por esta slice).
_ADK_LOGGERS = ("google_adk", "google.adk")


class ConfiguracaoInvalida(ValueError):
    """Configuração ausente ou proibida. A mensagem nunca contém valores de credencial."""


class LoggingInseguro(RuntimeError):
    """Logging em DEBUG ou callbacks do SDK poderiam vazar prompt, resposta ou credencial."""

    def __init__(self, setting: str | None):
        self.setting = setting
        super().__init__("Configuração de logging insegura para chamadas de LLM")


class _SuppressADKRecords(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not any(record.name == p or record.name.startswith(p + ".") for p in _ADK_LOGGERS)


_ADK_FILTER = _SuppressADKRecords()


def guard_logging() -> None:
    """Recusa logging inseguro antes de entregar dados a LiteLLM, google-genai ou ADK.

    Reusa a guarda de `training_llm` (LiteLLM, OpenAI, httpx, google.genai) e acrescenta os
    loggers do ADK, que podem registrar requisições e respostas do modelo.
    """
    os.environ.setdefault("LITELLM_LOG", "WARNING")
    try:
        _guard_sdk_logging()
    except InjectionJudgeError as exc:
        raise LoggingInseguro(exc.setting) from None
    nomes = set(_ADK_LOGGERS) | {
        n for n in logging.Logger.manager.loggerDict
        if any(n == p or n.startswith(p + ".") for p in _ADK_LOGGERS)
    }
    if any(logging.getLogger(n).getEffectiveLevel() <= logging.DEBUG for n in nomes):
        raise LoggingInseguro("google_adk.logger_level")
    # Filtros de logger não valem para registros criados em loggers filhos (o ADK cria loggers
    # sob demanda, ex.: `google_adk.google.adk.workflow._node_runner`, que faz
    # `logger.exception` com a mensagem do provedor). Nível acima de CRITICAL nos ancestrais
    # impede a criação desses registros em todo filho com nível herdado.
    for raiz in _ADK_LOGGERS:
        logging.getLogger(raiz).setLevel(logging.CRITICAL + 1)
    # Sem handlers configurados, o registro iria ao `logging.lastResort` (stderr).
    if logging.lastResort is not None and _ADK_FILTER not in logging.lastResort.filters:
        logging.lastResort.addFilter(_ADK_FILTER)
    for logger in [logging.getLogger()] + [logging.getLogger(n) for n in nomes]:
        if logger.name != "root" and _ADK_FILTER not in logger.filters:
            logger.addFilter(_ADK_FILTER)
        for handler in logger.handlers:
            if _ADK_FILTER not in handler.filters:
                handler.addFilter(_ADK_FILTER)


def _provider_prefix(model: str) -> str:
    prefix, sep, rest = model.partition("/")
    if not sep or not rest.strip():
        raise ConfiguracaoInvalida("modelo LiteLLM exige prefixo de provedor (gemini/ ou openrouter/)")
    return prefix


def is_gemini_family(model: str) -> bool:
    """Verdadeiro para qualquer modelo Gemini, direto (`gemini/…`) ou roteado (`openrouter/google/gemini-…`)."""
    partes = [p.strip().casefold() for p in model.split("/")]
    return any(p == "gemini" or p.startswith("gemini-") for p in partes)


def _positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    bruto = env.get(key, "").strip()
    if not bruto:
        return default
    try:
        valor = int(bruto)
    except ValueError:
        raise ConfiguracaoInvalida(f"{key} deve ser inteiro positivo") from None
    if valor < 1:
        raise ConfiguracaoInvalida(f"{key} deve ser inteiro positivo")
    return valor


def _positive_float(env: Mapping[str, str], key: str, default: float) -> float:
    bruto = env.get(key, "").strip()
    if not bruto:
        return default
    try:
        valor = float(bruto)
    except ValueError:
        raise ConfiguracaoInvalida(f"{key} deve ser número positivo") from None
    if not valor > 0 or valor == float("inf"):
        raise ConfiguracaoInvalida(f"{key} deve ser número positivo")
    return valor


@dataclass(frozen=True)
class GenerationSettings:
    """Modelos, fonte de prompts, limites e credenciais do serviço de geração.

    - `generation_model`: modelo Gemini (AI Studio) dos agentes ADK E3, E4 e E8, sem prefixo.
    - `input_judge_model`: modelo LiteLLM do juiz de entrada E2 (`gemini/…` ou `openrouter/…`).
    - `output_judge_model`: modelo LiteLLM do juiz de saída E10; só `openrouter/…` e nunca
      Gemini (RAG-JUI-002, ADR-0004).
    """

    prompt_source: str
    generation_model: str
    input_judge_model: str
    output_judge_model: str
    gemini_api_key: str = field(repr=False)
    openrouter_api_key: str = field(repr=False)
    max_items: int = DEFAULT_MAX_ITEMS
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if self.prompt_source != PROMPT_SOURCE_OFFLINE:
            raise ConfiguracaoInvalida(
                "PROMPT_SOURCE deve ser escolhida explicitamente; suportada: offline (ADR-0016)"
            )
        for nome in ("generation_model", "input_judge_model", "output_judge_model"):
            if not getattr(self, nome).strip():
                raise ConfiguracaoInvalida(f"{nome.upper()} não configurado; não há modelo padrão")
        if "/" in self.generation_model or not is_gemini_family(self.generation_model):
            raise ConfiguracaoInvalida(
                "GENERATION_MODEL deve ser um modelo Gemini do AI Studio, sem prefixo de provedor"
            )
        if _provider_prefix(self.input_judge_model) not in {"gemini", "openrouter"}:
            raise ConfiguracaoInvalida("INPUT_JUDGE_MODEL: provedor não suportado")
        if is_gemini_family(self.output_judge_model):
            raise ConfiguracaoInvalida("OUTPUT_JUDGE_MODEL não pode ser da família Gemini (ADR-0004)")
        if _provider_prefix(self.output_judge_model) != "openrouter":
            raise ConfiguracaoInvalida("OUTPUT_JUDGE_MODEL deve usar o OpenRouter (openrouter/…)")
        if not self.gemini_api_key.strip():
            raise ConfiguracaoInvalida("GEMINI_API_KEY ausente")
        if not self.openrouter_api_key.strip():
            raise ConfiguracaoInvalida("OPENROUTER_API_KEY ausente")
        for nome in ("max_items", "max_input_chars"):
            if getattr(self, nome) < 1:
                raise ConfiguracaoInvalida(f"{nome} deve ser positivo")
        if not self.timeout_seconds > 0:
            raise ConfiguracaoInvalida("timeout_seconds deve ser positivo")

    @classmethod
    def from_mapping(cls, env: Mapping[str, str]) -> GenerationSettings:
        return cls(
            prompt_source=env.get("PROMPT_SOURCE", "").strip(),
            generation_model=env.get("GENERATION_MODEL", "").strip(),
            input_judge_model=env.get("INPUT_JUDGE_MODEL", "").strip(),
            output_judge_model=env.get("OUTPUT_JUDGE_MODEL", "").strip(),
            gemini_api_key=env.get("GEMINI_API_KEY", ""),
            openrouter_api_key=env.get("OPENROUTER_API_KEY", ""),
            max_items=_positive_int(env, "CHAT_MAX_ITEMS", DEFAULT_MAX_ITEMS),
            max_input_chars=_positive_int(env, "CHAT_MAX_INPUT_CHARS", DEFAULT_MAX_INPUT_CHARS),
            timeout_seconds=_positive_float(env, "LLM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
        )

    @classmethod
    def from_env(cls, env_file: str = ".env") -> GenerationSettings:
        """Lê `.env` (se existir) sobreposto pelo ambiente do processo."""
        guard_logging()
        return cls.from_mapping(read_settings(env_file))

    def litellm_api_key(self, model: str) -> str:
        """Credencial do provedor do modelo LiteLLM; nunca encaminha a chave de outro provedor."""
        prefixo = _provider_prefix(model)
        if prefixo == "gemini":
            return self.gemini_api_key
        if prefixo == "openrouter":
            return self.openrouter_api_key
        raise ConfiguracaoInvalida("provedor LiteLLM não suportado")

    def secret_values(self) -> tuple[str, ...]:
        """Valores de credencial, para verificações de vazamento (SEC-PRM-001)."""
        return tuple(v for v in (self.gemini_api_key, self.openrouter_api_key) if v.strip())

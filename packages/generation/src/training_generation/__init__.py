"""Geração protegida do chat: agentes ADK, juízes e validação determinística (T-019)."""

from training_generation.agents import (
    GenerationError,
    GenerationMetadata,
    GenerationService,
    RepairItem,
    gemini_response_schema,
    generation_response_schema,
)
from training_generation.prompts import PromptArtifact, PromptArtifactError, PromptSet, load_prompts
from training_generation.settings import (
    ConfiguracaoInvalida,
    GenerationSettings,
    LoggingInseguro,
    guard_logging,
    is_gemini_family,
)
from training_generation.validation import (
    DUPLICATE_RATIO,
    NormalizedInput,
    SaidaInvalida,
    SanitizedItem,
    ValidationResult,
    normalize_evidence,
    normalize_input,
    sanitize_item,
    validate_items,
)

__all__ = [
    "ConfiguracaoInvalida",
    "DUPLICATE_RATIO",
    "GenerationError",
    "GenerationMetadata",
    "GenerationService",
    "GenerationSettings",
    "LoggingInseguro",
    "NormalizedInput",
    "PromptArtifact",
    "PromptArtifactError",
    "PromptSet",
    "RepairItem",
    "SaidaInvalida",
    "SanitizedItem",
    "ValidationResult",
    "gemini_response_schema",
    "generation_response_schema",
    "guard_logging",
    "is_gemini_family",
    "load_prompts",
    "normalize_evidence",
    "normalize_input",
    "sanitize_item",
    "validate_items",
]

from .chat import ChatPipeline, ChatFailure

__all__ += ["ChatPipeline", "ChatFailure"]

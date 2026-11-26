"""Configuração do serviço de geração: RAG-JUI-002, SEC-PRM-001 e fonte explícita de prompts."""

import logging

import pytest

from training_generation.settings import ConfiguracaoInvalida, GenerationSettings, LoggingInseguro, guard_logging


@pytest.mark.parametrize("model", [
    "gemini/gemini-3.1-flash-lite",
    "openrouter/google/gemini-3.1-flash-lite",
    "openrouter/google/Gemini-2.5-pro",
])
def test_RAG_JUI_002_juiz_de_saida_gemini_recusa_iniciar(make_env, model):
    with pytest.raises(ConfiguracaoInvalida, match="Gemini"):
        GenerationSettings.from_mapping(make_env(OUTPUT_JUDGE_MODEL=model))


def test_RAG_JUI_002_juiz_de_saida_exige_openrouter(make_env):
    with pytest.raises(ConfiguracaoInvalida, match="OpenRouter"):
        GenerationSettings.from_mapping(make_env(OUTPUT_JUDGE_MODEL="anthropic/claude-haiku-5.5"))


@pytest.mark.parametrize("key", ["GENERATION_MODEL", "INPUT_JUDGE_MODEL", "OUTPUT_JUDGE_MODEL"])
def test_modelos_sem_padrao_falham_na_configuracao(make_env, key):
    with pytest.raises(ConfiguracaoInvalida, match="não há modelo padrão"):
        GenerationSettings.from_mapping(make_env(**{key: None}))


@pytest.mark.parametrize("source", [None, "", "langfuse", "auto"])
def test_fonte_de_prompts_explicita_e_offline(make_env, source):
    with pytest.raises(ConfiguracaoInvalida, match="PROMPT_SOURCE"):
        GenerationSettings.from_mapping(make_env(PROMPT_SOURCE=source))


@pytest.mark.parametrize("model", ["gemini/gemini-3.1-flash-lite", "gpt-6", "openrouter/x"])
def test_modelo_dos_agentes_adk_e_gemini_sem_prefixo(make_env, model):
    with pytest.raises(ConfiguracaoInvalida, match="GENERATION_MODEL"):
        GenerationSettings.from_mapping(make_env(GENERATION_MODEL=model))


@pytest.mark.parametrize("key", ["GEMINI_API_KEY", "OPENROUTER_API_KEY"])
def test_credencial_ausente_falha_sem_expor_valores(make_env, secrets, key):
    with pytest.raises(ConfiguracaoInvalida) as erro:
        GenerationSettings.from_mapping(make_env(**{key: "  "}))
    assert all(s not in str(erro.value) for s in secrets)


def test_SEC_PRM_001_repr_nao_expoe_credenciais(settings, secrets):
    texto = repr(settings) + str(settings)
    assert all(s not in texto for s in secrets)
    assert settings.secret_values() == secrets


def test_credencial_por_provedor_nunca_cruza(settings, secrets):
    assert settings.litellm_api_key("gemini/gemini-3.1-flash-lite") == secrets[0]
    assert settings.litellm_api_key("openrouter/anthropic/claude-haiku-5.5") == secrets[1]


@pytest.mark.parametrize("key,value", [("CHAT_MAX_ITEMS", "0"), ("CHAT_MAX_ITEMS", "x"),
                                       ("CHAT_MAX_INPUT_CHARS", "-1"), ("LLM_TIMEOUT_SECONDS", "0")])
def test_limites_invalidos_falham(make_env, key, value):
    with pytest.raises(ConfiguracaoInvalida, match=key):
        GenerationSettings.from_mapping(make_env(**{key: value}))


def test_limites_padrao_iniciais(settings):
    assert (settings.max_items, settings.max_input_chars, settings.timeout_seconds) == (20, 8000, 60.0)


def test_guarda_recusa_debug_no_adk():
    logger = logging.getLogger("google_adk.teste_guarda")
    anterior = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        with pytest.raises(LoggingInseguro):
            guard_logging()
    finally:
        logger.setLevel(anterior)


def test_guarda_recusa_litellm_log_debug(monkeypatch):
    monkeypatch.setenv("LITELLM_LOG", "DEBUG")
    with pytest.raises(LoggingInseguro):
        guard_logging()

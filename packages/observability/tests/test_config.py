"""OBS-CFG-001 — validação de credenciais de observabilidade do Langfuse na inicialização."""

import pytest

from training_observability import (
    ObservabilityConfig,
    ObservabilityConfigError,
    configure_observability,
)

AMBIENTE_COMPLETO = {
    "LANGFUSE_PUBLIC_KEY": "pk-lf-teste-123456",
    "LANGFUSE_SECRET_KEY": "sk-lf-segredo-confidencial-999",
    "LANGFUSE_HOST": "http://localhost:3000",
}


def test_obs_cfg_001_falha_quando_langfuse_secret_key_vazio():
    env = {**AMBIENTE_COMPLETO, "LANGFUSE_SECRET_KEY": ""}

    with pytest.raises(ObservabilityConfigError) as exc_info:
        ObservabilityConfig.from_env(env)

    assert "LANGFUSE_SECRET_KEY" in str(exc_info.value)
    assert "LANGFUSE_SECRET_KEY" in exc_info.value.missing_vars


def test_obs_cfg_001_falha_quando_langfuse_secret_key_ausente():
    env = {k: v for k, v in AMBIENTE_COMPLETO.items() if k != "LANGFUSE_SECRET_KEY"}

    with pytest.raises(ObservabilityConfigError) as exc_info:
        ObservabilityConfig.from_env(env)

    assert "LANGFUSE_SECRET_KEY" in str(exc_info.value)
    assert exc_info.value.missing_vars == ("LANGFUSE_SECRET_KEY",)


@pytest.mark.parametrize("valor", [None, "", "   "])
def test_obs_cfg_001_falha_quando_langfuse_public_key_ausente_ou_espacos(valor):
    env = dict(AMBIENTE_COMPLETO)
    if valor is None:
        env.pop("LANGFUSE_PUBLIC_KEY")
    else:
        env["LANGFUSE_PUBLIC_KEY"] = valor

    with pytest.raises(ObservabilityConfigError) as exc_info:
        ObservabilityConfig.from_env(env)

    assert "LANGFUSE_PUBLIC_KEY" in str(exc_info.value)
    assert "LANGFUSE_PUBLIC_KEY" in exc_info.value.missing_vars


def test_obs_cfg_001_falha_quando_langfuse_host_ausente():
    env = {
        "LANGFUSE_PUBLIC_KEY": AMBIENTE_COMPLETO["LANGFUSE_PUBLIC_KEY"],
        "LANGFUSE_SECRET_KEY": AMBIENTE_COMPLETO["LANGFUSE_SECRET_KEY"],
    }

    with pytest.raises(ObservabilityConfigError) as exc_info:
        ObservabilityConfig.from_env(env)

    assert "LANGFUSE_HOST" in str(exc_info.value)
    assert "LANGFUSE_HOST" in exc_info.value.missing_vars


def test_obs_cfg_001_falha_nomeando_todas_variaveis_ausentes():
    with pytest.raises(ObservabilityConfigError) as exc_info:
        ObservabilityConfig.from_env({})

    mensagem = str(exc_info.value)
    assert "LANGFUSE_PUBLIC_KEY" in mensagem
    assert "LANGFUSE_SECRET_KEY" in mensagem
    assert "LANGFUSE_HOST" in mensagem
    assert set(exc_info.value.missing_vars) == {
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_HOST",
    }


def test_obs_cfg_001_configure_observability_falha_ao_iniciar_com_secret_vazio():
    env = {**AMBIENTE_COMPLETO, "LANGFUSE_SECRET_KEY": ""}

    with pytest.raises(ObservabilityConfigError) as exc_info:
        configure_observability(env=env, set_global_tracer_provider=False)

    assert "LANGFUSE_SECRET_KEY" in str(exc_info.value)


def test_obs_cfg_001_mensagem_de_erro_nao_vaza_valor_das_credenciais():
    env = {**AMBIENTE_COMPLETO, "LANGFUSE_PUBLIC_KEY": ""}

    with pytest.raises(ObservabilityConfigError) as exc_info:
        ObservabilityConfig.from_env(env)

    assert AMBIENTE_COMPLETO["LANGFUSE_SECRET_KEY"] not in str(exc_info.value)


def test_obs_cfg_001_aceita_langfuse_host_e_prioriza_base_url():
    cfg_host = ObservabilityConfig.from_env(
        {**AMBIENTE_COMPLETO, "LANGFUSE_HOST": "http://localhost:3000/"}
    )
    assert cfg_host.base_url == "http://localhost:3000"

    cfg_base = ObservabilityConfig.from_env(
        {
            **AMBIENTE_COMPLETO,
            "LANGFUSE_BASE_URL": "http://127.0.0.1:3100/",
        }
    )
    assert cfg_base.base_url == "http://127.0.0.1:3100"

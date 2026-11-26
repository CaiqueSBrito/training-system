"""Configuração e validação de credenciais de observabilidade (Langfuse / OpenTelemetry)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

ENV_LANGFUSE_PUBLIC_KEY = "LANGFUSE_PUBLIC_KEY"
ENV_LANGFUSE_SECRET_KEY = "LANGFUSE_SECRET_KEY"
ENV_LANGFUSE_HOST = "LANGFUSE_HOST"
ENV_LANGFUSE_BASE_URL = "LANGFUSE_BASE_URL"
ENV_LANGFUSE_ENVIRONMENT = "LANGFUSE_TRACING_ENVIRONMENT"
ENV_LANGFUSE_RELEASE = "LANGFUSE_RELEASE"
ENV_OTEL_SERVICE_NAME = "OTEL_SERVICE_NAME"


class ObservabilityConfigError(ValueError):
    """Erro de configuração ao inicializar a observabilidade sem credenciais válidas."""

    def __init__(self, message: str, *, missing_vars: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.missing_vars = missing_vars


def _valor_nao_vazio(valor: str | None) -> str | None:
    if valor is None:
        return None
    limpo = valor.strip()
    return limpo if limpo else None


def _erro_variaveis_ausentes(faltando: list[str]) -> ObservabilityConfigError:
    lista = ", ".join(faltando)
    mensagem = (
        f"Configuração de observabilidade do Langfuse incompleta. "
        f"Variável(is) ausente(s) ou vazia(s): {lista}. "
        f"Defina-as no ambiente (.env) antes de iniciar."
    )
    return ObservabilityConfigError(mensagem, missing_vars=tuple(faltando))


@dataclass(frozen=True, slots=True)
class ObservabilityConfig:
    """Parâmetros validados para exportar traces OpenTelemetry ao Langfuse."""

    public_key: str
    secret_key: str
    base_url: str
    service_name: str = "training-system"
    environment: str | None = None
    release: str | None = None
    timeout: int = 5
    flush_at: int | None = None
    flush_interval: float | None = None
    instrument_adk: bool = True

    def __post_init__(self) -> None:
        public_key = _valor_nao_vazio(self.public_key)
        secret_key = _valor_nao_vazio(self.secret_key)
        base_url = _valor_nao_vazio(self.base_url)

        faltando: list[str] = []
        if public_key is None:
            faltando.append(ENV_LANGFUSE_PUBLIC_KEY)
        if secret_key is None:
            faltando.append(ENV_LANGFUSE_SECRET_KEY)
        if base_url is None:
            faltando.append(ENV_LANGFUSE_HOST)

        if faltando:
            raise _erro_variaveis_ausentes(faltando)

        assert public_key is not None
        assert secret_key is not None
        assert base_url is not None

        if self.timeout <= 0:
            raise ObservabilityConfigError("timeout deve ser maior que zero.")
        if self.flush_at is not None and self.flush_at <= 0:
            raise ObservabilityConfigError("flush_at deve ser maior que zero.")
        if self.flush_interval is not None and self.flush_interval <= 0:
            raise ObservabilityConfigError("flush_interval deve ser maior que zero.")

        object.__setattr__(self, "public_key", public_key)
        object.__setattr__(self, "secret_key", secret_key)
        object.__setattr__(self, "base_url", base_url.rstrip("/"))

    def __repr__(self) -> str:
        return (
            f"ObservabilityConfig(public_key={self.public_key!r}, "
            f"secret_key='***', base_url={self.base_url!r}, "
            f"service_name={self.service_name!r}, environment={self.environment!r}, "
            f"timeout={self.timeout!r}, instrument_adk={self.instrument_adk!r})"
        )

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        **overrides: Any,
    ) -> ObservabilityConfig:
        """Lê e valida as credenciais do Langfuse a partir do ambiente."""
        origem = os.environ if env is None else env

        raw_public = (
            overrides.pop("public_key")
            if "public_key" in overrides
            else origem.get(ENV_LANGFUSE_PUBLIC_KEY)
        )
        raw_secret = (
            overrides.pop("secret_key")
            if "secret_key" in overrides
            else origem.get(ENV_LANGFUSE_SECRET_KEY)
        )
        if "base_url" in overrides:
            raw_base_url = overrides.pop("base_url")
            overrides.pop("host", None)
        elif "host" in overrides:
            raw_base_url = overrides.pop("host")
        else:
            raw_base_url = _valor_nao_vazio(
                origem.get(ENV_LANGFUSE_BASE_URL)
            ) or origem.get(ENV_LANGFUSE_HOST)

        public_key = _valor_nao_vazio(raw_public)
        secret_key = _valor_nao_vazio(raw_secret)
        base_url = _valor_nao_vazio(raw_base_url)

        faltando: list[str] = []
        if public_key is None:
            faltando.append(ENV_LANGFUSE_PUBLIC_KEY)
        if secret_key is None:
            faltando.append(ENV_LANGFUSE_SECRET_KEY)
        if base_url is None:
            faltando.append(ENV_LANGFUSE_HOST)

        if faltando:
            raise _erro_variaveis_ausentes(faltando)

        assert public_key is not None
        assert secret_key is not None
        assert base_url is not None

        service_name = (
            overrides.pop("service_name", None)
            or _valor_nao_vazio(origem.get(ENV_OTEL_SERVICE_NAME))
            or "training-system"
        )
        environment = overrides.pop("environment", None) or _valor_nao_vazio(
            origem.get(ENV_LANGFUSE_ENVIRONMENT)
        )
        release = overrides.pop("release", None) or _valor_nao_vazio(
            origem.get(ENV_LANGFUSE_RELEASE)
        )

        return cls(
            public_key=public_key,
            secret_key=secret_key,
            base_url=base_url,
            service_name=service_name,
            environment=environment,
            release=release,
            **overrides,
        )

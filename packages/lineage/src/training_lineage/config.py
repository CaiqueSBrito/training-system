"""Conexão exclusiva da aplicação; nunca usa credenciais do Langfuse."""

import os
from pathlib import Path
import shlex

from sqlalchemy import URL, Engine, create_engine


def database_url(env_file: str | Path = ".env") -> URL:
    """Lê KEY=value (aspas/comentários permitidos); ambiente prevalece sobre arquivo.

    Não executa shell nem expande variáveis. Nenhum segredo é copiado para o ambiente.
    """
    values: dict[str, str] = {}
    path = Path(env_file)
    if path.is_file():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("export "):
                stripped = stripped[7:].lstrip()
            key, separator, value = stripped.partition("=")
            if not separator or not key.strip().startswith("APP_POSTGRES_"):
                continue
            try:
                parts = shlex.split(value, comments=True)
            except ValueError:
                raise ValueError(f"Configuração inválida em {path.name}:{number}") from None
            values[key.strip()] = " ".join(parts)
    values.update({key: value for key, value in os.environ.items() if key.startswith("APP_POSTGRES_")})
    for key in ("APP_POSTGRES_DB", "APP_POSTGRES_USER", "APP_POSTGRES_PASSWORD"):
        if not values.get(key):
            raise ValueError(f"Defina {key} no ambiente ou no .env")
    try:
        port = int(values.get("APP_POSTGRES_PORT", "5432"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError("APP_POSTGRES_PORT deve estar entre 1 e 65535") from None
    return URL.create(
        "postgresql+psycopg", username=values["APP_POSTGRES_USER"],
        password=values["APP_POSTGRES_PASSWORD"], host=values.get("APP_POSTGRES_HOST", "127.0.0.1"),
        port=port, database=values["APP_POSTGRES_DB"],
    )


def create_lineage_engine(env_file: str | Path = ".env") -> Engine:
    return create_engine(database_url(env_file), pool_pre_ping=True, hide_parameters=True,
                         connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"})

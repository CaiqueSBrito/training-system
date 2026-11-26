"""Configuração imutável da API, sem registrar credenciais."""

from dataclasses import dataclass, fields
import os
from pathlib import Path
import shlex

from sqlalchemy import URL
from training_lineage import database_url


@dataclass(frozen=True)
class ApiSettings:
    database_url: URL | str
    cookie_secure: bool = True
    session_lifetime_seconds: int = 604800
    password_min_length: int = 12
    login_max_failures_per_account: int = 5
    login_max_failures_per_origin: int = 20
    login_window_seconds: int = 900
    register_max_per_origin: int = 5
    register_window_seconds: int = 3600

    def __post_init__(self):
        for field in fields(self):
            if field.name not in ('database_url', 'cookie_secure'):
                value = getattr(self, field.name)
                if type(value) is not int or value < 1:
                    raise ValueError(f'{field.name} deve ser um inteiro positivo')
        if type(self.cookie_secure) is not bool:
            raise ValueError('cookie_secure deve ser booleano')

    @classmethod
    def from_env(cls, env_file: str | Path = '.env') -> 'ApiSettings':
        values = {}
        path = Path(env_file)
        if path.is_file():
            for number, line in enumerate(path.read_text().splitlines(), 1):
                line = line.strip().removeprefix('export ')
                key, separator, value = line.partition('=')
                if separator and key.strip().startswith('API_'):
                    try:
                        values[key.strip()] = ' '.join(shlex.split(value, comments=True))
                    except ValueError:
                        raise ValueError(f'Configuração inválida em {path.name}:{number}') from None
        values.update({k: v for k, v in os.environ.items() if k.startswith('API_')})
        kwargs = {'database_url': database_url(env_file)}
        for field in fields(cls):
            key = 'API_' + field.name.upper()
            if key not in values or field.name == 'database_url':
                continue
            value = values[key]
            if field.name == 'cookie_secure':
                if value.lower() not in ('true', 'false'):
                    raise ValueError(f'{key} deve ser true ou false')
                kwargs[field.name] = value.lower() == 'true'
            else:
                try:
                    kwargs[field.name] = int(value)
                except ValueError:
                    raise ValueError(f'{key} deve ser um inteiro positivo') from None
        return cls(**kwargs)

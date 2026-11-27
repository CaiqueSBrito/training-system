"""Configuração imutável da API, sem registrar credenciais."""

from dataclasses import dataclass, field, fields
import os
from pathlib import Path
import shlex

from sqlalchemy import URL
from training_lineage import database_url


def attempt_secret():
    value = os.environ.get('API_ATTEMPT_KEY_SECRET', '')
    if not value.strip():
        raise ValueError('Defina API_ATTEMPT_KEY_SECRET')
    return value


@dataclass(frozen=True)
class ApiSettings:
    database_url: URL | str
    attempt_key_secret: str = field(default_factory=attempt_secret, repr=False)
    password_hash_concurrency: int = field(default_factory=lambda: max(2, os.cpu_count() or 2))
    password_hash_queue_multiplier: int = 4
    cookie_secure: bool = True
    docs_enabled: bool = True
    session_idle_seconds: int = 3600
    session_lifetime_seconds: int = 604800
    password_min_length: int = 12
    login_max_failures_per_account: int = 5
    login_max_failures_per_origin: int = 20
    login_window_seconds: int = 900
    register_max_per_origin: int = 5
    register_window_seconds: int = 3600
    chat_max_active_per_owner: int = 1
    chat_max_active_global: int = 4
    chat_turn_timeout_seconds: int = 180
    chat_session_id_max_length: int = 256
    chat_max_requests_per_window: int = 20
    chat_request_window_seconds: int = 60
    chat_pool_size: int = 4
    chat_pool_timeout_seconds: int = 2

    def __post_init__(self):
        for field in fields(self):
            if field.name not in ('database_url', 'cookie_secure', 'docs_enabled', 'attempt_key_secret'):
                value = getattr(self, field.name)
                if type(value) is not int or value < 1:
                    raise ValueError(f'{field.name} deve ser um inteiro positivo')
        if self.password_min_length > 1024:
            raise ValueError('password_min_length deve ser no máximo 1024')
        if not isinstance(self.attempt_key_secret, str) or not self.attempt_key_secret.strip():
            raise ValueError('Defina API_ATTEMPT_KEY_SECRET')
        if type(self.docs_enabled) is not bool:
            raise ValueError('docs_enabled deve ser booleano')
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
            if field.name == 'password_hash_concurrency' and not value.strip():
                continue
            if field.name == 'attempt_key_secret':
                kwargs[field.name] = value
            elif field.name in ('cookie_secure', 'docs_enabled'):
                if value.lower() not in ('true', 'false'):
                    raise ValueError(f'{key} deve ser true ou false')
                kwargs[field.name] = value.lower() == 'true'
            else:
                try:
                    kwargs[field.name] = int(value)
                except ValueError:
                    raise ValueError(f'{key} deve ser um inteiro positivo') from None
        return cls(**kwargs)

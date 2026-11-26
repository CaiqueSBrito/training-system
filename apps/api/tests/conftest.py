"""T-024 (G3): fixtures dos casos de autenticação da apps/api.

Postgres descartável real (padrão de packages/lineage/tests): um banco novo por
teste, migrado até a head (que deve incluir a migração 0006 de contas), apagado
ao final. Nenhum dublê: a API real roda sobre o banco real pelo cliente ASGI.

A interface pública exercitada está em
governanca/mudancas/2026-11-26-apps-api-autenticacao/evidencias/T-024/interface.md.
O produto é importado dentro das fixtures para que cada caso falhe isolado, pelo
motivo certo, enquanto training_api.app / training_api.config não existirem.
"""

from importlib import import_module
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from training_lineage import create_lineage_engine, migrate

from api_auth_support import (
    LOGIN_FAILURES_PER_ACCOUNT, LOGIN_FAILURES_PER_ORIGIN, LOGIN_WINDOW, MIN_PASSWORD, ORIGIN_B,
    REGISTRATION_WINDOW, REGISTRATIONS_PER_ORIGIN, SESSION_LIFETIME, Api,
)


@pytest.fixture
def database():
    """Banco temporário exclusivo, migrado até a head."""
    admin = create_lineage_engine()
    name = f"test_api_{uuid4().hex}"
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
    engine = create_engine(admin.url.set(database=name), hide_parameters=True)
    try:
        migrate(engine, "head")
        yield engine
    finally:
        engine.dispose()
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def app(database):
    """App real da apps/api com limites pequenos e explícitos para os testes."""
    config = import_module("training_api.config")
    factory = import_module("training_api.app")
    settings = config.ApiSettings(
        database_url=database.url,
        cookie_secure=True,
        session_lifetime_seconds=SESSION_LIFETIME,
        password_min_length=MIN_PASSWORD,
        login_max_failures_per_account=LOGIN_FAILURES_PER_ACCOUNT,
        login_max_failures_per_origin=LOGIN_FAILURES_PER_ORIGIN,
        login_window_seconds=LOGIN_WINDOW,
        register_max_per_origin=REGISTRATIONS_PER_ORIGIN,
        register_window_seconds=REGISTRATION_WINDOW,
    )
    return factory.create_app(settings)


@pytest.fixture
def api(app):
    client = Api(app)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def alice(api):
    response = api.register("alice@example.com", origin=ORIGIN_B)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def bob(api):
    response = api.register("bob@example.com", origin=ORIGIN_B)
    assert response.status_code == 201, response.text
    return response.json()

"""N1–N3: testes escritos antes das correções R4; dublês só neste arquivo."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Lock
import time

import pytest
from sqlalchemy import text
from starlette.testclient import TestClient

from api_auth_support import PASSWORD, ORIGIN_A


def test_N1_forty_simultaneous_logins_bound_argon2(app, monkeypatch):
    from fastapi_users.password import PasswordHelper
    from training_api.app import create_app
    from training_api import limits
    # Não testa a disputa dos advisory locks, já coberta na R3: assegura que os
    # 40 logins cheguem ao hasher, com PostgreSQL real e reservas em commit.
    original_reserve = limits.reserve_login
    async def uncontended_reserve(request, email):
        import anyio
        for _ in range(400):
            try:
                return await original_reserve(request, email)
            except Exception as exc:
                if getattr(exc, 'status_code', None) != 429:
                    raise
                await anyio.sleep(0.005)
        raise AssertionError('reserva não progrediu')
    monkeypatch.setattr('training_api.app.reserve_login', uncontended_reserve)
    active = maximum = calls = 0
    mutex = Lock()
    def slow_hash(self, password):
        nonlocal active, maximum, calls
        with mutex:
            active += 1
            calls += 1
            maximum = max(maximum, active)
        try:
            time.sleep(0.04)
            return 'test-only-hash'
        finally:
            with mutex:
                active -= 1
    monkeypatch.setattr(PasswordHelper, 'hash', slow_hash)
    configured = create_app(replace(app.state.settings, password_hash_concurrency=2,
                                    login_max_failures_per_origin=100))
    barrier = Barrier(41)
    with TestClient(configured, base_url='https://testserver', client=ORIGIN_A) as client:
        def login(index):
            barrier.wait(timeout=15)
            return client.post('/auth/login', data={'username': f'concurrency{index}@example.com', 'password': PASSWORD})
        with ThreadPoolExecutor(max_workers=40) as executor:
            futures = [executor.submit(login, index) for index in range(40)]
            barrier.wait(timeout=15)
            assert all(f.result(timeout=20).status_code == 400 for f in futures)
    assert calls == 40
    assert maximum == 2


def test_N2_purge_skips_locked_rows_and_is_periodic(api, alice, app, database):
    from hashlib import sha256
    session = api.login('alice@example.com')
    token = sha256(session.token.encode()).hexdigest()
    with database.begin() as connection:
        connection.execute(text("UPDATE auth_access_token SET created_at=now()-interval '8 days' WHERE token=:token"), {'token': token})
    app.state.next_auth_purge = 0
    with database.begin() as locked:
        locked.execute(text('SELECT * FROM auth_access_token WHERE token=:token FOR UPDATE'), {'token': token})
        started = time.monotonic()
        assert api.login_response('absent-a@example.com', 'incorrect-123').status_code == 400
        assert time.monotonic() - started < 5
        assert locked.scalar(text('SELECT count(*) FROM auth_access_token WHERE token=:token'), {'token': token}) == 1
    # Mesmo após destravar, o intervalo impede nova purga imediata.
    assert api.login_response('absent-b@example.com', 'incorrect-123').status_code == 400
    with database.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM auth_access_token WHERE token=:token'), {'token': token}) == 1
    app.state.next_auth_purge = 0
    assert api.login_response('absent-c@example.com', 'incorrect-123').status_code == 400
    with database.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM auth_access_token WHERE token=:token'), {'token': token}) == 0


@pytest.mark.parametrize('csrf', [False, True])
def test_N2_authentication_dependencies_commit_before_return(api, alice, app, csrf):
    from fastapi import Depends
    from sqlalchemy.ext.asyncio import AsyncSession
    from training_api.auth import current_user
    from training_api.csrf import require_csrf
    from training_api.database import get_session
    session = api.login('alice@example.com')
    dependency = require_csrf if csrf else current_user
    # Rota exclusiva do teste, observa a mesma AsyncSession antes do finalizador.
    @app.api_route('/test-auth-transaction', methods=['POST' if csrf else 'GET'])
    async def observe(checked=Depends(dependency), db: AsyncSession = Depends(get_session, scope='function')):
        return {'transaction_open': db.in_transaction()}
    response = api.request('POST' if csrf else 'GET', '/test-auth-transaction', session=session,
                           csrf=session.csrf if csrf else None)
    assert response.status_code == 200
    assert response.json() == {'transaction_open': False}


def test_N3_login_nowait_releases_verified_reservation(api, alice, database):
    with database.begin() as locked:
        locked.execute(text('SELECT * FROM app_user FOR UPDATE'))
        response = api.login_response('alice@example.com')
        assert response.status_code == 429
    with database.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM auth_attempt WHERE kind='login_failure'")) == 0


def test_N3_password_change_nowait_releases_verified_reservation(api, alice, database):
    session = api.login('alice@example.com')
    with database.begin() as locked:
        locked.execute(text('SELECT * FROM app_user FOR UPDATE'))
        response = api.request('POST', '/auth/change-password', session=session, csrf=session.csrf,
                               json={'current_password': PASSWORD, 'new_password': 'new-password-123'})
        assert response.status_code == 429
    with database.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM auth_attempt WHERE kind='login_failure'")) == 0


def test_N3_revoked_session_after_verification_releases_reservation(api, alice, database, monkeypatch):
    from training_api.auth import UserManager
    session = api.login('alice@example.com')
    original = UserManager.hash_password
    async def revoke_then_hash(self, password):
        with database.begin() as connection:
            connection.execute(text('DELETE FROM auth_access_token'))
        return await original(self, password)
    monkeypatch.setattr(UserManager, 'hash_password', revoke_then_hash)
    response = api.request('POST', '/auth/change-password', session=session, csrf=session.csrf,
                           json={'current_password': PASSWORD, 'new_password': 'new-password-123'})
    assert response.status_code == 401
    with database.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM auth_attempt WHERE kind='login_failure'")) == 0


def test_N1_hash_concurrency_default_and_env(monkeypatch, tmp_path):
    from training_api.config import ApiSettings
    monkeypatch.setattr('training_api.config.os.cpu_count', lambda: 1)
    assert ApiSettings(database_url='postgresql+psycopg://localhost/test').password_hash_concurrency == 2
    for key, value in {'APP_POSTGRES_DB': 'test', 'APP_POSTGRES_USER': 'test', 'APP_POSTGRES_PASSWORD': 'test'}.items():
        monkeypatch.setenv(key, value)
    env = tmp_path / 'api.env'
    env.write_text('API_PASSWORD_HASH_CONCURRENCY=\n')
    assert ApiSettings.from_env(env).password_hash_concurrency == 2
    monkeypatch.setenv('API_PASSWORD_HASH_CONCURRENCY', '3')
    assert ApiSettings.from_env(env).password_hash_concurrency == 3
    monkeypatch.setenv('API_PASSWORD_HASH_CONCURRENCY', '0')
    with pytest.raises(ValueError, match='password_hash_concurrency'):
        ApiSettings.from_env(env)


def test_N2_purge_batch_is_bounded(api, alice, app, database):
    with database.begin() as connection:
        connection.execute(text("INSERT INTO auth_attempt(kind,scope,key,created_at) SELECT 'register','origin',:key,now()-interval '2 days' FROM generate_series(1,105)"), {'key': 'f'*64})
    app.state.next_auth_purge = 0
    assert api.login_response('absent-batch@example.com', 'incorrect-123').status_code == 400
    with database.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM auth_attempt WHERE key=:key'), {'key': 'f'*64}) == 5

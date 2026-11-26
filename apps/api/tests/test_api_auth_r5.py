"""R5: regressões de conta inativa, purga múltipla e teto da fila do hash."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, Lock
import time

import pytest
from sqlalchemy import text
from starlette.testclient import TestClient

from api_auth_support import PASSWORD, ORIGIN_A


@pytest.mark.parametrize('password', [PASSWORD, 'incorrect-password-123'])
def test_R4_1_inactive_account_correct_and_wrong_password_exhaust_same_budget(api, alice, database, password):
    with database.begin() as connection:
        connection.execute(text('UPDATE app_user SET is_active=false'))
    responses = [api.login_response('alice@example.com', password) for _ in range(4)]
    assert [response.status_code for response in responses] == [400, 400, 400, 429]
    assert all(response.json() == {'detail': 'LOGIN_BAD_CREDENTIALS'} for response in responses[:3])
    with database.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM auth_attempt WHERE kind='login_failure' AND scope='account'")) == 3
        assert connection.scalar(text('SELECT count(*) FROM auth_access_token')) == 0


def test_R4_2_multiple_expired_batches_removed_in_one_purge(api, alice, app, database):
    with database.begin() as connection:
        connection.execute(text("INSERT INTO auth_attempt(kind,scope,key,created_at) SELECT 'register','origin',:key,now()-interval '2 days' FROM generate_series(1,305)"), {'key': 'e'*64})
        connection.execute(text("INSERT INTO auth_access_token(token,user_id,csrf_token,created_at) SELECT md5(i::text)||md5(i::text),id,md5(('csrf'||i)::text)||md5(('csrf'||i)::text),now()-interval '8 days' FROM app_user CROSS JOIN generate_series(1,205) i"))
    app.state.next_auth_purge = 0
    assert api.login_response('absent-purge@example.com', 'incorrect-123').status_code == 400
    with database.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM auth_attempt WHERE key=:key'), {'key': 'e'*64}) == 0
        assert connection.scalar(text('SELECT count(*) FROM auth_access_token')) == 0


def test_R4_3_queue_overflow_returns_503_without_hash(app, monkeypatch):
    from fastapi_users.password import PasswordHelper
    from training_api.app import create_app
    from training_api import limits
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
    gate, mutex = Event(), Lock()
    calls = 0
    def slow_hash(self, password):
        nonlocal calls
        with mutex:
            calls += 1
        assert gate.wait(timeout=10)
        return 'test-only-hash'
    monkeypatch.setattr(PasswordHelper, 'hash', slow_hash)
    configured = create_app(replace(app.state.settings, password_hash_concurrency=2,
                                    password_hash_queue_multiplier=1, login_max_failures_per_origin=100))
    with TestClient(configured, base_url='https://testserver', client=ORIGIN_A) as client:
        def login(index):
            return client.post('/auth/login', data={'username': f'queue{index}@example.com', 'password': PASSWORD})
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(login, index) for index in range(4)]
            try:
                deadline = time.monotonic() + 10
                while configured.state.password_budget.pending < 4 and time.monotonic() < deadline:
                    time.sleep(0.005)
                assert configured.state.password_budget.pending == 4
                assert login(99).status_code == 503
                with mutex:
                    assert calls == 2  # duas executando e duas em fila; a quinta não chegou ao hash
            finally:
                gate.set()
            assert all(future.result(timeout=10).status_code == 400 for future in futures)
    assert calls == 4


def test_R4_3_queue_multiplier_from_environment(monkeypatch, tmp_path):
    from training_api.config import ApiSettings
    for key, value in {'APP_POSTGRES_DB': 'test', 'APP_POSTGRES_USER': 'test', 'APP_POSTGRES_PASSWORD': 'test'}.items():
        monkeypatch.setenv(key, value)
    assert ApiSettings(database_url='postgresql+psycopg://localhost/test').password_hash_queue_multiplier == 4
    monkeypatch.setenv('API_PASSWORD_HASH_QUEUE_MULTIPLIER', '2')
    assert ApiSettings.from_env(tmp_path / 'missing.env').password_hash_queue_multiplier == 2
    monkeypatch.setenv('API_PASSWORD_HASH_QUEUE_MULTIPLIER', '0')
    with pytest.raises(ValueError, match='password_hash_queue_multiplier'):
        ApiSettings.from_env(tmp_path / 'missing.env')

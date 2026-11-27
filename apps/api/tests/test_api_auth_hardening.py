"""T-024 R3: regressões de segurança escritas antes das correções do produto."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import hmac
import time
from threading import Barrier

import anyio
import pytest
from sqlalchemy import text
from starlette.testclient import TestClient

from api_auth_support import ORIGIN_A, ORIGIN_B, PASSWORD


def test_A1_concurrent_login_does_not_starve_authenticated_read(api, alice, database):
    session = api.login('alice@example.com', origin=ORIGIN_B)
    attack = api.client(ORIGIN_A)
    reader = api.client(ORIGIN_B)
    barrier = Barrier(17)
    def attempt(index):
        barrier.wait(timeout=10)
        return attack.post('/auth/login', data={'username': f'absent{index}@example.com', 'password': PASSWORD})
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(attempt, index) for index in range(16)]
        barrier.wait(timeout=10)
        reads = [executor.submit(reader.get, '/users/me', headers={'Cookie': f'__Host-training_session={session.token}'})
                 for _ in range(3)]
        assert all(f.result(timeout=10).status_code == 200 for f in reads)
        statuses = [f.result(timeout=10).status_code for f in futures]
    assert time.monotonic() - started < 10
    assert set(statuses) <= {400, 429}
    assert 400 in statuses
    with database.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM auth_attempt WHERE kind='login_failure' AND scope='origin'")) == statuses.count(400)


def test_A2_ipv6_prefix_shares_registration_limit(api):
    for index in range(3):
        assert api.register(f'ipv6-{index}@example.com', origin=(f'2001:db8:abcd:12::{index+1}', 40000)).status_code == 201
    assert api.register('ipv6-blocked@example.com', origin=('2001:db8:abcd:12::99', 40000)).status_code == 429
    assert api.register('ipv6-other@example.com', origin=('2001:db8:abcd:13::1', 40000)).status_code == 201


def test_A3_missing_client_fails_closed(app, database):
    with TestClient(app, base_url='https://testserver', client=None) as client:
        response = client.post('/auth/login', data={'username': 'none@example.com', 'password': PASSWORD})
        assert response.status_code == 403
        assert client.post('/auth/register', json={'email': 'none@example.com', 'password': PASSWORD}).status_code == 403
    with database.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM auth_attempt')) == 0


def test_A4_password_change_failures_share_login_budget(api, alice, database):
    session = api.login('alice@example.com')
    for _ in range(3):
        response = api.request('POST', '/auth/change-password', session=session, csrf=session.csrf,
                               json={'current_password': 'incorrect-123', 'new_password': PASSWORD})
        assert response.status_code == 400
        assert response.json()['detail'] == 'INVALID_CURRENT_PASSWORD'
    assert api.request('POST', '/auth/change-password', session=session, csrf=session.csrf,
                       json={'current_password': PASSWORD, 'new_password': 'new-password-123'}).status_code == 429
    assert api.login_response('alice@example.com', origin=ORIGIN_B).status_code == 429
    with database.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM auth_attempt WHERE kind='login_failure' AND scope='account'")) == 3


def test_A5_account_hmac_uses_postgres_normalization(api, app, database):
    email = 'vİctim@example.com'
    assert api.login_response(email, 'incorrect-123').status_code == 400
    with database.connect() as connection:
        normalized = connection.scalar(text('SELECT lower(:email)'), {'email': email})
        key = connection.scalar(text("SELECT key FROM auth_attempt WHERE scope='account'"))
    expected = hmac.new(app.state.settings.attempt_key_secret.encode(), normalized.encode(), 'sha256').hexdigest()
    assert key == expected
    assert key != sha256(normalized.encode()).hexdigest()


@pytest.mark.parametrize('path', ['/auth/login', '/auth/register'])
@pytest.mark.parametrize('headers', [{'Origin': 'https://evil.example'}, {'Origin': 'null'},
                                     {'Sec-Fetch-Site': 'cross-site'}])
def test_A6_cross_site_auth_rejected(api, path, headers):
    response = api.request('POST', path, headers=headers,
                           data={'username': 'none@example.com', 'password': PASSWORD} if path.endswith('login') else None,
                           json={'email': 'none@example.com', 'password': PASSWORD} if path.endswith('register') else None)
    assert response.status_code == 403


def test_A6_same_origin_auth_allowed(api):
    response = api.request('POST', '/auth/register', headers={'Origin': 'https://testserver', 'Sec-Fetch-Site': 'same-origin'},
                           json={'email': 'same@example.com', 'password': PASSWORD})
    assert response.status_code == 201


def test_A8_hashing_yields_to_event_loop(app, monkeypatch):
    from training_api.auth import UserManager
    manager = UserManager(None, app.state.settings)
    def slow_hash(password):
        time.sleep(0.15)
        return 'test-hash'
    monkeypatch.setattr(manager.password_helper, 'hash', slow_hash)
    async def run():
        ticks = 0
        async def ticker():
            nonlocal ticks
            for _ in range(8):
                await asyncio.sleep(0.01)
                ticks += 1
        hashing = asyncio.create_task(manager.hash_password(PASSWORD))
        await ticker()
        assert not hashing.done()
        assert ticks == 8
        assert await hashing == 'test-hash'
    anyio.run(run)


def test_maximum_password_length(api, alice):
    assert api.register('too-long@example.com', password='x' * 1025).status_code == 400
    assert api.login_response('alice@example.com', password='x' * 1025).status_code == 400
    session = api.login('alice@example.com')
    assert api.request('POST', '/auth/change-password', session=session, csrf=session.csrf,
                       json={'current_password': PASSWORD, 'new_password': 'x' * 1025}).status_code == 400
    assert api.register('max@example.com', password='x' * 1024).status_code == 201


def test_A9_idle_expiry_and_throttled_touch(api, alice, database):
    session = api.login('alice@example.com')
    with database.connect() as connection:
        original = connection.scalar(text('SELECT last_seen_at FROM auth_access_token'))
    assert api.me(session).status_code == 200
    with database.connect() as connection:
        assert connection.scalar(text('SELECT last_seen_at FROM auth_access_token')) == original
    with database.begin() as connection:
        connection.execute(text("UPDATE auth_access_token SET last_seen_at=clock_timestamp()-interval '2 minutes'"))
    assert api.me(session).status_code == 200
    with database.connect() as connection:
        assert connection.scalar(text('SELECT last_seen_at FROM auth_access_token')) >= original
    with database.begin() as connection:
        connection.execute(text("UPDATE auth_access_token SET last_seen_at=clock_timestamp()-interval '61 minutes'"))
    assert api.me(session).status_code == 401


def test_A10_only_session_and_csrf_digests_stored(api, alice, database):
    session = api.login('alice@example.com')
    with database.connect() as connection:
        token, csrf = connection.execute(text('SELECT token, csrf_token FROM auth_access_token')).one()
    assert token == sha256(session.token.encode()).hexdigest()
    assert csrf == sha256(session.csrf.encode()).hexdigest()
    assert api.me(cookie=token).status_code == 401
    assert api.request('POST', '/auth/logout', session=session, csrf=csrf).status_code == 403


def test_A10_attempt_secret_required(monkeypatch, tmp_path):
    from training_api.config import ApiSettings
    monkeypatch.delenv('API_ATTEMPT_KEY_SECRET', raising=False)
    with pytest.raises(ValueError, match='API_ATTEMPT_KEY_SECRET'):
        ApiSettings(database_url='postgresql+psycopg://localhost/test')
    for name, value in {'APP_POSTGRES_DB': 'test', 'APP_POSTGRES_USER': 'test', 'APP_POSTGRES_PASSWORD': 'test'}.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match='API_ATTEMPT_KEY_SECRET'):
        ApiSettings.from_env(tmp_path / 'missing.env')


def test_A10_opportunistic_purge(api, alice, app, database):
    session = api.login('alice@example.com')
    with database.begin() as connection:
        connection.execute(text("UPDATE auth_access_token SET created_at=now()-interval '8 days'"))
        connection.execute(text("INSERT INTO auth_attempt(kind,scope,key,created_at) VALUES ('login_failure','origin',:key,now()-interval '2 days'), ('register','origin',:key,now()-interval '2 days')"), {'key': 'a' * 64})
    # R4: torna a purga periódica elegível sem esperar um minuto.
    app.state.next_auth_purge = 0
    assert api.login_response('absent@example.com', 'incorrect-123').status_code == 400
    with database.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM auth_access_token')) == 0
        assert connection.scalar(text("SELECT count(*) FROM auth_attempt WHERE key=:key"), {'key': 'a' * 64}) == 0


def test_I1_no_store_and_I2_docs_optional(api, alice, app):
    from training_api.app import create_app
    session = api.login('alice@example.com')
    assert api.me(session).headers['cache-control'] == 'no-store'
    configured = create_app(replace(app.state.settings, docs_enabled=False))
    with TestClient(configured, base_url='https://testserver') as client:
        assert client.get('/docs').status_code == 404
        assert client.get('/redoc').status_code == 404
        assert client.get('/openapi.json').status_code == 200


def test_A8_all_credential_flows_hash_and_verify_in_threads(api, monkeypatch):
    from fastapi_users.password import PasswordHelper
    original_hash = PasswordHelper.hash
    original_verify = PasswordHelper.verify_and_update
    calls = []
    def checked_hash(self, password):
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        calls.append('hash')
        return original_hash(self, password)
    def checked_verify(self, password, hashed):
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        calls.append('verify')
        return original_verify(self, password, hashed)
    monkeypatch.setattr(PasswordHelper, 'hash', checked_hash)
    monkeypatch.setattr(PasswordHelper, 'verify_and_update', checked_verify)
    assert api.register('threads@example.com').status_code == 201
    session = api.login('threads@example.com')
    assert api.request('POST', '/auth/change-password', session=session, csrf=session.csrf,
                       json={'current_password': PASSWORD, 'new_password': 'new-thread-password-123'}).status_code == 204
    assert calls.count('hash') == 2
    assert calls.count('verify') == 2

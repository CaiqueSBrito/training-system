"""R5-1: sobrecarga sem verificação não consome limite de conta ou origem."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, Lock
import time

import pytest
from sqlalchemy import text
from starlette.testclient import TestClient

from api_auth_support import PASSWORD, ORIGIN_A


@pytest.mark.parametrize('flow', ['existing_login', 'missing_login', 'change_password'])
def test_R5_1_repeated_overload_releases_reservations(app, database, monkeypatch, flow):
    from fastapi_users.password import PasswordHelper
    from training_api.app import create_app
    from training_api import limits
    configured = create_app(replace(app.state.settings, password_hash_concurrency=2,
                                    password_hash_queue_multiplier=1))
    gate, mutex = Event(), Lock()
    calls = 0
    def slow_hash(self, password):
        nonlocal calls
        with mutex:
            calls += 1
        assert gate.wait(timeout=15)
        return 'test-only-hash'
    def unexpected_verification(self, password, hashed):
        raise AssertionError('fila cheia não pode verificar senha')
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
    with TestClient(configured, base_url='https://testserver', client=ORIGIN_A) as client:
        assert client.post('/auth/register', json={'email': 'legitimate@example.com', 'password': PASSWORD}).status_code == 201
        login = client.post('/auth/login', data={'username': 'legitimate@example.com', 'password': PASSWORD})
        assert login.status_code == 200
        csrf = login.json()['csrf_token']
        monkeypatch.setattr(PasswordHelper, 'hash', slow_hash)
        monkeypatch.setattr(PasswordHelper, 'verify_and_update', unexpected_verification)
        def attack(index):
            return client.post('/auth/login', data={'username': f'blocking{index}@example.com', 'password': PASSWORD})
        def recorded_failures():
            with database.connect() as connection:
                return connection.execute(text("SELECT id,scope,key,created_at FROM auth_attempt WHERE kind='login_failure' ORDER BY id")).all()
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(attack, index) for index in range(4)]
            try:
                deadline = time.monotonic() + 10
                while configured.state.password_budget.pending < 4 and time.monotonic() < deadline:
                    time.sleep(0.005)
                assert configured.state.password_budget.pending == 4
                baseline = recorded_failures()
                assert len(baseline) == 8  # quatro reservas, uma por conta e outra por origem
                for _ in range(8):  # acima de ambos os limites G3 (3 por conta, 6 por origem)
                    if flow == 'change_password':
                        response = client.post('/auth/change-password', headers={'X-CSRF-Token': csrf},
                                               json={'current_password': PASSWORD, 'new_password': 'new-password-123'})
                    else:
                        email = 'legitimate@example.com' if flow == 'existing_login' else 'missing@example.com'
                        response = client.post('/auth/login', data={'username': email, 'password': PASSWORD})
                    assert response.status_code == 503
                    assert recorded_failures() == baseline
                with mutex:
                    assert calls == 2
            finally:
                gate.set()
            assert all(future.result(timeout=10).status_code == 400 for future in futures)
        assert calls == 4

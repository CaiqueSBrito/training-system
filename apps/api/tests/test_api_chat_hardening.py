"""T-025 R3: casos escritos antes das correções; PG/Qdrant reais, só injeções de falha em teste."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import logging
import secrets
import threading
import time
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from starlette.background import BackgroundTask
from training_generation.chat import ChatPipeline
from training_lineage import tables as t

from api_auth_support import SESSION_COOKIE, CSRF_HEADER
from api_chat_support import chat_box, chat_qdrant, events, post_chat


@pytest.fixture(autouse=True)
def ephemeral_secret(monkeypatch):
    monkeypatch.setenv('API_ATTEMPT_KEY_SECRET', secrets.token_hex(32))


def settings(app, **values):
    app.state.settings = replace(app.state.settings, **values)


def hold_model(box):
    entered, release = threading.Event(), threading.Event()
    original = box.models.input_verdict

    async def wait(prompt):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(.01)
        return await original(prompt)
    box.models.input_verdict = wait
    return entered, release


@pytest.mark.parametrize('session_id', ['   ', 's' * 257])
def test_M1_A3_session_rejected_before_stream(api, chat_box, session_id):
    response = api.request('POST', '/chat', session=chat_box.session, csrf=chat_box.session.csrf,
                           json={'session_id': session_id, 'mensagem': 'Gere'})
    assert response.status_code == 422
    assert chat_box.models.calls == []


@pytest.mark.parametrize('chunked', [False, True])
def test_A3_body_limit_before_json_and_auth(app, api, chunked):
    # ASGI direto permite provar chunked e ausência de cookie antes do parser.
    async def exercise():
        chunks = [b'{' + b'x' * (128 * 1024), b'x' * (128 * 1024)]
        received, sent = [], []
        async def receive():
            index = len(received)
            received.append(index)
            return {'type': 'http.request', 'body': chunks[index], 'more_body': index == 0}
        async def send(message):
            sent.append(message)
        headers = [(b'content-type', b'application/json')]
        if not chunked:
            headers.append((b'content-length', str(sum(map(len, chunks))).encode()))
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.3'},
                 'method': 'POST', 'path': '/chat', 'raw_path': b'/chat', 'root_path': '',
                 'scheme': 'https', 'http_version': '1.1', 'query_string': b'',
                 'headers': headers, 'client': ('203.0.113.10', 40000), 'server': ('testserver', 443)}
        await app(scope, receive, send)
        assert next(m['status'] for m in sent if m['type'] == 'http.response.start') == 413
        assert len(received) == (2 if chunked else 0)
    api.client().portal.call(exercise)


def test_M1_A5_exception_after_200_has_sanitized_final(api, chat_box, database, monkeypatch, caplog):
    original = ChatPipeline.run
    async def late_failure(self, *args, **kwargs):
        await original(self, *args, **kwargs)
        raise RuntimeError('secret-sentinel-do-not-log')
    monkeypatch.setattr(ChatPipeline, 'run', late_failure)
    with caplog.at_level(logging.WARNING):
        response = post_chat(api, chat_box)
    final = events(response)[-1][1]
    assert final['status'] == 'erro' and final['itens'] == []
    assert 'secret-sentinel' not in response.text + caplog.text
    records = [r for r in caplog.records if 'RuntimeError' in r.getMessage()]
    assert records and all(r.exc_info is None for r in records)
    if final['chat_turn_id']:
        with database.connect() as connection:
            assert connection.execute(select(t.chat_turn.c.response).where(
                t.chat_turn.c.chat_turn_id == final['chat_turn_id'])).scalar_one() == final


def test_A7_no_inexistent_id_when_error_commit_fails(api, chat_box, monkeypatch):
    from sqlalchemy import event
    def fail_commit(connection):
        raise RuntimeError('commit-secret-sentinel')
    engine = chat_box.factory(progress=None).engine
    chat_box.models.observe = lambda: event.listen(engine, 'commit', fail_commit)
    try:
        final = events(post_chat(api, chat_box))[-1][1]
        assert final['status'] == 'erro' and not final['itens']
        assert final['chat_turn_id'] is None
        assert 'commit-secret-sentinel' not in json.dumps(final)
    finally:
        if event.contains(engine, 'commit', fail_commit):
            event.remove(engine, 'commit', fail_commit)


def test_A1_users_me_remains_responsive_during_e9(api, chat_box, monkeypatch):
    import training_generation.chat as module
    original = ChatPipeline._resolve
    entered, release = threading.Event(), threading.Event()
    observed = []
    def large_e9(self, *args, **kwargs):
        observed.append(threading.current_thread().name)
        entered.set()
        # Algoritmo real quadrático do E9 com 6000 caracteres; barreira mantém
        # a etapa aberta para medir /users/me sem depender da velocidade da CPU.
        assert module._evidence_range('a' * 6000, 'a' * 6000) == (0, 6000)
        assert release.wait(5)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(ChatPipeline, '_resolve', large_e9)
    api.client()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(post_chat, api, chat_box)
        try:
            assert entered.wait(5)
            start = time.monotonic()
            assert api.me(chat_box.session).status_code == 200
            assert time.monotonic() - start < 1
            assert not future.done()
        finally:
            release.set()
        assert events(future.result(timeout=15))[-1][1]['status'] == 'ok'
    assert all('training-chat-turn' in name for name in observed)


@pytest.mark.parametrize('other_owner,expected', [(False, 429), (True, 503)])
def test_A2_active_owner_and_global_limits_before_stream(api, app, chat_box, bob, other_owner, expected):
    settings(app, chat_max_active_global=1, chat_max_active_per_owner=1)
    other = api.login('bob@example.com') if other_owner else chat_box.session
    entered, release = hold_model(chat_box)
    api.client()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(post_chat, api, chat_box)
        try:
            assert entered.wait(5)
            response = api.request('POST', '/chat', session=other, csrf=other.csrf,
                json={'session_id': 'parallel', 'mensagem': 'Gere'})
            assert response.status_code == expected
            assert not response.headers.get('content-type', '').startswith('text/event-stream')
        finally:
            release.set()
        assert events(future.result(timeout=15))[-1][1]['status'] == 'ok'
    # Vaga liberada após a resposta; outro turno pode entrar.
    assert post_chat(api, chat_box).status_code == 200


def test_A2_deadline_cancels_and_persists_error(api, app, chat_box, database):
    settings(app, chat_turn_timeout_seconds=1)
    chat_box.models.slow = True
    start = time.monotonic()
    frames = events(post_chat(api, chat_box))
    final = frames[-1][1]
    assert [data['stage'] for name, data in frames if name == 'progress'][-1] == 'E12'
    assert time.monotonic() - start < 5
    assert chat_box.models.cancelled
    assert final['status'] == 'erro' and final['itens'] == []
    with database.connect() as connection:
        row = connection.execute(select(t.chat_turn.c.response).where(
            t.chat_turn.c.chat_turn_id == final['chat_turn_id'])).scalar_one()
        assert row == final
        assert not connection.execute(select(t.chat_item)).all()


def test_A8_rate_rejected_before_stream_including_e1_refusals(api, app, chat_box, database):
    settings(app, chat_max_requests_per_window=1)
    assert events(post_chat(api, chat_box, message='x' * 8001))[-1][1]['status'] == 'recusa'
    response = post_chat(api, chat_box, message='x' * 8001)
    assert response.status_code == 429
    with database.connect() as connection:
        assert len(connection.execute(select(t.chat_turn)).all()) == 1


@pytest.mark.parametrize('kind', ['items', 'turns'])
def test_I1_lineage_is_no_store(api, chat_box, kind):
    final = events(post_chat(api, chat_box))[-1][1]
    identifier = final['itens'][0]['item_id'] if kind == 'items' else final['chat_turn_id']
    response = api.request('GET', f'/lineage/{kind}/{identifier}', session=chat_box.session)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'


def test_A6_background_closes_generator_on_blocked_send(api, app, chat_box, database, monkeypatch):
    import training_api.chat_routes as module
    captured = []
    original = module.StreamingResponse
    def response(*args, **kwargs):
        captured.append(kwargs.get('background'))
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'StreamingResponse', response)
    chat_box.models.slow = True

    async def exercise():
        first = True
        blocked = asyncio.Event()
        async def receive():
            nonlocal first
            if first:
                first = False
                return {'type': 'http.request', 'body': json.dumps({'session_id': 'slow-send', 'mensagem': 'Gere'}).encode(), 'more_body': False}
            await blocked.wait()
            while not chat_box.models.entered.is_set():
                await asyncio.sleep(.01)
            return {'type': 'http.disconnect'}
        async def send(message):
            if message['type'] == 'http.response.start':
                assert message['status'] == 200
            if message['type'] == 'http.response.body' and message.get('body'):
                blocked.set()
                await asyncio.Event().wait()
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.3'},
                 'method': 'POST', 'path': '/chat', 'raw_path': b'/chat', 'root_path': '',
                 'scheme': 'https', 'http_version': '1.1', 'query_string': b'',
                 'headers': [(b'content-type', b'application/json'),
                             (b'cookie', f'{SESSION_COOKIE}={chat_box.session.token}'.encode()),
                             (CSRF_HEADER.lower().encode(), chat_box.session.csrf.encode())],
                 'client': ('203.0.113.10', 40000), 'server': ('testserver', 443)}
        await asyncio.wait_for(app(scope, receive, send), 8)
    api.client().portal.call(exercise)
    assert chat_box.models.cancelled
    assert len(captured) == 1 and isinstance(captured[0], BackgroundTask)
    assert captured[0].func.__name__ == 'aclose'
    with database.connect() as connection:
        assert connection.execute(select(t.chat_turn.c.response)).scalar_one()['status'] == 'erro'
    # Liberação explícita também em yield bloqueado, sem depender do GC.
    chat_box.models.slow = False
    assert post_chat(api, chat_box).status_code == 200


def test_M2_cli_callback_preserves_aggregate_api_gets_real_stages(chat_box):
    cli_records, api_records = [], []
    pipeline = chat_box.factory(progress=cli_records.append)
    asyncio.run(pipeline.run(chat_box.owner, 'cli', 'Gere', document_version_ids=[chat_box.version], count=1))
    assert 'E5_E7' in [r['stage'] for r in cli_records]
    assert not {'E5', 'E6', 'E7'} & {r['stage'] for r in cli_records}
    pipeline.stage_progress = api_records.append
    asyncio.run(pipeline.run(chat_box.owner, 'api', 'Gere', document_version_ids=[chat_box.version], count=1))
    assert [r['stage'] for r in api_records] == ['E5', 'E6', 'E7']


def test_N2_runtime_failure_logs_only_exception_type(app, api, caplog, monkeypatch):
    import training_api.chat_runtime as module
    # Novo lifespan com injeção apenas em teste, sem ler credenciais reais.
    def unavailable(*args, **kwargs):
        raise ValueError('runtime-secret-sentinel')
    monkeypatch.setattr(module, 'ChatRuntime', unavailable)
    from starlette.testclient import TestClient
    with caplog.at_level(logging.WARNING), TestClient(app, base_url='https://testserver'):
        pass
    assert 'ValueError' in caplog.text and 'runtime-secret-sentinel' not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)

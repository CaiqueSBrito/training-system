"""T-025 / G3: API-CHT-001, API-LIN-001 e AUT-OWN-001, sem chamadas LLM reais."""
import asyncio
import json
import secrets
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from training_contracts import ChatResponse
from training_lineage import tables as t
from training_lineage.chat import ChatLineageRepository

from api_auth_support import SESSION_COOKIE, CSRF_HEADER
from api_chat_support import chat_box, chat_qdrant, events, post_chat


@pytest.fixture(autouse=True)
def chat_attempt_secret(monkeypatch):
    # Segredo efêmero exclusivo destes casos; não depende de credencial local.
    monkeypatch.setenv('API_ATTEMPT_KEY_SECRET', secrets.token_hex(32))


LABELS = {'E1': 'entendendo', 'E2': 'entendendo', 'E3': 'entendendo', 'E4': 'entendendo',
          'E5': 'buscando', 'E6': 'buscando', 'E7': 'buscando', 'E8': 'gerando',
          'E9': 'verificando', 'E10': 'verificando', 'E11': 'verificando', 'E12': 'verificando'}


def assert_progress(frames, stages):
    records = [data for name, data in frames if name == 'progress']
    assert [data['stage'] for data in records] == stages
    assert [data['sequence'] for data in records] == list(range(1, len(stages) + 1))
    for data in records:
        assert data['label'] == LABELS[data['stage']]
        assert isinstance(data['seconds'], (float, int)) and data['seconds'] >= 0
        assert set(data) == {'stage', 'sequence', 'label', 'seconds'}


def stored_response(database, response):
    # Conexão independente: evento final só é válido depois do commit.
    with database.connect() as connection:
        row = connection.execute(select(t.chat_turn).where(
            t.chat_turn.c.chat_turn_id == response.chat_turn_id)).mappings().one()
        assert row['response'] == response.model_dump(mode='json')
        return row


@pytest.mark.parametrize('mode,stages,status', [
    ('ok', list(LABELS), 'ok'),
    ('recusa', ['E1', 'E12'], 'recusa'),
    ('erro', ['E1', 'E2', 'E3', 'E4', 'E5', 'E6', 'E7', 'E8', 'E12'], 'erro'),
])
def test_api_cht_001_order_headers_single_final_and_committed_graph(api, chat_box, database, mode, stages, status):
    chat_box.models.fail = mode == 'erro'
    frames = events(post_chat(api, chat_box, message='x' * 8001 if mode == 'recusa' else 'Gere um flashcard'))
    assert_progress(frames, stages)
    response = ChatResponse.model_validate(frames[-1][1])
    assert response.status.value == status
    row = stored_response(database, response)
    assert row['owner_id'] == chat_box.owner
    assert response.trace_id and response.chat_turn_id
    if mode == 'ok':
        assert response.itens and response.fontes
        with database.connect() as connection:
            for item in response.itens:
                found = ChatLineageRepository(connection).get_item_lineage(chat_box.owner, item.item_id)
                assert found['lineage'].trace_id == response.trace_id
    else:
        assert not response.itens and not response.fontes
        with database.connect() as connection:
            assert not connection.execute(select(t.chat_item)).all()
        if mode == 'recusa':
            assert response.motivo_recusa.value == 'tamanho_excedido'
            assert chat_box.models.calls == []
        else:
            assert 'timeout' not in response.mensagem


@pytest.mark.parametrize('path,method', [('/chat', 'POST'), ('/lineage/items/unknown', 'GET'),
                                       ('/lineage/turns/unknown', 'GET')])
@pytest.mark.parametrize('cookie', [None, 'invalid-session'])
def test_aut_own_001_data_routes_require_valid_session(api, database, path, method, cookie):
    response = api.request(method, path, cookie=cookie,
                           **({'json': {'session_id': 's', 'mensagem': 'Gere'}} if method == 'POST' else {}))
    assert response.status_code == 401, response.text
    with database.connect() as connection:
        assert not connection.execute(select(t.chat_turn)).all()


@pytest.mark.parametrize('csrf', ['missing', 'invalid', 'other-session'])
def test_api_cht_001_csrf_rejected_without_turn(api, chat_box, database, csrf):
    token = None if csrf == 'missing' else 'invalid'
    if csrf == 'other-session':
        token = api.login('alice@example.com').csrf
    response = api.request('POST', '/chat', session=chat_box.session, csrf=token,
                           json={'session_id': 's', 'mensagem': 'Gere'})
    assert response.status_code == 403, response.text
    assert chat_box.models.calls == []
    with database.connect() as connection:
        assert not connection.execute(select(t.chat_turn)).all()


def test_aut_own_001_body_owner_never_selected(api, chat_box, bob, database):
    response = post_chat(api, chat_box, owner_id=str(bob['id']))
    assert response.status_code in (200, 422), response.text
    if response.status_code == 422:
        with database.connect() as connection:
            assert not connection.execute(select(t.chat_turn)).all()
        response = post_chat(api, chat_box)
    final = ChatResponse.model_validate(events(response)[-1][1])
    assert final.status.value == 'ok'
    assert stored_response(database, final)['owner_id'] == chat_box.owner
    with database.connect() as connection:
        assert not connection.execute(select(t.chat_turn).where(t.chat_turn.c.owner_id == str(bob['id']))).all()


def test_aut_own_001_query_owner_cannot_select_another_owner(api, chat_box, bob, database):
    response = api.request('POST', '/chat', session=chat_box.session, csrf=chat_box.session.csrf,
        params={'owner_id': str(bob['id'])}, json={'session_id': 's', 'mensagem': 'Gere um flashcard'})
    assert response.status_code in (200, 422), response.text
    if response.status_code == 200:
        final = ChatResponse.model_validate(events(response)[-1][1])
        assert stored_response(database, final)['owner_id'] == chat_box.owner
    else:
        with database.connect() as connection:
            assert not connection.execute(select(t.chat_turn)).all()


@pytest.fixture
def delivered(chat_box):
    # Semeia pelo domínio real para isolar GET /lineage de POST /chat.
    pipeline = chat_box.factory(progress=None)
    response = asyncio.run(pipeline.run(chat_box.owner, 'seed', 'Gere um flashcard',
                                       document_version_ids=[chat_box.version], count=1))
    assert response.status.value == 'ok' and response.itens
    return response


@pytest.mark.parametrize('kind', ['items', 'turns'])
def test_api_lin_001_owner_sources_and_equal_404(api, chat_box, bob, delivered, kind, database):
    identifier = delivered.itens[0].item_id if kind == 'items' else delivered.chat_turn_id
    path = f'/lineage/{kind}/{identifier}'
    own = api.request('GET', path, session=chat_box.session)
    assert own.status_code == 200, own.text
    with database.connect() as connection:
        expected = ChatLineageRepository(connection).get_item_lineage(chat_box.owner, delivered.itens[0].item_id)
    expected = json.loads(json.dumps(expected, default=lambda obj: obj.model_dump(mode='json')))
    if kind == 'items':
        assert own.json() == expected
    else:
        assert own.json()['response'] == delivered.model_dump(mode='json')
        assert own.json()['turn']['user_id'] == chat_box.owner
        assert own.json()['items'] == [expected]
    lineage = own.json()['lineage'] if kind == 'items' else own.json()['items'][0]['lineage']
    assert lineage['trace_id'] == delivered.trace_id
    citation = lineage['citations'][0]
    assert {'document_id', 'document_version_id', 'page', 'section_path', 'evidence'} <= citation.keys()
    assert citation['document_version_id'] == chat_box.version
    assert citation['evidence'] and citation['section_path'] == ['Biologia']
    # Markdown não tem página exata: null conserva a proveniência real.
    assert citation['page'] is None
    other = api.login('bob@example.com')
    denied = api.request('GET', path, session=other)
    missing = api.request('GET', f'/lineage/{kind}/{uuid4().hex}', session=other)
    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json()
    # Spoofing por query tampouco permite consultar o id alheio.
    spoof = api.request('GET', path, session=other, params={'owner_id': chat_box.owner})
    assert spoof.status_code in (404, 422)
    if spoof.status_code == 404:
        assert spoof.json() == missing.json()


def test_api_cht_001_auth_transaction_released_before_models(api, chat_box, database):
    with database.begin() as connection:
        connection.execute(text("UPDATE auth_access_token SET last_seen_at=clock_timestamp()-interval '2 minutes'"))
    observations = []

    def observe():
        with database.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout='500ms'"))
            # Prova visibilidade do heartbeat e ausência do lock da autenticação.
            age = connection.execute(text('SELECT extract(epoch FROM clock_timestamp()-last_seen_at) FROM auth_access_token')).scalar_one()
            connection.execute(text('SELECT token FROM auth_access_token FOR UPDATE NOWAIT')).all()
            observations.append(age)
            assert age < 60
    chat_box.models.observe = observe
    final = ChatResponse.model_validate(events(post_chat(api, chat_box))[-1][1])
    assert final.status.value == 'ok'
    assert observations and max(observations) < 60


def test_api_cht_001_disconnect_cancels_and_persists_error(api, app, chat_box, database):
    chat_box.models.slow = True

    async def exercise():
        first = True
        sent = []
        body = json.dumps({'session_id': 'disconnect', 'mensagem': 'Gere um flashcard'}).encode()

        async def receive():
            nonlocal first
            if first:
                first = False
                return {'type': 'http.request', 'body': body, 'more_body': False}
            await asyncio.wait_for(chat_box.models.entered.wait(), 5)
            return {'type': 'http.disconnect'}

        async def send(message):
            sent.append(message)
            if message['type'] == 'http.response.start':
                assert message['status'] == 200, 'POST /chat deve existir antes de testar desconexão'

        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.3'},
                 'http_version': '1.1', 'method': 'POST', 'scheme': 'https', 'path': '/chat',
                 'raw_path': b'/chat', 'query_string': b'', 'root_path': '',
                 'server': ('testserver', 443), 'client': ('203.0.113.10', 40000),
                 'headers': [(b'host', b'testserver'), (b'content-type', b'application/json'),
                             (b'cookie', f'{SESSION_COOKIE}={chat_box.session.token}'.encode()),
                             (CSRF_HEADER.lower().encode(), chat_box.session.csrf.encode())]}
        await asyncio.wait_for(app(scope, receive, send), 8)
        assert chat_box.models.cancelled
        bodies = b''.join(msg.get('body', b'') for msg in sent)
        assert b'"item_id"' not in bodies

    # Mesmo loop do lifespan/engine assíncrono; não cria servidor nem chamada de rede.
    api.client().portal.call(exercise)
    with database.connect() as connection:
        row = connection.execute(select(t.chat_turn)).mappings().one()
        assert row['owner_id'] == chat_box.owner
        assert row['record']['status'] == row['response']['status'] == 'erro'
        assert row['response']['itens'] == []
        assert not connection.execute(select(t.chat_item)).all()

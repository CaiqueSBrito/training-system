"""Decisões humanas A4/I2/A11: texto literal e cookie __Host-, sem LLM real."""
from dataclasses import replace
from http.cookies import SimpleCookie
import json
import secrets

import pytest
from sqlalchemy import select
from training_contracts import Flashcard, InputVerdict, RawGenerationOutput
from training_contracts.rendering import render_item
from training_ingestion import Ingestor
from training_ingestion.indexing import IndexingPipeline
from training_lineage import tables as t

from api_chat_support import chat_box, chat_qdrant, events, post_chat

MARKUP = '<script>alert("x")</script> <img src="x" onerror="alert(1)"> &amp; literal'


@pytest.fixture(autouse=True)
def ephemeral_secret(monkeypatch):
    monkeypatch.setenv('API_ATTEMPT_KEY_SECRET', secrets.token_hex(32))


def test_A4_plaintext_is_identical_in_sse_item_and_turn_lineage(api, chat_box, tmp_path):
    source = tmp_path / 'literal.md'
    source.write_text(f'# HTML\n\nConteúdo literal: `{MARKUP}`.\n', encoding='utf-8')
    Ingestor(chat_box.factory(progress=None).engine,
             processor=IndexingPipeline(chat_box.models, chat_box.factory(progress=None).index)).ingest(source, chat_box.owner)

    async def generate(prompt, classification, plan, context, count, *args, **kwargs):
        chunk = next(chunk for chunk in context.chunks if MARKUP in chunk.text)
        return RawGenerationOutput(itens=[dict(tipo='flashcard', front='Qual trecho é literal?',
            back=MARKUP, bloom_level='lembrar', citations=[dict(chunk_ref=chunk.ref, evidence=MARKUP)])])
    chat_box.models.generate = generate
    final = events(post_chat(api, chat_box))[-1][1]
    assert final['status'] == 'ok' and len(final['itens']) == 1
    item_id = final['itens'][0]['item_id']
    item = api.request('GET', f'/lineage/items/{item_id}', session=chat_box.session)
    turn = api.request('GET', f"/lineage/turns/{final['chat_turn_id']}", session=chat_box.session)
    assert item.status_code == turn.status_code == 200
    values = [final['itens'][0]['conteudo'], item.json()['lineage']['content'],
              turn.json()['items'][0]['lineage']['content'], turn.json()['response']['itens'][0]['conteudo']]
    for value in values:
        assert value['back'] == MARKUP
        assert value['citations'][0]['evidence'] == MARKUP
    assert item.json()['lineage']['citations'][0]['evidence'] == MARKUP
    # Entidade literal da fonte preservada, sem escapar ou desescapar parcialmente.
    for payload in (final, item.json(), turn.json()):
        encoded = json.dumps(payload, ensure_ascii=False)
        assert '<script>' in encoded and '<img' in encoded and 'onerror=' in encoded
        assert '&lt;' not in encoded and '&amp;amp;' not in encoded


def test_A4_controls_still_removed_without_html_or_unicode_conversion():
    original = Flashcard(tipo='flashcard', front='Pergunta\u200b\x07\n\té',
        back=MARKUP, bloom_level='lembrar',
        citations=[{'chunk_ref': 'C1', 'evidence': MARKUP + '\u2066'}])
    delivered = render_item(original)
    assert delivered.front == 'Pergunta\n\té'
    assert delivered.back == delivered.citations[0].evidence == MARKUP
    assert '\u200b' in original.front and original.citations[0].evidence.endswith('\u2066')


def test_I2_turn_exposes_only_category_and_keeps_full_verdict_in_database(api, chat_box, database):
    async def judge(prompt):
        return InputVerdict(malicioso=True, categoria='injection', motivo='segredo-do-juiz-nao-publicar')
    chat_box.models.input_verdict = judge
    final = events(post_chat(api, chat_box))[-1][1]
    assert final['status'] == 'recusa'
    response = api.request('GET', f"/lineage/turns/{final['chat_turn_id']}", session=chat_box.session)
    assert response.status_code == 200
    assert response.json()['turn']['input_verdict'] == {'categoria': 'injection'}
    assert 'segredo-do-juiz-nao-publicar' not in response.text
    with database.connect() as connection:
        record = connection.execute(select(t.chat_turn.c.record).where(
            t.chat_turn.c.chat_turn_id == final['chat_turn_id'])).scalar_one()
        assert record['input_verdict']['motivo'] == 'segredo-do-juiz-nao-publicar'
        assert record['input_verdict']['malicioso'] is True


def test_A11_host_cookie_attributes_and_legacy_cookie_rejected(api, alice):
    session = api.login('alice@example.com')
    parsed = SimpleCookie()
    parsed.load(session.set_cookie)
    assert set(parsed) == {'__Host-training_session'}
    cookie = parsed['__Host-training_session']
    assert cookie['secure'] and cookie['httponly'] and cookie['samesite'].lower() == 'lax'
    assert cookie['path'] == '/' and cookie['domain'] == ''
    assert api.me(session).status_code == 200
    legacy = api.request('GET', '/users/me', headers={'Cookie': f'training_session={session.token}'})
    assert legacy.status_code == 401


def test_A11_host_cookie_cannot_disable_secure(app):
    with pytest.raises(ValueError, match='Secure'):
        replace(app.state.settings, cookie_secure=False)

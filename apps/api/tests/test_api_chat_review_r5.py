"""R1/R3/R4/R5/N5: novos casos antes do código; persistência real, sem LLM."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import secrets
import time

import pytest
from sqlalchemy import select
from training_contracts import ChatRequest, ChatResponse, ChatTurnRecord, RawGenerationOutput, OutputJudgeResult
from training_lineage import tables as t
from training_lineage.chat import ChatLineageRepository

from api_chat_support import chat_box, chat_qdrant, events, post_chat


@pytest.fixture(autouse=True)
def ephemeral_secret(monkeypatch):
    monkeypatch.setenv('API_ATTEMPT_KEY_SECRET', secrets.token_hex(32))


@pytest.mark.parametrize('chunked', [False, True])
def test_R1_body_limit_with_root_path(api, app, chunked):
    async def exercise():
        count = 0
        sent = []
        async def receive():
            nonlocal count
            count += 1
            return {'type': 'http.request', 'body': b'x' * (128 * 1024 + 1), 'more_body': count == 1}
        async def send(message):
            sent.append(message)
        headers = [(b'content-type', b'application/json')]
        if not chunked:
            headers.append((b'content-length', b'262146'))
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.3'},
                 'method': 'POST', 'path': '/api/chat', 'raw_path': b'/api/chat', 'root_path': '/api',
                 'scheme': 'https', 'http_version': '1.1', 'query_string': b'', 'headers': headers,
                 'client': ('203.0.113.10', 40000), 'server': ('testserver', 443)}
        await app(scope, receive, send)
        assert next(m['status'] for m in sent if m['type'] == 'http.response.start') == 413
        assert count == (2 if chunked else 0)
    api.client().portal.call(exercise)


def test_R3_runtime_passes_configured_timeout_to_real_qdrant(app, database, chat_qdrant, monkeypatch, tmp_path):
    from qdrant_client import QdrantClient
    import qdrant_client
    from training_api.chat_runtime import ChatRuntime
    # Clientes reais e SDK real, só chaves literais de teste; nenhuma invocação LLM.
    values = {'GEMINI_API_KEY': 'test-only-key', 'OPENROUTER_API_KEY': 'test-only-key',
              'PROMPT_SOURCE': 'offline', 'GENERATION_MODEL': 'gemini-3.1-flash-lite',
              'INPUT_JUDGE_MODEL': 'gemini/gemini-3.1-flash-lite',
              'OUTPUT_JUDGE_MODEL': 'openrouter/anthropic/claude-haiku-5.5',
              'INGESTION_JUDGE_MODEL': 'gemini/gemini-3.1-flash-lite', 'QDRANT_URL': chat_qdrant,
              'CHAT_RERANKER_CALIBRATION': str(Path('packages/retrieval/calibration/bge-v1.json').resolve())}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    timeouts = []
    def client(*args, **kwargs):
        timeouts.append(kwargs.get('timeout'))
        return QdrantClient(*args, **kwargs)
    monkeypatch.setattr(qdrant_client, 'QdrantClient', client)
    runtime = ChatRuntime(database.url, env_file=tmp_path / 'absent.env',
                          api_settings=replace(app.state.settings, chat_qdrant_timeout_seconds=7))
    try:
        assert timeouts == [7]
        assert runtime.processor.index.client.get_collections().collections
        runtime.processor.index.client.delete_collection(runtime.processor.index.collection_name)
    finally:
        runtime.close()


def test_R3_drain_beyond_deadline_logs_and_N5_clears_loop(chat_box, caplog):
    from training_api.chat_execution import ChatJob
    async def slow_external(prompt):
        await asyncio.to_thread(time.sleep, .15)
        return await original(prompt)
    original = chat_box.models.input_verdict
    chat_box.models.input_verdict = slow_external
    job = ChatJob(chat_box.factory(progress=None), chat_box.owner,
                  ChatRequest(session_id='drain', mensagem='Gere'), [chat_box.version], .02)
    def execute():
        result = job.run()
        with pytest.raises(RuntimeError, match='no current event loop'):
            asyncio.get_event_loop()
        return result
    with caplog.at_level(logging.WARNING), ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(execute).result(timeout=5)
    assert result.status.value == 'erro'
    warnings = [r for r in caplog.records if 'Drenagem' in r.getMessage() and 'prazo' in r.getMessage()]
    assert warnings and all(r.exc_info is None for r in warnings)


def test_R4_cancelled_turn_uses_timestamp_instant_not_iso_sort(chat_box, database):
    from training_api.chat_execution import ChatJob
    # Mesmo segundo: Z sem micros ordena lexicograficamente acima de .750000Z,
    # mas é temporalmente anterior ao início .500000 do turno cancelado.
    old_time = datetime(2026, 11, 27, 0, 0, 0, tzinfo=timezone.utc)
    current_time = old_time.replace(microsecond=750000)
    for identifier, created in [('old-error', old_time), ('current-error', current_time)]:
        response = ChatResponse(status='erro', mensagem=identifier, chat_turn_id=identifier, trace_id='trace-' + identifier)
        turn = ChatTurnRecord(chat_turn_id=identifier, session_id='cancel-boundary', user_id=chat_box.owner,
            prompt='Gere', status='erro', trace_id=response.trace_id, created_at=created)
        with database.begin() as connection:
            ChatLineageRepository(connection).save_turn(turn, None, None, [], [], [], response)
    job = ChatJob(chat_box.factory(progress=None), chat_box.owner,
                  ChatRequest(session_id='cancel-boundary', mensagem='Gere'), [chat_box.version], 1)
    job.started = old_time.replace(microsecond=500000)
    result = job.error_response(cancelled=True)
    assert result.chat_turn_id == 'current-error'
    with database.connect() as connection:
        assert len(connection.execute(select(t.chat_turn)).all()) == 2


def test_R5_lineage_cleaning_covers_nested_text_and_preserves_original():
    from training_api.chat_routes import lineage_output
    raw = {'lineage': {'item_id': 'unchanged-id', 'content': {'back': '<b>ab\u202ecd</b>',
        'options': ['x\u202ey'], 'citations': [{'evidence': 'a\u202eb'}]},
        'citations': [{'evidence': 'a\u202eb', 'section_path': ['A\u202eB']}]},
        'turn': {'prompt': 'p\u202eq'}, 'response': {'omissoes': [{'detalhe': 'd\u202ee'}],
        'fontes': [{'section_path': ['s\u202et']}]}}
    before = deepcopy(raw)
    result = lineage_output(raw)
    assert '\u202e' not in json.dumps(result, ensure_ascii=False)
    assert result['lineage']['content']['back'] == '<b>abcd</b>'
    assert result['response']['fontes'][0]['section_path'] == ['st']
    assert result['lineage']['item_id'] == 'unchanged-id'
    assert raw == before


def test_R5_http_lineage_removes_bidi_without_changing_persisted(api, chat_box, database):
    evidence = 'A membrana celular controla a entrada de substâncias.'
    async def generate(prompt, classification, plan, context, count, repair_items=None, *, item_type=None):
        chunk = context.chunks[0]
        good = dict(tipo='flashcard', front='Qual a função da membrana celular?', back=evidence + '\u202e',
                    bloom_level='lembrar', citations=[dict(chunk_ref=chunk.ref, evidence=evidence)])
        bad = dict(tipo='flashcard', front='Como ajustar o foco de uma lente?', back='O ajuste depende da distância focal.',
                   bloom_level='lembrar', citations=[dict(chunk_ref=chunk.ref, evidence=evidence)])
        return RawGenerationOutput(itens=[bad] if repair_items else [good, bad])
    async def judge(items, context):
        return OutputJudgeResult(vereditos=[dict(item_index=i, fundamentado=item.front != 'Como ajustar o foco de uma lente?',
            conforme_rubrica=True, motivo='observa\u202eção' if item.front == 'Como ajustar o foco de uma lente?' else None)
            for i, item in enumerate(items)])
    chat_box.models.generate, chat_box.models.judge = generate, judge
    final = events(post_chat(api, chat_box, message='Gere um flashcard\u202e'))[-1][1]
    assert final['status'] == 'ok' and len(final['itens']) == len(final['omissoes']) == 1
    item_id, turn_id = final['itens'][0]['item_id'], final['chat_turn_id']
    def snapshot():
        with database.connect() as connection:
            return (connection.execute(select(t.chat_item.c.record).where(t.chat_item.c.item_id == item_id)).scalar_one(),
                    connection.execute(select(t.chat_turn.c.record).where(t.chat_turn.c.chat_turn_id == turn_id)).scalar_one(),
                    connection.execute(select(t.chat_turn.c.response).where(t.chat_turn.c.chat_turn_id == turn_id)).scalar_one())
    before = snapshot()
    # Registros legítimos e imutáveis, criados pelo domínio: nenhum UPDATE ou
    # bypass da proveniência para montar a fixture.
    assert '\u202e' in before[0]['content']['back']
    assert '\u202e' in before[1]['prompt']
    assert '\u202e' in before[2]['omissoes'][0]['detalhe']
    for path in [f'/lineage/items/{item_id}', f'/lineage/turns/{turn_id}']:
        response = api.request('GET', path, session=chat_box.session)
        assert response.status_code == 200
        assert '\u202e' not in json.dumps(response.json(), ensure_ascii=False)
    assert snapshot() == before

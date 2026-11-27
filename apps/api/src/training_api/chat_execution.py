"""Admissão limitada e execução do domínio num loop próprio, fora do ASGI."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import logging
from threading import Lock
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, text
from training_contracts import ChatResponse, ChatTurnRecord
from training_lineage import tables as t
from training_lineage.chat import ChatLineageRepository
from training_observability import chat_turn_span, get_current_trace_id

log = logging.getLogger(__name__)


class Lease:
    def __init__(self, workers, owner):
        self.workers, self.owner, self.closed = workers, owner, False

    def close(self):
        with self.workers.lock:
            if not self.closed:
                self.closed = True
                self.workers.active[self.owner] -= 1
                if not self.workers.active[self.owner]:
                    del self.workers.active[self.owner]


class ChatWorkers:
    def __init__(self, maximum):
        self.lock = Lock()
        self.active = {}
        self.executor = ThreadPoolExecutor(max_workers=maximum, thread_name_prefix='training-chat-turn')

    def acquire(self, owner, settings):
        with self.lock:
            if self.active.get(owner, 0) >= settings.chat_max_active_per_owner:
                raise HTTPException(429, 'Turno já em execução')
            if sum(self.active.values()) >= settings.chat_max_active_global:
                raise HTTPException(503, 'Chat temporariamente ocupado')
            self.active[owner] = self.active.get(owner, 0) + 1
        return Lease(self, owner)

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)


def preflight_rate(engine, owner, settings):
    # Namespace separado: limita TODAS as requisições aceitas na borda (inclusive
    # recusa no E1), preservando sem alteração o limite do domínio por owner.
    key = 'api-chat:' + owner
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout='500ms'"))
        connection.execute(text("SET LOCAL statement_timeout='2000ms'"))
        locked = connection.execute(text('SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))'),
                                    {'key': 'chat-rate:' + key}).scalar_one()
        if not locked:
            raise HTTPException(429, 'Muitas requisições')
        now = connection.execute(select(func.clock_timestamp())).scalar_one()
        count = connection.execute(text("SELECT count(*) FROM chat_rate_event WHERE owner_id=:owner AND acquired_at > :now - :seconds * interval '1 second'"),
            {'owner': key, 'now': now, 'seconds': settings.chat_request_window_seconds}).scalar_one()
        if count >= settings.chat_max_requests_per_window:
            raise HTTPException(429, 'Muitas requisições')
        connection.execute(t.chat_rate_event.insert().values(event_id=uuid4().hex, owner_id=key, acquired_at=now))


def confirmed(engine, owner, response):
    if not response.chat_turn_id:
        return False
    with engine.connect() as connection:
        saved = connection.execute(select(t.chat_turn.c.response).where(
            t.chat_turn.c.owner_id == owner, t.chat_turn.c.chat_turn_id == response.chat_turn_id)).scalar_one_or_none()
    return saved == response.model_dump(mode='json')


class ChatJob:
    def __init__(self, pipeline, owner, body, scope, timeout_seconds):
        self.pipeline, self.owner, self.body, self.scope = pipeline, owner, body, scope
        self.timeout_seconds = timeout_seconds
        self.lock = Lock()
        self.loop = self.task = None
        self.cancel_requested = False
        self.started = datetime.now(timezone.utc)

    def cancel(self):
        with self.lock:
            if self.cancel_requested:
                return
            self.cancel_requested = True
            if self.loop is not None and not self.loop.is_closed() and self.task is not None:
                self.loop.call_soon_threadsafe(self.task.cancel)

    def error_response(self, *, cancelled=False):
        response = ChatResponse(status='erro', mensagem='Não foi possível concluir o turno.')
        try:
            # CancelledError já é gravado pelo domínio: preserva o turno confirmado.
            if cancelled:
                with self.pipeline.engine.connect() as connection:
                    saved = connection.execute(select(t.chat_turn.c.response).where(
                        t.chat_turn.c.owner_id == self.owner, t.chat_turn.c.session_id == self.body.session_id,
                        t.chat_turn.c.record['created_at'].astext >= self.started.isoformat(),
                        t.chat_turn.c.record['status'].astext == 'erro')
                        .order_by(t.chat_turn.c.record['created_at'].astext.desc()).limit(1)).scalar_one_or_none()
                if saved:
                    return ChatResponse.model_validate(saved)
            turn_id = uuid4().hex
            with chat_turn_span(turn_id, user_id=self.owner, session_id=self.body.session_id,
                                tracer_provider=self.pipeline.provider):
                trace_id = get_current_trace_id()
                if trace_id is None:
                    raise RuntimeError('Tracing indisponível')
                response = response.model_copy(update={'chat_turn_id': turn_id, 'trace_id': trace_id})
                turn = ChatTurnRecord(chat_turn_id=turn_id, session_id=self.body.session_id,
                    user_id=self.owner, prompt=self.body.mensagem[:self.pipeline.generation.settings.max_input_chars],
                    status='erro', trace_id=trace_id, created_at=datetime.now(timezone.utc))
                with self.pipeline.engine.begin() as connection:
                    ChatLineageRepository(connection).save_turn(turn, None, None, [], [], [], response,
                        input_audit={'raw_length': len(self.body.mensagem), 'stored_length': len(turn.prompt),
                                     'raw_sha256': sha256(self.body.mensagem.encode('utf-8', 'surrogatepass')).hexdigest()})
            if not confirmed(self.pipeline.engine, self.owner, response):
                raise RuntimeError('Commit não confirmado')
            return response
        except Exception as exc:
            log.warning('Persistência de erro do chat categoria=%s', type(exc).__name__)
            # Sem um commit confirmado não anunciar ID nem fontes inexistentes.
            return ChatResponse(status='erro', mensagem='Não foi possível concluir o turno.')

    def run(self):
        loop = asyncio.new_event_loop()
        # As chamadas asyncio.to_thread do domínio (admissão e retrieval) usam
        # SOMENTE este executor do worker; nunca o executor do loop ASGI.
        loop.set_default_executor(ThreadPoolExecutor(max_workers=2, thread_name_prefix='training-chat-io'))
        asyncio.set_event_loop(loop)

        async def execute():
            with self.lock:
                self.loop, self.task = loop, asyncio.current_task()
                cancelled = self.cancel_requested
            try:
                if cancelled:
                    raise asyncio.CancelledError
                response = ChatResponse.model_validate(await asyncio.wait_for(self.pipeline.run(self.owner,
                    self.body.session_id, self.body.mensagem, document_version_ids=self.scope,
                    count=min(5, self.pipeline.generation.settings.max_items)), timeout=self.timeout_seconds))
                if not confirmed(self.pipeline.engine, self.owner, response):
                    return self.error_response()
                return response
            except (asyncio.CancelledError, TimeoutError):
                return self.error_response(cancelled=True)
            except Exception as exc:
                log.warning('Execução do chat categoria=%s', type(exc).__name__)
                return self.error_response()
        try:
            return loop.run_until_complete(execute())
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            # Drena inclusive retrieval já em curso antes de liberar recursos/vaga.
            loop.run_until_complete(loop.shutdown_default_executor())
            with self.lock:
                self.task = self.loop = None
            loop.close()

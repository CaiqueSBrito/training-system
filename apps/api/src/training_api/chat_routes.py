"""SSE sem conteúdo intermediário; isolamento de owner em toda leitura."""
import asyncio
import json
import logging
import math

from anyio import CancelScope, to_thread
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from sqlalchemy import select
from training_contracts import ChatRequest, ChatResponse, ChatTurnRecord
from training_lineage import tables as t
from training_lineage.chat import ChatLineageRepository

from .auth import current_owner_id
from .csrf import require_csrf
from .chat_execution import ChatJob, preflight_rate

router = APIRouter()
log = logging.getLogger(__name__)
LABELS = {f'E{i}': ('entendendo' if i < 5 else 'buscando' if i < 8 else
                    'gerando' if i == 8 else 'verificando') for i in range(1, 13)}


def frame(name, data):
    return f'event: {name}\ndata: {json.dumps(data, ensure_ascii=True, allow_nan=False)}\n\n'


def versions(engine, owner):
    with engine.connect() as connection:
        # Só versões publicadas pelo protocolo de ingestão; o domínio revalida
        # autorização/recibos/chunks antes de qualquer conteúdo deixar o banco.
        return list(connection.execute(select(t.document_version.c.document_version_id)
            .join(t.ingestion_reservation,
                  t.ingestion_reservation.c.document_version_id == t.document_version.c.document_version_id)
            .where(t.document_version.c.owner_id == owner,
                   t.ingestion_reservation.c.owner_id == owner,
                   t.ingestion_reservation.c.phase == 'ready')
            .order_by(t.document_version.c.created_at, t.document_version.c.document_version_id)).scalars())


@router.post('/chat', dependencies=[Depends(require_csrf)])
async def chat(body: ChatRequest, request: Request, owner: str = Depends(current_owner_id)):
    settings = request.app.state.settings
    if not body.session_id.strip() or len(body.session_id) > settings.chat_session_id_max_length:
        raise HTTPException(422, 'session_id inválido')
    factory = getattr(request.app.state, 'chat_pipeline_factory', None)
    if factory is None:
        raise HTTPException(503, 'Chat indisponível: verifique a configuração dos componentes')
    workers = request.app.state.chat_workers
    lease = workers.acquire(owner, settings)
    queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def progress(record):
        loop.call_soon_threadsafe(queue.put_nowait, dict(record))

    try:
        pipeline = factory(progress=progress)
        # Canal adicional só da API: a CLI preserva progress agregado E5_E7.
        pipeline.stage_progress = progress
        def prepare():
            preflight_rate(pipeline.engine, owner, settings)
            return versions(pipeline.engine, owner)
        scope = await loop.run_in_executor(workers.executor, prepare)
    except BaseException as exc:
        lease.close()
        if isinstance(exc, (HTTPException, asyncio.CancelledError)):
            raise
        log.warning('Composição ou escopo do chat categoria=%s', type(exc).__name__)
        raise HTTPException(503, 'Chat indisponível') from None

    async def stream():
        job = ChatJob(pipeline, owner, body, scope, settings.chat_turn_timeout_seconds)
        try:
            future = workers.executor.submit(job.run)
        except Exception as exc:
            log.warning('Início do chat categoria=%s', type(exc).__name__)
            lease.close()
            yield frame('final', ChatResponse(status='erro', mensagem='Não foi possível concluir o turno.').model_dump(mode='json'))
            return
        task = asyncio.wrap_future(future)
        future.add_done_callback(lambda _: loop.call_soon_threadsafe(queue.put_nowait, None))
        pending, emitted, sequence = {}, set(), 0
        deadline = loop.time() + settings.chat_turn_timeout_seconds

        def ready(final=False):
            nonlocal sequence
            output = []
            for stage, label in LABELS.items():
                if stage in emitted:
                    continue
                if not final and (stage not in pending or int(stage[1:]) >= 8):
                    break
                if stage not in pending:
                    continue
                sequence += 1
                emitted.add(stage)
                output.append(frame('progress', {'stage': stage, 'label': label,
                    'sequence': sequence, 'seconds': pending[stage]}))
            return output

        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    job.cancel()
                    break
                try:
                    record = await asyncio.wait_for(queue.get(), min(15, remaining))
                except TimeoutError:
                    if loop.time() >= deadline:
                        job.cancel()
                        break
                    yield ': keepalive\n\n'
                    continue
                if record is None:
                    break
                stage, seconds = record.get('stage'), record.get('seconds')
                if stage in LABELS and isinstance(seconds, (float, int)) and math.isfinite(seconds) and seconds >= 0:
                    pending[stage] = pending.get(stage, 0.) + seconds
                for event in ready():
                    yield event
            try:
                response = await asyncio.shield(task)
            except Exception as exc:
                log.warning('Finalização do chat categoria=%s', type(exc).__name__)
                response = ChatResponse(status='erro', mensagem='Não foi possível concluir o turno.')
            # No timeout, callbacks de cancelamento/commit chegam durante a
            # espera protegida pelo worker. Drena-os antes de ordenar o final.
            while not queue.empty():
                record = queue.get_nowait()
                if record is None:
                    continue
                stage, seconds = record.get('stage'), record.get('seconds')
                if stage in LABELS and isinstance(seconds, (float, int)) and math.isfinite(seconds) and seconds >= 0:
                    pending[stage] = pending.get(stage, 0.) + seconds
            for event in ready(final=True):
                yield event
            yield frame('final', response.model_dump(mode='json'))
        finally:
            if not task.done():
                job.cancel()
            with CancelScope(shield=True):
                try:
                    try:
                        await asyncio.shield(task)
                    except Exception as exc:
                        log.warning('Limpeza do chat categoria=%s', type(exc).__name__)
                finally:
                    lease.close()

    raw = stream()
    class ManagedGenerator:
        def __aiter__(self):
            return self

        async def __anext__(self):
            return await raw.__anext__()

        async def aclose(self):
            with CancelScope(shield=True):
                try:
                    await raw.aclose()
                finally:
                    lease.close()
    gen = ManagedGenerator()
    return StreamingResponse(gen, media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
        background=BackgroundTask(gen.aclose))


def item_lineage(engine, owner, item_id):
    with engine.connect() as connection:
        return ChatLineageRepository(connection).get_item_lineage(owner, item_id)


def turn_lineage(engine, owner, turn_id):
    with engine.connect() as connection:
        row = connection.execute(select(t.chat_turn.c.record, t.chat_turn.c.response).where(
            t.chat_turn.c.owner_id == owner, t.chat_turn.c.chat_turn_id == turn_id)).mappings().first()
        if row is None:
            return None
        response = ChatResponse.model_validate(row['response'])
        repo = ChatLineageRepository(connection)
        return {'turn': ChatTurnRecord.model_validate(row['record']), 'response': response,
                'items': [repo.get_item_lineage(owner, item.item_id) for item in response.itens]}


@router.get('/lineage/items/{item_id}')
async def get_item(item_id: str, request: Request, response: Response, owner: str = Depends(current_owner_id)):
    response.headers['Cache-Control'] = 'no-store'
    result = await to_thread.run_sync(item_lineage, request.app.state.lineage_engine, owner, item_id)
    if result is None:
        raise HTTPException(404, 'Not Found')
    return jsonable_encoder(result)


@router.get('/lineage/turns/{chat_turn_id}')
async def get_turn(chat_turn_id: str, request: Request, response: Response, owner: str = Depends(current_owner_id)):
    response.headers['Cache-Control'] = 'no-store'
    result = await to_thread.run_sync(turn_lineage, request.app.state.lineage_engine, owner, chat_turn_id)
    if result is None:
        raise HTTPException(404, 'Not Found')
    return jsonable_encoder(result)

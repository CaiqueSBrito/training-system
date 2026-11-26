"""SSE sem conteúdo intermediário; isolamento de owner em toda leitura."""
import asyncio
import json
import logging
import math
from contextlib import suppress

from anyio import CancelScope, to_thread
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from training_contracts import ChatRequest, ChatResponse, ChatTurnRecord
from training_lineage import tables as t
from training_lineage.chat import ChatLineageRepository

from .auth import current_owner_id
from .csrf import require_csrf

router = APIRouter()
log = logging.getLogger(__name__)
LABELS = {f'E{i}': ('entendendo' if i < 5 else 'buscando' if i < 8 else
                    'gerando' if i == 8 else 'verificando') for i in range(1, 13)}


def frame(name, data):
    return f'event: {name}\ndata: {json.dumps(data, ensure_ascii=False, allow_nan=False)}\n\n'


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
    factory = getattr(request.app.state, 'chat_pipeline_factory', None)
    if factory is None:
        raise HTTPException(503, 'Chat indisponível: verifique a configuração dos componentes')
    queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def progress(record):
        # Contextos de recuperação em thread não acessam diretamente asyncio.Queue.
        loop.call_soon_threadsafe(queue.put_nowait, dict(record))

    try:
        pipeline = factory(progress=progress)
        scope = await to_thread.run_sync(versions, pipeline.engine, owner)
    except Exception:
        log.warning('Composição ou escopo do chat indisponível')
        raise HTTPException(503, 'Chat indisponível') from None

    async def stream():
        # Criada dentro do gerador: até uma desconexão antes da primeira iteração
        # não deixa produtor órfão. Auth/CSRF já confirmaram suas transações.
        async def produce():
            try:
                return await pipeline.run(owner, body.session_id, body.mensagem,
                                          document_version_ids=scope)
            finally:
                # call_soon permite aos callbacks anteriores enfileirados pela
                # thread/loop chegar antes do marcador de término.
                loop.call_soon(queue.put_nowait, None)

        task = asyncio.create_task(produce())
        pending = {}
        emitted = set()
        sequence = 0

        def collect(record):
            stage, seconds = record.get('stage'), record.get('seconds')
            if stage in LABELS and isinstance(seconds, (float, int)) and math.isfinite(seconds) and seconds >= 0:
                pending[stage] = pending.get(stage, 0.) + seconds

        def ready(final=False):
            nonlocal sequence
            output = []
            for stage, label in LABELS.items():
                if stage in emitted:
                    continue
                # E8–E12 aguardam o fim: reparos voltam a E8/E9/E10.
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
                try:
                    record = await asyncio.wait_for(queue.get(), 15)
                except TimeoutError:
                    yield ': keepalive\n\n'
                    continue
                if record is None:
                    break
                collect(record)
                for event in ready():
                    yield event
            response = ChatResponse.model_validate(await task)
            for event in ready(final=True):
                yield event
            yield frame('final', response.model_dump(mode='json'))
        finally:
            if not task.done():
                task.cancel()
            # Starlette cancela o escopo do gerador após http.disconnect; a task
            # recebe apenas o cancel explícito acima e pode persistir o erro.
            with CancelScope(shield=True):
                with suppress(asyncio.CancelledError):
                    await task

    return StreamingResponse(stream(), media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


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
async def get_item(item_id: str, request: Request, owner: str = Depends(current_owner_id)):
    result = await to_thread.run_sync(item_lineage, request.app.state.lineage_engine, owner, item_id)
    if result is None:
        raise HTTPException(404, 'Not Found')
    return jsonable_encoder(result)


@router.get('/lineage/turns/{chat_turn_id}')
async def get_turn(chat_turn_id: str, request: Request, owner: str = Depends(current_owner_id)):
    result = await to_thread.run_sync(turn_lineage, request.app.state.lineage_engine, owner, chat_turn_id)
    if result is None:
        raise HTTPException(404, 'Not Found')
    return jsonable_encoder(result)

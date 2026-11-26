"""Janelas persistidas e locks transacionais por chave, válidos entre processos."""

from contextlib import asynccontextmanager
from hashlib import sha256

from fastapi import HTTPException, Request
from sqlalchemy import func, insert, select, text
from training_lineage.tables import auth_attempt


def digest(value: str) -> str:
    return sha256(value.encode('utf-8')).hexdigest()


def origin_key(request: Request) -> str:
    # Não confia em X-Forwarded-For; o ASGI/proxy configurado define client.host.
    return digest(request.client.host if request.client else '')


async def lock(session, kind, scope, key):
    lock_id = int.from_bytes(sha256(f'auth:{kind}:{scope}:{key}'.encode()).digest()[:8],
                             'big', signed=True)
    await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock_id})


async def check(session, kind, scope, key, window, maximum):
    count = await session.scalar(select(func.count()).select_from(auth_attempt).where(
        auth_attempt.c.kind == kind, auth_attempt.c.scope == scope,
        auth_attempt.c.key == key,
        auth_attempt.c.created_at > func.clock_timestamp() - func.make_interval(0, 0, 0, 0, 0, 0, window),
    ))
    if count >= maximum:
        raise HTTPException(429, 'Too Many Requests')


async def record(session, kind, scope, key):
    await session.execute(insert(auth_attempt).values(
        kind=kind, scope=scope, key=key, created_at=func.clock_timestamp(),
    ))


async def register_attempt(request: Request):
    settings = request.app.state.settings
    key = origin_key(request)
    # Commit separado preserva a tentativa inclusive quando validação/cadastro falham.
    async with request.app.state.auth_sessions.begin() as session:
        await lock(session, 'register', 'origin', key)
        await check(session, 'register', 'origin', key,
                    settings.register_window_seconds, settings.register_max_per_origin)
        await record(session, 'register', 'origin', key)


@asynccontextmanager
async def login_attempt(request: Request, email: str):
    settings = request.app.state.settings
    account, origin = digest(email.strip().lower()), origin_key(request)
    async with request.app.state.auth_sessions.begin() as session:
        # Ordem única; as duas chaves permanecem travadas até gravar o resultado.
        await lock(session, 'login_failure', 'account', account)
        await lock(session, 'login_failure', 'origin', origin)
        for scope, key, maximum in (
            ('account', account, settings.login_max_failures_per_account),
            ('origin', origin, settings.login_max_failures_per_origin),
        ):
            await check(session, 'login_failure', scope, key, settings.login_window_seconds, maximum)
        yield session, account, origin

"""Reservas curtas persistidas: nenhuma trava ou conexão atravessa o Argon2."""

from hashlib import sha256
import hmac
from ipaddress import ip_address, ip_network

from fastapi import HTTPException, Request
from sqlalchemy import delete, func, insert, select, text, or_, and_
from training_lineage.tables import auth_access_token, auth_attempt


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def attempt_key(request: Request, value: str) -> str:
    return hmac.new(request.app.state.settings.attempt_key_secret.encode(), value.encode(), 'sha256').hexdigest()


def origin_key(request: Request) -> str:
    if request.client is None:
        raise HTTPException(403, 'Client origin required')
    try:
        address = ip_address(request.client.host)
        # IPv4 mapeado em IPv6 deve compartilhar o orçamento do IPv4 original.
        if address.version == 6 and address.ipv4_mapped:
            address = address.ipv4_mapped
        network = ip_network(f'{address}/{64 if address.version == 6 else 32}', strict=False)
    except ValueError:
        raise HTTPException(403, 'Invalid client origin') from None
    return attempt_key(request, str(network))


def seconds_ago(seconds):
    return func.clock_timestamp() - func.make_interval(0, 0, 0, 0, 0, 0, seconds)


async def purge_expired(session, settings):
    """Purga oportunística, transacional, nas tentativas e requisições autenticadas."""
    await session.execute(delete(auth_attempt).where(or_(
        and_(auth_attempt.c.kind == 'login_failure', auth_attempt.c.created_at <= seconds_ago(settings.login_window_seconds)),
        and_(auth_attempt.c.kind == 'register', auth_attempt.c.created_at <= seconds_ago(settings.register_window_seconds)),
    )))
    await session.execute(delete(auth_access_token).where(or_(
        auth_access_token.c.created_at <= seconds_ago(settings.session_lifetime_seconds),
        auth_access_token.c.last_seen_at <= seconds_ago(settings.session_idle_seconds),
    )))


async def lock(session, kind, scope, key):
    lock_id = int.from_bytes(sha256(f'auth:{kind}:{scope}:{key}'.encode()).digest()[:8], 'big', signed=True)
    acquired = await session.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': lock_id})
    if not acquired:
        raise HTTPException(429, 'Too Many Requests')


async def check(session, kind, scope, key, window, maximum):
    count = await session.scalar(select(func.count()).select_from(auth_attempt).where(
        auth_attempt.c.kind == kind, auth_attempt.c.scope == scope,
        auth_attempt.c.key == key, auth_attempt.c.created_at > seconds_ago(window),
    ))
    if count >= maximum:
        raise HTTPException(429, 'Too Many Requests')


async def record(session, kind, scope, key):
    return await session.scalar(insert(auth_attempt).values(
        kind=kind, scope=scope, key=key, created_at=func.clock_timestamp(),
    ).returning(auth_attempt.c.id))


async def register_attempt(request: Request):
    settings = request.app.state.settings
    key = origin_key(request)
    async with request.app.state.auth_sessions.begin() as session:
        await lock(session, 'register', 'origin', key)
        await purge_expired(session, settings)
        await check(session, 'register', 'origin', key, settings.register_window_seconds, settings.register_max_per_origin)
        await record(session, 'register', 'origin', key)


async def reserve_login(request: Request, email: str):
    settings = request.app.state.settings
    origin = origin_key(request)
    async with request.app.state.auth_sessions.begin() as session:
        # Mesma função/collation usada pela busca e pelo índice único de app_user.
        normalized = await session.scalar(select(func.lower(email)))
        account = attempt_key(request, normalized)
        await lock(session, 'login_failure', 'account', account)
        await lock(session, 'login_failure', 'origin', origin)
        await purge_expired(session, settings)
        reservations = []
        for scope, key, maximum in (
            ('account', account, settings.login_max_failures_per_account),
            ('origin', origin, settings.login_max_failures_per_origin),
        ):
            await check(session, 'login_failure', scope, key, settings.login_window_seconds, maximum)
            reservations.append(await record(session, 'login_failure', scope, key))
    return reservations


async def release_login(request: Request, reservations):
    async with request.app.state.auth_sessions.begin() as session:
        await session.execute(delete(auth_attempt).where(auth_attempt.c.id.in_(reservations)))

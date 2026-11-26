"""Reservas curtas persistidas: nenhuma trava ou conexão atravessa o Argon2."""

from hashlib import sha256
import hmac
from ipaddress import ip_address, ip_network
from time import monotonic

from fastapi import HTTPException, Request
from sqlalchemy import delete, func, insert, select, text
from training_lineage.tables import auth_attempt


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


async def purge_expired(session, settings, batch_size=100, time_budget_seconds=0.2):
    """Repete lotes por até ~200 ms; ocupadas são puladas, sem scan externo por IN."""
    deadline = monotonic() + time_budget_seconds
    jobs = [
        (text("""
            DELETE FROM auth_attempt WHERE ctid = ANY(ARRAY(
                SELECT ctid FROM auth_attempt
                WHERE (kind='login_failure' AND created_at <= clock_timestamp()-make_interval(secs => :login_window))
                   OR (kind='register' AND created_at <= clock_timestamp()-make_interval(secs => :register_window))
                LIMIT :batch_size FOR UPDATE SKIP LOCKED
            ))
        """), {'login_window': settings.login_window_seconds,
                'register_window': settings.register_window_seconds, 'batch_size': batch_size}),
        (text("""
            DELETE FROM auth_access_token WHERE ctid = ANY(ARRAY(
                SELECT ctid FROM auth_access_token
                WHERE created_at <= clock_timestamp()-make_interval(secs => :lifetime)
                   OR last_seen_at <= clock_timestamp()-make_interval(secs => :idle)
                LIMIT :batch_size FOR UPDATE SKIP LOCKED
            ))
        """), {'lifetime': settings.session_lifetime_seconds,
                'idle': settings.session_idle_seconds, 'batch_size': batch_size}),
    ]
    # Alterna tabelas; só repete aquela cujo lote veio completo.
    while jobs and monotonic() < deadline:
        full_batches = []
        for statement, parameters in jobs:
            if monotonic() >= deadline:
                return
            result = await session.execute(statement, parameters)
            if result.rowcount == batch_size:
                full_batches.append((statement, parameters))
        jobs = full_batches


async def maybe_purge_expired(request: Request):
    # Marca antes do await: não há fila de purgas concorrentes por requisição.
    # O lock local também cobre apps exercitadas por mais de um loop/thread.
    with request.app.state.auth_purge_lock:
        now = monotonic()
        if now < request.app.state.next_auth_purge:
            return
        request.app.state.next_auth_purge = now + 60
    async with request.app.state.auth_sessions.begin() as session:
        await purge_expired(session, request.app.state.settings)


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
    await maybe_purge_expired(request)
    async with request.app.state.auth_sessions.begin() as session:
        await lock(session, 'register', 'origin', key)
        await check(session, 'register', 'origin', key, settings.register_window_seconds, settings.register_max_per_origin)
        await record(session, 'register', 'origin', key)


async def reserve_login(request: Request, email: str):
    settings = request.app.state.settings
    origin = origin_key(request)
    await maybe_purge_expired(request)
    async with request.app.state.auth_sessions.begin() as session:
        # Mesma função/collation usada pela busca e pelo índice único de app_user.
        normalized = await session.scalar(select(func.lower(email)))
        account = attempt_key(request, normalized)
        await lock(session, 'login_failure', 'account', account)
        await lock(session, 'login_failure', 'origin', origin)
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

"""Adaptador HTTP da autenticação própria (ADR-0017)."""

from contextlib import asynccontextmanager
from threading import Lock

from anyio import CancelScope

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users import exceptions
from fastapi_users.authentication.strategy.db import DatabaseStrategy
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .auth import (
    SESSION_COOKIE, PasswordBudget, PasswordBudgetExceeded, UserCreate, UserManager, UserRead, cookie_transport,
    current_user, get_strategy, get_user_manager,
)
from .config import ApiSettings
from .csrf import require_csrf
from .database import AccessToken, User, get_session
from .limits import release_login, reserve_login, register_attempt
from .browser import require_same_origin


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    current_password: str
    new_password: str


def invalid_password(exc):
    return HTTPException(400, {'code': 'REGISTER_INVALID_PASSWORD', 'reason': exc.reason})


def create_app(settings: ApiSettings) -> FastAPI:
    url = make_url(settings.database_url)
    if url.drivername != 'postgresql+psycopg':
        raise ValueError('A API exige postgresql+psycopg')
    engine = create_async_engine(
        url, pool_pre_ping=True, hide_parameters=True,
        pool_size=5, max_overflow=5,
        connect_args={'connect_timeout': 5, 'options': '-c timezone=UTC'},
    )

    @asynccontextmanager
    async def lifespan(app):
        from anyio import to_thread
        from .chat_runtime import ChatRuntime
        runtime = None
        try:
            # Auth e GET de linhagem continuam disponíveis se modelos não estiverem
            # configurados; POST /chat informa 503, sem implementação alternativa.
            if getattr(app.state, 'chat_pipeline_factory', None) is None:
                try:
                    runtime = await to_thread.run_sync(lambda: ChatRuntime(url, api_settings=app.state.settings))
                    app.state.chat_pipeline_factory = runtime.factory
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning('Chat indisponível categoria=%s', type(exc).__name__)
            yield
        finally:
            await to_thread.run_sync(app.state.chat_workers.close)
            if runtime is not None:
                await to_thread.run_sync(runtime.close)
            app.state.lineage_engine.dispose()
            await engine.dispose()

    app = FastAPI(title='Training System API', lifespan=lifespan,
                  docs_url='/docs' if settings.docs_enabled else None,
                  redoc_url='/redoc' if settings.docs_enabled else None)
    from .chat_body import ChatBodyLimit
    from .chat_execution import ChatWorkers
    app.add_middleware(ChatBodyLimit)
    app.state.chat_workers = ChatWorkers(settings.chat_max_active_global)
    app.state.lineage_engine = create_engine(url, pool_pre_ping=True, hide_parameters=True,
        connect_args={'connect_timeout': 5, 'options': '-c timezone=UTC'})
    from .chat_routes import router
    app.include_router(router)
    app.state.settings = settings
    app.state.password_budget = PasswordBudget(settings)
    app.state.auth_purge_lock = Lock()
    app.state.next_auth_purge = 0
    app.state.auth_engine = engine
    app.state.auth_sessions = async_sessionmaker(engine, expire_on_commit=False)
    transport = cookie_transport(settings)

    @app.post('/auth/register', status_code=201, response_model=UserRead,
              dependencies=[Depends(require_same_origin), Depends(register_attempt)])
    async def register(
        body: UserCreate, request: Request,
        manager: UserManager = Depends(get_user_manager),
    ):
        try:
            return await manager.create(body, safe=True, request=request)
        except exceptions.InvalidPasswordException as exc:
            raise invalid_password(exc) from None
        except exceptions.UserAlreadyExists:
            raise HTTPException(400, 'REGISTER_USER_ALREADY_EXISTS') from None
        except IntegrityError as exc:
            # O índice lower(email) também protege cadastros concorrentes.
            if getattr(exc.orig, 'sqlstate', None) == '23505':
                raise HTTPException(400, 'REGISTER_USER_ALREADY_EXISTS') from None
            raise

    @app.post('/auth/login', dependencies=[Depends(require_same_origin)])
    async def login(
        request: Request, credentials: OAuth2PasswordRequestForm = Depends(),
        manager: UserManager = Depends(get_user_manager),
        strategy: DatabaseStrategy = Depends(get_strategy),
    ):
        reservations = await reserve_login(request, credentials.username)
        overloaded = False
        try:
            user = await manager.authenticate(credentials)
            if user is None or not user.is_active:
                raise HTTPException(400, 'LOGIN_BAD_CREDENTIALS')
            token = await strategy.write_token(user)
            await manager.user_db.session.commit()
            response = JSONResponse({'csrf_token': strategy.csrf_token})
            cookie = await transport.get_login_response(token)
            response.headers['set-cookie'] = cookie.headers['set-cookie']
            response.headers['cache-control'] = 'no-store'
            return response
        except PasswordBudgetExceeded:
            overloaded = True
            raise
        finally:
            if overloaded or manager.password_verified or manager.verified_nowait:
                # Libera inclusive após NOWAIT/erro, antes de pedir outra conexão.
                with CancelScope(shield=True):
                    await manager.user_db.session.rollback()
                    await release_login(request, reservations)

    @app.get('/users/me', response_model=UserRead)
    async def me(response: Response, user: User = Depends(current_user)):
        response.headers['cache-control'] = 'no-store'
        return user

    @app.post('/auth/logout', status_code=204, dependencies=[Depends(require_csrf)])
    async def logout(
        request: Request, user: User = Depends(current_user),
        strategy: DatabaseStrategy = Depends(get_strategy),
    ):
        await strategy.destroy_token(request.cookies[SESSION_COOKIE], user)
        return await transport.get_logout_response()

    @app.post('/auth/change-password', status_code=204, dependencies=[Depends(require_csrf)])
    async def change_password(
        body: PasswordChange, request: Request,
        user: User = Depends(current_user),
        manager: UserManager = Depends(get_user_manager),
        session: AsyncSession = Depends(get_session, scope='function'),
    ):
        # A leitura da sessão não deve reter uma conexão ao reservar a tentativa.
        email, user_id = user.email, user.id
        await session.commit()
        reservations = await reserve_login(request, email)
        snapshot = await session.scalar(select(User).where(User.id == user_id)
                                        .execution_options(populate_existing=True))
        if snapshot is None or not snapshot.is_active:
            raise HTTPException(401, 'Unauthorized')
        old_hash = snapshot.hashed_password
        await session.commit()
        if len(body.current_password) > 1024:
            raise HTTPException(400, 'INVALID_CURRENT_PASSWORD')
        try:
            valid, _ = await manager.verify_password(body.current_password, old_hash)
        except PasswordBudgetExceeded:
            # Nenhuma verificação ocorreu: não é uma falha de credencial.
            with CancelScope(shield=True):
                await session.rollback()
                await release_login(request, reservations)
            raise
        if not valid:
            raise HTTPException(400, 'INVALID_CURRENT_PASSWORD')
        try:
            try:
                await manager.validate_password(body.new_password, snapshot)
            except exceptions.InvalidPasswordException as exc:
                raise invalid_password(exc) from None
            new_hash = await manager.hash_password(body.new_password)
            try:
                locked = await session.scalar(select(User).where(User.id == user_id).with_for_update(nowait=True)
                                              .execution_options(populate_existing=True))
            except DBAPIError as exc:
                if getattr(exc.orig, 'sqlstate', None) == '55P03':
                    raise HTTPException(429, 'Too Many Requests') from None
                raise
            # Revalida a sessão após a thread e o lock: outra troca pode tê-la revogado.
            strategy = await get_strategy(request, session)
            still_valid = await strategy.read_token(request.cookies[SESSION_COOKIE], manager)
            if locked is None or not locked.is_active or still_valid is None:
                raise HTTPException(401, 'Unauthorized')
            if locked.hashed_password != old_hash:
                raise HTTPException(400, 'INVALID_CURRENT_PASSWORD')
            await manager.user_db.update(locked, {'hashed_password': new_hash})
            await session.execute(delete(AccessToken).where(AccessToken.user_id == locked.id))
            await session.commit()
            return await transport.get_logout_response()
        finally:
            # Senha atual já foi verificada: 429/401 não consomem orçamento.
            with CancelScope(shield=True):
                await session.rollback()
                await release_login(request, reservations)

    return app


def app_from_env() -> FastAPI:
    return create_app(ApiSettings.from_env())

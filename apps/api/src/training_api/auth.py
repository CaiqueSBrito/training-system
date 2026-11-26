"""fastapi-users: política de senha, sessão persistida e dono autenticado."""

from uuid import UUID
import secrets

from anyio import CapacityLimiter, to_thread

from fastapi import Depends, HTTPException, Request
from fastapi_users import BaseUserManager, UUIDIDMixin, exceptions, schemas
from fastapi_users.authentication import CookieTransport
from fastapi_users.authentication.strategy.db import DatabaseStrategy
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from .limits import digest, maybe_purge_expired

from .database import AccessToken, TokenDatabase, User, UserDatabase, get_session

SESSION_COOKIE = 'training_session'


class UserRead(schemas.BaseUser[UUID]):
    pass


class UserCreate(schemas.BaseUserCreate):
    pass


class UserManager(UUIDIDMixin, BaseUserManager[User, UUID]):
    def __init__(self, database, settings, password_limiter=None):
        super().__init__(database)
        self.settings = settings
        self.password_limiter = password_limiter or CapacityLimiter(settings.password_hash_concurrency)
        self.password_verified = False

    async def validate_password(self, password, user):
        if not self.settings.password_min_length <= len(password) <= 1024:
            raise exceptions.InvalidPasswordException(
                reason=f'A senha deve ter entre {self.settings.password_min_length} e 1024 caracteres.'
            )

    async def hash_password(self, password):
        return await to_thread.run_sync(self.password_helper.hash, password, limiter=self.password_limiter)

    async def verify_password(self, password, hashed):
        return await to_thread.run_sync(self.password_helper.verify_and_update, password, hashed, limiter=self.password_limiter)

    async def create(self, user_create, safe=False, request=None):
        await self.validate_password(user_create.password, user_create)
        existing = await self.user_db.get_by_email(user_create.email)
        await self.user_db.session.commit()
        if existing is not None:
            raise exceptions.UserAlreadyExists()
        values = (user_create.create_update_dict() if safe
                  else user_create.create_update_dict_superuser())
        password = values.pop('password')
        values['hashed_password'] = await self.hash_password(password)
        user = await self.user_db.create(values)
        await self.on_after_register(user, request)
        return user

    async def authenticate(self, credentials):
        self.password_verified = False
        if len(credentials.password) > 1024:
            return None
        user = await self.user_db.get_by_email(credentials.username)
        old_hash = user.hashed_password if user else None
        # Libera a conexão antes de ocupar uma thread Argon2.
        await self.user_db.session.commit()
        if user is None:
            await self.hash_password(credentials.password)
            return None
        valid, upgraded_hash = await self.verify_password(credentials.password, old_hash)
        if not valid:
            return None
        self.password_verified = True
        try:
            locked = await self.user_db.session.scalar(
                select(User).where(User.id == user.id).with_for_update(nowait=True)
                .execution_options(populate_existing=True)
            )
        except DBAPIError as exc:
            if getattr(exc.orig, 'sqlstate', None) == '55P03':
                raise HTTPException(429, 'Too Many Requests') from None
            raise
        # Uma troca concorrente nunca permite criar sessão com a senha anterior.
        if locked is None or locked.hashed_password != old_hash:
            return None
        if upgraded_hash:
            await self.user_db.update(locked, {'hashed_password': upgraded_hash})
        return locked


class HashedDatabaseStrategy(DatabaseStrategy):
    async def write_token(self, user):
        token = secrets.token_urlsafe(32)
        self.csrf_token = secrets.token_urlsafe(32)
        await self.database.create({'token': digest(token), 'user_id': user.id,
                                    'csrf_token': digest(self.csrf_token)})
        return token


async def get_user_manager(
    request: Request, session: AsyncSession = Depends(get_session, scope='function'),
):
    return UserManager(UserDatabase(session, User), request.app.state.settings,
                       request.app.state.password_limiter)


async def get_strategy(
    request: Request, session: AsyncSession = Depends(get_session, scope='function'),
):
    return HashedDatabaseStrategy(
        TokenDatabase(session, AccessToken, request.app.state.settings),
        lifetime_seconds=request.app.state.settings.session_lifetime_seconds,
    )


def cookie_transport(settings):
    return CookieTransport(
        cookie_name=SESSION_COOKIE,
        cookie_max_age=settings.session_lifetime_seconds,
        cookie_secure=settings.cookie_secure,
        cookie_httponly=True, cookie_samesite='lax', cookie_path='/',
    )


async def current_user(
    request: Request,
    manager: UserManager = Depends(get_user_manager),
    strategy: DatabaseStrategy = Depends(get_strategy),
) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    # Limita entrada antes da consulta VARCHAR e nunca tenta interpretar como JWT.
    if not token or len(token) != 43:
        raise HTTPException(401, 'Unauthorized')
    await maybe_purge_expired(request)
    user = await strategy.read_token(token, manager)
    # Fecha autenticação antes de entregar controle a rotas longas (SSE).
    await manager.user_db.session.commit()
    if user is None or not user.is_active:
        raise HTTPException(401, 'Unauthorized')
    return user


async def current_owner_id(user: User = Depends(current_user)) -> str:
    return str(user.id)

"""fastapi-users: política de senha, sessão persistida e dono autenticado."""

from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi_users import BaseUserManager, UUIDIDMixin, exceptions, schemas
from fastapi_users.authentication import CookieTransport
from fastapi_users.authentication.strategy.db import DatabaseStrategy
from sqlalchemy.ext.asyncio import AsyncSession

from .database import AccessToken, TokenDatabase, User, UserDatabase, get_session

SESSION_COOKIE = 'training_session'


class UserRead(schemas.BaseUser[UUID]):
    pass


class UserCreate(schemas.BaseUserCreate):
    pass


class UserManager(UUIDIDMixin, BaseUserManager[User, UUID]):
    def __init__(self, database, settings):
        super().__init__(database)
        self.settings = settings

    async def validate_password(self, password, user):
        if len(password) < self.settings.password_min_length:
            raise exceptions.InvalidPasswordException(
                reason=f'A senha deve ter pelo menos {self.settings.password_min_length} caracteres.'
            )


async def get_user_manager(
    request: Request, session: AsyncSession = Depends(get_session, scope='function'),
):
    return UserManager(UserDatabase(session, User), request.app.state.settings)


async def get_strategy(
    request: Request, session: AsyncSession = Depends(get_session, scope='function'),
):
    return DatabaseStrategy(
        TokenDatabase(session, AccessToken),
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
    user = await strategy.read_token(token, manager)
    if user is None or not user.is_active:
        raise HTTPException(401, 'Unauthorized')
    return user


async def current_owner_id(user: User = Depends(current_user)) -> str:
    return str(user.id)

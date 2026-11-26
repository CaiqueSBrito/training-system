"""Modelos da autenticação e adaptadores transacionais do fastapi-users."""

from uuid import uuid4

from fastapi import Request
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase
from sqlalchemy import func, select, update
from sqlalchemy.orm import DeclarativeBase
from training_lineage.tables import app_user, auth_access_token

from .limits import digest, purge_expired, seconds_ago


class Base(DeclarativeBase):
    pass


class User(Base):
    __table__ = app_user

    def __init__(self, **kwargs):
        kwargs.setdefault('id', uuid4())
        super().__init__(**kwargs)


class AccessToken(Base):
    __table__ = auth_access_token


async def get_session(request: Request):
    async with request.app.state.auth_sessions() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


class UserDatabase(SQLAlchemyUserDatabase):
    async def get_by_email(self, email):
        result = await self.session.execute(select(User).where(func.lower(User.email) == func.lower(email)))
        return result.scalar_one_or_none()

    async def create(self, create_dict):
        user = User(**create_dict)
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def update(self, user, update_dict):
        for key, value in update_dict.items():
            setattr(user, key, value)
        await self.session.flush()
        return user


class TokenDatabase(SQLAlchemyAccessTokenDatabase):
    def __init__(self, session, access_token_table, settings):
        super().__init__(session, access_token_table)
        self.settings = settings

    async def get_by_token(self, token, max_age=None):
        # Somente a borda recebe o token bruto; nenhum digest é um bearer válido.
        statement = select(AccessToken).where(
            AccessToken.token == digest(token),
            AccessToken.last_seen_at > seconds_ago(self.settings.session_idle_seconds),
            AccessToken.created_at > seconds_ago(self.settings.session_lifetime_seconds),
        )
        if max_age is not None:
            statement = statement.where(AccessToken.created_at > max_age)
        access = await self.session.scalar(statement)
        if access is not None:
            await self.session.execute(update(AccessToken).where(
                AccessToken.token == access.token,
                AccessToken.last_seen_at <= seconds_ago(60),
            ).values(last_seen_at=func.clock_timestamp()).execution_options(synchronize_session=False))
            await purge_expired(self.session, self.settings)
        return access

    async def create(self, create_dict):
        token = AccessToken(**create_dict)
        self.session.add(token)
        await self.session.flush()
        await self.session.refresh(token)
        return token

    async def delete(self, access_token):
        await self.session.delete(access_token)
        await self.session.flush()

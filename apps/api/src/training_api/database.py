"""Modelos sobre as tabelas da linhagem; transações exclusivas da autenticação."""

import secrets
from uuid import uuid4

from fastapi import Request
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase
from sqlalchemy import func, select
from sqlalchemy.orm import DeclarativeBase
from training_lineage.tables import app_user, auth_access_token


class Base(DeclarativeBase):
    pass


class User(Base):
    __table__ = app_user

    def __init__(self, **kwargs):
        kwargs.setdefault('id', uuid4())
        super().__init__(**kwargs)


class AccessToken(Base):
    __table__ = auth_access_token

    def __init__(self, **kwargs):
        kwargs.setdefault('csrf_token', secrets.token_urlsafe(32))
        super().__init__(**kwargs)


async def get_session(request: Request):
    async with request.app.state.auth_sessions() as session:
        async with session.begin():
            yield session


class UserDatabase(SQLAlchemyUserDatabase):
    """A unidade de trabalho externa faz commit, inclusive hash + revogação."""

    async def get_by_email(self, email):
        # Login e troca de senha serializam na conta, impedindo sessão com hash antigo.
        result = await self.session.execute(
            select(User).where(func.lower(User.email) == func.lower(email)).with_for_update()
        )
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
    async def create(self, create_dict):
        token = AccessToken(**create_dict)
        self.session.add(token)
        await self.session.flush()
        await self.session.refresh(token)
        return token

    async def delete(self, access_token):
        await self.session.delete(access_token)
        await self.session.flush()

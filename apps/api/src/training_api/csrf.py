"""CSRF de sessão: autenticação obrigatória antes da comparação constante."""

import secrets

from fastapi import Depends, HTTPException, Request
from fastapi_users.authentication.strategy.db import DatabaseStrategy

from .auth import SESSION_COOKIE, current_user, get_strategy
from .database import User
from .limits import digest


async def require_csrf(
    request: Request,
    user: User = Depends(current_user),
    strategy: DatabaseStrategy = Depends(get_strategy),
) -> None:
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
        return
    token = await strategy.database.get_by_token(request.cookies[SESSION_COOKIE])
    await strategy.database.session.commit()
    supplied = request.headers.get('X-CSRF-Token', '')
    if (token is None or not supplied or len(supplied) > 64
            or not secrets.compare_digest(digest(supplied), token.csrf_token)):
        raise HTTPException(403, 'CSRF token inválido')

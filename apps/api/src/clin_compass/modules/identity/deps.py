"""Публичные зависимости FastAPI модуля identity: текущий пользователь и проверка роли."""

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response

from clin_compass.core.deps import DbSession
from clin_compass.modules.identity import service
from clin_compass.modules.identity.schemas import Role, UserOut

SESSION_COOKIE = "compass_session"


def set_session_cookie(response: Response, token: str, *, ttl: timedelta, secure: bool) -> None:
    """Cookie сессии: httpOnly, SameSite=Lax; Secure — на стенде (HTTPS)."""
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


async def get_current_user(request: Request, session: DbSession) -> UserOut:
    token = request.cookies.get(SESSION_COOKIE)
    user = await service.resolve_session(session, token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


CurrentUser = Annotated[UserOut, Depends(get_current_user)]


def require_role(*roles: Role) -> Callable[[UserOut], Awaitable[UserOut]]:
    """Зависимость: пользователь вошёл и имеет одну из ролей, иначе 403."""

    async def _check(user: CurrentUser) -> UserOut:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Forbidden for this role")
        return user

    return _check


DoctorUser = Annotated[UserOut, Depends(require_role(Role.DOCTOR))]
PatientUser = Annotated[UserOut, Depends(require_role(Role.PATIENT))]
AdminUser = Annotated[UserOut, Depends(require_role(Role.ADMIN))]

"""HTTP-роутер модуля identity: вход, выход, текущий пользователь."""

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from clin_compass.core.deps import AppSettings, DbSession
from clin_compass.core.errors import PROBLEM_JSON, RATE_LIMITED_RESPONSE, ProblemOut, problem
from clin_compass.modules.identity import service
from clin_compass.modules.identity.deps import SESSION_COOKIE, CurrentUser, set_session_cookie
from clin_compass.modules.identity.schemas import LoginRequest, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


_PROBLEM_CONTENT = {PROBLEM_JSON: {"schema": ProblemOut.model_json_schema()}}


@router.post(
    "/login",
    response_model=UserOut,
    responses={
        **RATE_LIMITED_RESPONSE,
        401: {"description": "Invalid email or password", "content": _PROBLEM_CONTENT},
        403: {
            "description": "Password is correct, but sign-in is closed; `code` says why",
            "content": _PROBLEM_CONTENT,
        },
    },
)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
) -> UserOut | JSONResponse:
    host = request.client.host if request.client else None
    # Лимит проверяется до argon2: 429 не тратит процессор и не говорит, верен ли пароль.
    await service.check_login(session, settings, email=body.email, host=host)
    try:
        user = await service.authenticate(session, body.email, body.password)
    except service.UserDisabledError as error:
        # Пароль верен, поэтому это не неудача для лимита. И не успешный вход:
        # прежние неудачи пары остаются, сессия не создаётся.
        return problem(403, "Sign-in is closed for this account", code=error.reason.value)
    if user is None:
        await service.login_failed(session, settings, email=body.email, host=host)
        await session.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")
    await service.login_succeeded(session, settings, email=body.email, host=host)
    ttl = timedelta(hours=settings.session_ttl_hours)
    token = await service.create_session(session, user.id, ttl)
    await session.commit()
    set_session_cookie(response, token, ttl=ttl, secure=settings.cookie_secure)
    return user


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, session: DbSession) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await service.revoke_session(session, token)
        await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return user

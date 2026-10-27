"""Публичный интерфейс модуля identity: пользователи, сессии, журнал доступа."""

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.core import realtime, tokens
from clin_compass.core.config import Settings
from clin_compass.core.errors import RateLimitedError
from clin_compass.modules.identity import security, throttle
from clin_compass.modules.identity.models import AccessLogEntry, AuthSession, User
from clin_compass.modules.identity.schemas import ActiveSession, DisabledReason, Role, UserOut


class UserDisabledError(Exception):
    """Пароль верен, но вход пользователю закрыт (`disable_user`)."""

    def __init__(self, reason: DisabledReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


def _normalize_email(email: str) -> str:
    return email.strip().lower()


async def create_user(
    session: AsyncSession, *, email: str, password: str, role: Role, full_name: str
) -> UserOut:
    user = User(
        email=_normalize_email(email),
        password_hash=await security.hash_password(password),
        role=Role(role).value,
        full_name=full_name,
    )
    session.add(user)
    await session.flush()
    return UserOut.model_validate(user)


async def authenticate(session: AsyncSession, email: str, password: str) -> UserOut | None:
    """Пользователь по адресу и паролю или None.

    `UserDisabledError` — только при верном пароле: неверный пароль у отключённого
    пользователя даёт тот же None, что у любого другого.
    """
    user = await session.scalar(select(User).where(User.email == _normalize_email(email)))
    password_hash = user.password_hash if user is not None else None
    if not await security.verify_password(password_hash, password) or user is None:
        return None
    if user.disabled_at is not None:
        # Время без причины база не пропустит (ck_users_disabled_complete),
        # а причину пишет только `disable_user` из DisabledReason.
        raise UserDisabledError(DisabledReason(cast("str", user.disabled_reason)))
    return UserOut.model_validate(user)


async def disable_user(session: AsyncSession, user_id: UUID, reason: DisabledReason) -> None:
    """Закрывает пользователю вход. У отключённого время и причина остаются первыми.

    Действующие сессии не пускают его сразу, но не отзываются: сокеты закрывает
    `revoke_user_sessions`. Коммитит вызывающий.
    """
    await session.execute(
        update(User)
        .where(User.id == user_id, User.disabled_at.is_(None))
        .values(disabled_at=datetime.now(UTC), disabled_reason=reason.value)
    )


async def get_user(session: AsyncSession, user_id: UUID) -> UserOut | None:
    user = await session.get(User, user_id)
    return UserOut.model_validate(user) if user is not None else None


async def get_user_by_email(session: AsyncSession, email: str) -> UserOut | None:
    """Пользователь по email с той же нормализацией, что при входе."""
    user = await session.scalar(select(User).where(User.email == _normalize_email(email)))
    return UserOut.model_validate(user) if user is not None else None


async def get_users(session: AsyncSession, user_ids: list[UUID]) -> list[UserOut]:
    if not user_ids:
        return []
    users = await session.scalars(select(User).where(User.id.in_(user_ids)))
    return [UserOut.model_validate(u) for u in users]


async def list_users(session: AsyncSession, role: Role) -> list[UserOut]:
    """Пользователи роли по ФИО: врачи для назначения заместителей, администраторы (#56)."""
    users = await session.scalars(
        select(User).where(User.role == role).order_by(User.full_name, User.id)
    )
    return [UserOut.model_validate(u) for u in users]


async def create_session(session: AsyncSession, user_id: UUID, ttl: timedelta) -> str:
    """Создаёт сессию и возвращает токен. Токен показывается один раз — в БД только его хеш."""
    token = tokens.new_token()
    session.add(
        AuthSession(
            token_hash=tokens.token_hash(token),
            user_id=user_id,
            expires_at=datetime.now(UTC) + ttl,
        )
    )
    await session.flush()
    return token


async def get_active_session(session: AsyncSession, token: str) -> ActiveSession | None:
    """Сессия по токену, если она не отозвана, не истекла и вход пользователю не закрыт."""
    return await get_active_session_by_key(session, tokens.token_hash(token))


async def get_active_session_by_key(session: AsyncSession, key: str) -> ActiveSession | None:
    """Действующая сессия по уже хешированному ключу для проверки WS-доставки."""
    row = (
        await session.execute(
            select(User, AuthSession.expires_at)
            .join(AuthSession, AuthSession.user_id == User.id)
            .where(
                AuthSession.token_hash == key,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > datetime.now(UTC),
                User.disabled_at.is_(None),
            )
        )
    ).one_or_none()
    if row is None:
        return None
    user, expires_at = row
    return ActiveSession(user=UserOut.model_validate(user), key=key, expires_at=expires_at)


async def resolve_session(session: AsyncSession, token: str) -> UserOut | None:
    """Пользователь действующей сессии или None."""
    active = await get_active_session(session, token)
    return active.user if active is not None else None


async def revoke_session(session: AsyncSession, token: str) -> None:
    """Отзывает сессию. При коммите сокеты сессии закрываются на всех экземплярах api.

    Уже отозванную или неизвестную сессию не трогает: время отзыва остаётся первым.
    """
    key = tokens.token_hash(token)
    revoked = await session.scalar(
        update(AuthSession)
        .where(AuthSession.token_hash == key, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
        .returning(AuthSession.token_hash)
    )
    if revoked is not None:
        await realtime.publish_session_ended(session, key)


async def revoke_user_sessions(session: AsyncSession, user_id: UUID) -> None:
    """Отзывает все действующие сессии пользователя, как `revoke_session` каждую.

    При коммите их сокеты закрываются кодом 4401 на всех экземплярах api. Отозванные
    раньше и истёкшие сессии не трогает. Коммитит вызывающий.
    """
    now = datetime.now(UTC)
    revoked = await session.scalars(
        update(AuthSession)
        .where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
        .values(revoked_at=now)
        .returning(AuthSession.token_hash)
    )
    for key in revoked.all():
        await realtime.publish_session_ended(session, key)


async def log_access(
    session: AsyncSession,
    *,
    actor_id: UUID,
    action: str,
    patient_id: UUID | None = None,
    subject: str | None = None,
) -> None:
    """Запись в журнал доступа к данным пациента.

    Коммитит вызывающий — вместе с основным действием.
    """
    session.add(
        AccessLogEntry(actor_id=actor_id, action=action, patient_id=patient_id, subject=subject)
    )
    await session.flush()


# Лимиты частоты (спека #23). Пороги — в настройках, окна задают смысл лимита.
_LOGIN_WINDOW = timedelta(minutes=15)
_ACCOUNT_WINDOW = timedelta(days=1)
_INVITE_WINDOW = timedelta(hours=1)


def _login_keys(settings: Settings, email: str, host: str | None) -> tuple[str, str, str]:
    """HMAC пары «адрес + IP», IP и адреса. Несуществующий адрес считается как настоящий."""
    address = _normalize_email(email)
    ip = throttle.ip_key(host)
    secret = settings.throttle_key
    return (
        throttle.digest(secret, f"{address}|{ip}"),
        throttle.digest(secret, ip),
        throttle.digest(secret, address),
    )


async def check_login(
    session: AsyncSession, settings: Settings, *, email: str, host: str | None
) -> None:
    """До проверки пароля: `RateLimitedError`, если лимит входа исчерпан.

    Берёт блокировку пары «адрес + IP» до конца транзакции. Пара занята параллельным входом —
    сразу 429: ожидание держало бы соединение пула за argon2 чужого запроса.
    Лимит адреса не действует для пары, с которой недавно был успешный вход.
    """
    pair, ip, address = _login_keys(settings, email, host)
    if not await throttle.try_lock(session, pair):
        raise RateLimitedError(1, "login.lock")
    limits = [
        (throttle.Limit("login.pair", settings.login_failures_per_pair, _LOGIN_WINDOW), pair),
        (throttle.Limit("login.ip", settings.login_failures_per_ip, _LOGIN_WINDOW), ip),
    ]
    if not await throttle.seen(session, throttle.KNOWN_BUCKET, pair, throttle.KNOWN_WINDOW):
        limits.append(
            (
                throttle.Limit(
                    "login.account", settings.login_failures_per_account, _ACCOUNT_WINDOW
                ),
                address,
            )
        )
    exhausted: dict[str, int] = {}
    for limit, key in limits:
        wait = await throttle.retry_after(session, limit, key)
        if wait is not None:
            exhausted[limit.bucket] = wait
    if exhausted:
        raise RateLimitedError(max(exhausted.values()), *exhausted)


async def login_failed(
    session: AsyncSession, settings: Settings, *, email: str, host: str | None
) -> None:
    """Неудачный вход: событие в ключах пары, IP и адреса. Коммитит вызывающий."""
    for bucket, key in zip(
        ("login.pair", "login.ip", "login.account"),
        _login_keys(settings, email, host),
        strict=True,
    ):
        await throttle.record(session, bucket, key)


async def login_succeeded(
    session: AsyncSession, settings: Settings, *, email: str, host: str | None
) -> None:
    """Успешный вход стирает неудачи пары и адреса и запоминает пару как знакомую.

    Неудачи IP остаются: иначе каждый угаданный пароль из утечки обнулял бы счётчик IP.
    """
    pair, _ip, address = _login_keys(settings, email, host)
    await throttle.forget(session, "login.pair", pair)
    await throttle.forget(session, "login.account", address)
    await throttle.forget(session, throttle.KNOWN_BUCKET, pair)
    await throttle.record(session, throttle.KNOWN_BUCKET, pair)


async def check_invite_creation(session: AsyncSession, settings: Settings, doctor_id: UUID) -> None:
    """`RateLimitedError`, если врач исчерпал лимит приглашений за час."""
    wait = await throttle.retry_after(
        session,
        throttle.Limit("invite.create", settings.invites_per_doctor, _INVITE_WINDOW),
        throttle.digest(settings.throttle_key, str(doctor_id)),
    )
    if wait is not None:
        raise RateLimitedError(wait, "invite.create")


async def invite_created(session: AsyncSession, settings: Settings, doctor_id: UUID) -> None:
    """Врач создал приглашение: событие для лимита. Коммитит вызывающий."""
    await throttle.record(
        session, "invite.create", throttle.digest(settings.throttle_key, str(doctor_id))
    )


async def check_registration(
    session: AsyncSession, settings: Settings, *, host: str | None
) -> None:
    """`RateLimitedError`, если с этого IP исчерпан лимит неудачных регистраций за час."""
    wait = await throttle.retry_after(
        session,
        throttle.Limit("register.failure", settings.registration_failures_per_ip, _INVITE_WINDOW),
        throttle.digest(settings.throttle_key, throttle.ip_key(host)),
    )
    if wait is not None:
        raise RateLimitedError(wait, "register.failure")


async def registration_failed(
    session: AsyncSession, settings: Settings, *, host: str | None
) -> None:
    """Регистрация с негодным приглашением или занятым адресом. Коммитит вызывающий."""
    await throttle.record(
        session, "register.failure", throttle.digest(settings.throttle_key, throttle.ip_key(host))
    )

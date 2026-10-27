"""Postgres leases and short-lived typing state for messaging connections."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.core import realtime
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import Role, UserOut
from clin_compass.modules.messaging.models import PresenceLease, TypingState, UserPresence
from clin_compass.modules.messaging.schemas import TYPING_MIN_INTERVAL_SECONDS, MessagingStatusOut
from clin_compass.modules.patients import service as patients

Pair = tuple[UUID, UUID]
_TYPING_MIN_INTERVAL = text(f"interval '{TYPING_MIN_INTERVAL_SECONDS} seconds'")


async def touch(
    session: AsyncSession,
    actor: UserOut,
    *,
    connection_id: UUID,
    session_key: str,
    session_expires_at: datetime,
) -> None:
    """Refresh the lease after session validation, using the database clock."""
    await _lock_user(session, actor.id)
    active = await identity.get_active_session_by_key(session, session_key)
    if active is None or active.user.id != actor.id:
        return
    actor = active.user
    session_expires_at = min(session_expires_at, active.expires_at)
    await prune_expired(session, user_id=actor.id)
    was_online = await is_online(session, actor.id)
    lease = insert(PresenceLease).values(
        connection_id=connection_id,
        user_id=actor.id,
        session_key=session_key,
        lease_until=func.least(
            func.clock_timestamp() + text("interval '60 seconds'"), session_expires_at
        ),
        last_seen_at=func.clock_timestamp(),
    )
    await session.execute(
        lease.on_conflict_do_update(
            index_elements=[PresenceLease.connection_id],
            set_={
                "user_id": lease.excluded.user_id,
                "session_key": lease.excluded.session_key,
                "lease_until": lease.excluded.lease_until,
                "last_seen_at": lease.excluded.last_seen_at,
            },
        )
    )
    presence = insert(UserPresence).values(user_id=actor.id, last_seen_at=func.clock_timestamp())
    await session.execute(
        presence.on_conflict_do_update(
            index_elements=[UserPresence.user_id],
            set_={
                "last_seen_at": func.greatest(
                    UserPresence.last_seen_at, presence.excluded.last_seen_at
                )
            },
        )
    )
    if not was_online:
        for patient_id, doctor_id in await current_pairs(session, actor):
            await publish_presence(session, patient_id, doctor_id, actor.id, online=True)


async def disconnect(session: AsyncSession, actor: UserOut, *, connection_id: UUID) -> None:
    """Remove only this connection and recompute its pair's remaining state."""
    await _lock_user(session, actor.id)
    await prune_expired(session, user_id=actor.id)
    affected = list(
        (
            await session.execute(
                select(TypingState.patient_id, TypingState.doctor_id).where(
                    TypingState.connection_id == connection_id
                )
            )
        ).all()
    )
    lease = await session.get(PresenceLease, connection_id)
    if lease is None or lease.user_id != actor.id:
        return
    await session.delete(lease)
    await session.flush()

    current = set(await current_pairs(session, actor))
    for patient_id, doctor_id in set(affected) & current:
        await publish_typing(session, patient_id, doctor_id, actor.id)
    if not await is_online(session, actor.id):
        for patient_id, doctor_id in current:
            await publish_presence(session, patient_id, doctor_id, actor.id, online=False)


async def get_status(
    session: AsyncSession,
    *,
    patient_id: UUID,
    doctor_id: UUID,
    peer_id: UUID,
    peer_full_name: str,
) -> MessagingStatusOut:
    """Read the peer's unexpired leases, persistent last seen, and aggregate typing."""
    await _lock_user(session, peer_id)
    await prune_expired(session, user_id=peer_id)
    last_seen_at = await session.scalar(
        select(UserPresence.last_seen_at).where(UserPresence.user_id == peer_id)
    )
    typing_until = await session.scalar(
        select(func.max(TypingState.expires_at)).where(
            TypingState.patient_id == patient_id,
            TypingState.doctor_id == doctor_id,
            TypingState.user_id == peer_id,
            TypingState.expires_at > func.clock_timestamp(),
        )
    )
    return MessagingStatusOut(
        patient_id=patient_id,
        doctor_id=doctor_id,
        peer_full_name=peer_full_name,
        online=await is_online(session, peer_id),
        last_seen_at=last_seen_at,
        typing_until=typing_until,
    )


async def set_typing(
    session: AsyncSession,
    *,
    user_id: UUID,
    connection_id: UUID,
    session_key: str,
    patient_id: UUID,
    doctor_id: UUID,
    active: bool,
) -> None:
    """Apply the connection-scoped five-second typing lease and two-second limit."""
    await _lock_user(session, user_id)
    active_session = await identity.get_active_session_by_key(session, session_key)
    if active_session is None or active_session.user.id != user_id:
        return
    await prune_expired(session, user_id=user_id)
    live_connection = await session.scalar(
        select(PresenceLease.connection_id).where(
            PresenceLease.connection_id == connection_id,
            PresenceLease.user_id == user_id,
            PresenceLease.session_key == session_key,
            PresenceLease.lease_until > func.clock_timestamp(),
        )
    )
    if live_connection is None:
        return

    key = (
        TypingState.connection_id == connection_id,
        TypingState.patient_id == patient_id,
        TypingState.doctor_id == doctor_id,
        TypingState.user_id == user_id,
    )
    if active:
        recent = await session.scalar(
            select(TypingState.connection_id).where(
                *key,
                TypingState.last_active_at > func.clock_timestamp() - _TYPING_MIN_INTERVAL,
            )
        )
        if recent is not None:
            return
        statement = insert(TypingState).values(
            connection_id=connection_id,
            patient_id=patient_id,
            doctor_id=doctor_id,
            user_id=user_id,
            expires_at=func.clock_timestamp() + text("interval '5 seconds'"),
            last_active_at=func.clock_timestamp(),
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    TypingState.connection_id,
                    TypingState.patient_id,
                    TypingState.doctor_id,
                ],
                set_={
                    "user_id": statement.excluded.user_id,
                    "expires_at": statement.excluded.expires_at,
                    "last_active_at": statement.excluded.last_active_at,
                },
            )
        )
    else:
        removed = await session.scalar(
            update(TypingState)
            .where(*key, TypingState.expires_at > func.clock_timestamp())
            .values(expires_at=func.clock_timestamp())
            .returning(TypingState.connection_id)
        )
        if removed is None:
            return
    await publish_typing(session, patient_id, doctor_id, user_id)


async def clear_typing(
    session: AsyncSession, *, user_id: UUID, patient_id: UUID, doctor_id: UUID
) -> None:
    """Clear every tab's typing state after accepting a new message."""
    await _lock_user(session, user_id)
    removed = await session.scalar(
        update(TypingState)
        .where(
            TypingState.user_id == user_id,
            TypingState.patient_id == patient_id,
            TypingState.doctor_id == doctor_id,
            TypingState.expires_at > func.clock_timestamp(),
        )
        .values(expires_at=func.clock_timestamp())
        .returning(TypingState.connection_id)
    )
    if removed is not None:
        await publish_typing(session, patient_id, doctor_id, user_id)


async def can_deliver(
    session: AsyncSession, *, recipient_id: UUID, patient_id: UUID, doctor_id: UUID
) -> bool:
    """Allow a message event only to a participant in the pair that is current now."""
    if recipient_id not in {patient_id, doctor_id}:
        return False
    return await patients.lead_doctor_of(session, patient_id) == doctor_id


async def prune_expired(session: AsyncSession, *, user_id: UUID) -> None:
    """Remove expired technical records; their absence never erases last-seen history."""
    now = func.clock_timestamp()
    # Delete leases first: their FK cascade removes typing rows without reversing
    # the parent-child lock order used by disconnect.
    await session.execute(
        delete(PresenceLease).where(
            PresenceLease.user_id == user_id,
            PresenceLease.lease_until <= now,
        )
    )
    # Inactive rows remain briefly as the two-second rate-limit marker.
    await session.execute(
        delete(TypingState).where(
            TypingState.user_id == user_id,
            TypingState.expires_at <= func.clock_timestamp(),
            TypingState.last_active_at <= func.clock_timestamp() - _TYPING_MIN_INTERVAL,
        )
    )


async def _lock_user(session: AsyncSession, user_id: UUID) -> None:
    """Serialize presence transitions across API replicas for one user."""
    key = f"messaging:presence:{user_id}"
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))


async def is_online(session: AsyncSession, user_id: UUID) -> bool:
    return bool(
        await session.scalar(
            select(PresenceLease.connection_id)
            .where(
                PresenceLease.user_id == user_id,
                PresenceLease.lease_until > func.clock_timestamp(),
            )
            .limit(1)
        )
    )


async def current_pairs(session: AsyncSession, actor: UserOut) -> list[Pair]:
    if actor.role is Role.PATIENT:
        doctor_id = await patients.lead_doctor_of(session, actor.id)
        return [(actor.id, doctor_id)] if doctor_id is not None else []
    if actor.role is Role.DOCTOR:
        return [
            (patient.id, actor.id) for patient in await patients.list_patients(session, actor.id)
        ]
    return []


async def publish_typing(
    session: AsyncSession, patient_id: UUID, doctor_id: UUID, user_id: UUID
) -> None:
    typing_until = await session.scalar(
        select(func.max(TypingState.expires_at)).where(
            TypingState.patient_id == patient_id,
            TypingState.doctor_id == doctor_id,
            TypingState.user_id == user_id,
            TypingState.expires_at > func.clock_timestamp(),
        )
    )
    await realtime.publish(
        session,
        [patient_id, doctor_id],
        "message.typing",
        {
            "patient_id": str(patient_id),
            "doctor_id": str(doctor_id),
            "user_id": str(user_id),
            "typing_until": typing_until.isoformat() if typing_until else None,
        },
    )


async def publish_presence(
    session: AsyncSession,
    patient_id: UUID,
    doctor_id: UUID,
    user_id: UUID,
    *,
    online: bool,
) -> None:
    last_seen_at = await session.scalar(
        select(UserPresence.last_seen_at).where(UserPresence.user_id == user_id)
    )
    await realtime.publish(
        session,
        [patient_id, doctor_id],
        "message.presence",
        {
            "patient_id": str(patient_id),
            "doctor_id": str(doctor_id),
            "user_id": str(user_id),
            "online": online,
            "last_seen_at": last_seen_at.isoformat() if last_seen_at else None,
        },
    )

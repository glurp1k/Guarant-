"""Пользователи."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_config
from bot.db.models import User


async def get_or_create(session: AsyncSession, tg_id: int, username: str | None, full_name: str) -> User:
    """Найти пользователя или создать, попутно освежив юзернейм и имя."""
    user = await session.get(User, tg_id)
    normalized = (username or "").lower() or None

    if user is None:
        user = User(
            tg_id=tg_id,
            username=normalized,
            full_name=full_name[:255],
            is_admin=tg_id in get_config().admin_ids,
        )
        session.add(user)
        await session.commit()
        return user

    changed = False
    if user.username != normalized:
        user.username = normalized
        changed = True
    if user.full_name != full_name[:255]:
        user.full_name = full_name[:255]
        changed = True
    if tg_id in get_config().admin_ids and not user.is_admin:
        user.is_admin = True
        changed = True

    user.last_seen_at = datetime.now(timezone.utc)
    await session.commit()
    if changed:
        await session.refresh(user)
    return user


async def find_by_username(session: AsyncSession, username: str) -> User | None:
    """Поиск по юзернейму без учёта регистра."""
    stmt = select(User).where(func.lower(User.username) == username.lower())
    return (await session.execute(stmt)).scalar_one_or_none()


async def find_any(session: AsyncSession, query: str) -> User | None:
    """Поиск по ID или юзернейму — для админки."""
    query = query.strip().lstrip("@")
    if query.isdigit():
        user = await session.get(User, int(query))
        if user is not None:
            return user
    return await find_by_username(session, query)


async def is_admin(session: AsyncSession, tg_id: int) -> bool:
    if tg_id in get_config().admin_ids:
        return True
    user = await session.get(User, tg_id)
    return bool(user and user.is_admin)


async def admin_ids(session: AsyncSession) -> list[int]:
    """Все, кому уходят уведомления админки."""
    stmt = select(User.tg_id).where(User.is_admin.is_(True))
    rows = (await session.execute(stmt)).scalars().all()
    return sorted({*rows, *get_config().admin_ids})

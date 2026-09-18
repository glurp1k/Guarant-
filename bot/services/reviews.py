"""Отзывы о второй стороне сделки.

Оставить отзыв можно только по закрытой сделке и только один раз — это
единственное, что отличает репутацию от накрутки. Счётчики лежат прямо
у пользователя, чтобы профиль и карточка проверки не считали их запросом.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Deal, DealStatus, Review, User
from bot.services.settings import settings

log = logging.getLogger(__name__)

CLOSED_STATUSES = (DealStatus.COMPLETED.value, DealStatus.REFUNDED.value)


class ReviewError(Exception):
    """Отзыв оставить нельзя — текст показывается пользователю."""


def enabled() -> bool:
    return settings.get_bool("reviews_enabled")


def counterpart(deal: Deal, author_id: int) -> int | None:
    return deal.counterpart_id(author_id)


async def existing(session: AsyncSession, deal_id: int, author_id: int) -> Review | None:
    stmt = select(Review).where(Review.deal_id == deal_id, Review.author_id == author_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def ensure_can_leave(session: AsyncSession, deal: Deal, author: User) -> int:
    """Проверить право на отзыв и вернуть, о ком он будет."""
    if not enabled():
        raise ReviewError("Отзывы сейчас отключены")
    if deal.status not in CLOSED_STATUSES:
        raise ReviewError("Отзыв можно оставить только по закрытой сделке")

    target_id = counterpart(deal, author.tg_id)
    if target_id is None:
        raise ReviewError("Вы не участник этой сделки")
    if await existing(session, deal.id, author.tg_id) is not None:
        raise ReviewError("Вы уже оставили отзыв по этой сделке")

    return target_id


async def leave(
    session: AsyncSession,
    deal: Deal,
    author: User,
    rating: int,
    comment: str | None = None,
) -> Review:
    """Сохранить отзыв и обновить счётчики адресата."""
    target_id = await ensure_can_leave(session, deal, author)

    if settings.get_bool("reviews_comment_required") and not (comment or "").strip():
        raise ReviewError("К отзыву нужен комментарий")

    target = await session.get(User, target_id)
    if target is None:
        raise ReviewError("Вторая сторона не найдена")

    review = Review(
        deal_id=deal.id,
        author_id=author.tg_id,
        target_id=target_id,
        rating=1 if rating > 0 else -1,
        comment=(comment or "").strip()[:1000] or None,
    )
    session.add(review)

    if review.is_positive:
        target.reviews_plus += 1
    else:
        target.reviews_minus += 1

    try:
        await session.commit()
    except IntegrityError:
        # Два нажатия подряд на одну кнопку — отзыв уже есть.
        await session.rollback()
        raise ReviewError("Вы уже оставили отзыв по этой сделке") from None

    log.info("Отзыв по сделке %s: %s → %s (%+d)", deal.code, author.tg_id, target_id, review.rating)
    return review


async def for_user(session: AsyncSession, user_id: int, limit: int = 10) -> list[Review]:
    stmt = (
        select(Review)
        .where(Review.target_id == user_id)
        .order_by(Review.id.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def recount(session: AsyncSession, user: User) -> None:
    """Пересчитать счётчики из таблицы — на случай ручных правок в БД."""
    rows = (await session.execute(
        select(Review.rating).where(Review.target_id == user.tg_id)
    )).scalars().all()
    user.reviews_plus = sum(1 for rating in rows if rating > 0)
    user.reviews_minus = sum(1 for rating in rows if rating < 0)
    await session.commit()

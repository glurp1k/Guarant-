"""Отзывы после закрытой сделки."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Deal, User
from bot.keyboards import user as kb
from bot.keyboards.callbacks import ReviewCB
from bot.services import reviews as reviews_service
from bot.services.reviews import ReviewError
from bot.services.settings import settings
from bot.states import ReviewSG
from bot.utils.render import show
from bot.utils.style import block, note, title
from bot.utils.texts import esc

log = logging.getLogger(__name__)
router = Router(name="reviews")


async def offer(bot: Bot, deal: Deal, user_id: int | None) -> None:
    """Предложить одной из сторон оценить партнёра."""
    if not user_id or not reviews_service.enabled():
        return
    if not settings.get_bool("reviews_ask_after_deal"):
        return

    try:
        await bot.send_message(
            user_id,
            block(
                title(settings.get("icon_reviews"), f"Сделка {deal.code} закрыта"),
                "Оцените вторую сторону — отзыв увидят все, кто будет её проверять.",
            ),
            reply_markup=kb.review_ask(deal.id),
        )
    except Exception as exc:  # noqa: BLE001 - пользователь мог заблокировать бота
        log.debug("Предложение отзыва не доставлено %s: %s", user_id, exc)


async def offer_both(bot: Bot, deal: Deal) -> None:
    await offer(bot, deal, deal.seller_id)
    await offer(bot, deal, deal.buyer_id)


async def _save(call: CallbackQuery, session: AsyncSession, user: User,
                deal: Deal, value: str, comment: str | None) -> None:
    try:
        review = await reviews_service.leave(session, deal, user, 1 if value == "+" else -1, comment)
    except ReviewError as exc:
        await show(call, f"❌ {exc}", kb.back_only())
        return

    mark = "👍 положительный" if review.is_positive else "👎 отрицательный"
    await show(call, block(
        title("✅", "Отзыв сохранён"),
        f"Оценка: {mark}",
        note("", f"Комментарий: {esc(comment)}" if comment else ""),
    ), kb.back_only())


@router.callback_query(ReviewCB.filter(F.action == "rate"))
async def rate(call: CallbackQuery, callback_data: ReviewCB, session: AsyncSession,
               user: User, state: FSMContext) -> None:
    deal = await session.get(Deal, callback_data.deal_id)
    if deal is None:
        await call.answer("Сделка не найдена", show_alert=True)
        return

    try:
        await reviews_service.ensure_can_leave(session, deal, user)
    except ReviewError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    value = callback_data.value or "+"
    required = settings.get_bool("reviews_comment_required")

    await state.set_state(ReviewSG.comment)
    await state.update_data(deal_id=deal.id, value=value)

    await show(
        call,
        block(
            title("✍️", "Комментарий к отзыву"),
            "Напишите пару слов о второй стороне."
            + ("" if required else " Можно и без комментария."),
        ),
        kb.cancel() if required else kb.review_skip_comment(deal.id, value),
    )


@router.callback_query(ReviewCB.filter(F.action == "save"))
async def save_without_comment(call: CallbackQuery, callback_data: ReviewCB, session: AsyncSession,
                               user: User, state: FSMContext) -> None:
    deal = await session.get(Deal, callback_data.deal_id)
    if deal is None:
        await call.answer("Сделка не найдена", show_alert=True)
        return

    await state.clear()
    await _save(call, session, user, deal, callback_data.value or "+", None)


@router.message(ReviewSG.comment)
async def save_with_comment(message: Message, session: AsyncSession, user: User, state: FSMContext) -> None:
    data = await state.get_data()
    deal = await session.get(Deal, data.get("deal_id", 0))
    if deal is None:
        await state.clear()
        await message.answer("❌ Сделка не найдена.")
        return

    comment = (message.text or "").strip()
    try:
        review = await reviews_service.leave(
            session, deal, user, 1 if data.get("value") == "+" else -1, comment
        )
    except ReviewError as exc:
        await message.answer(f"❌ {exc}")
        return

    await state.clear()
    mark = "👍 положительный" if review.is_positive else "👎 отрицательный"
    await message.answer(
        block(title("✅", "Отзыв сохранён"), f"Оценка: {mark}", f"Комментарий: {esc(comment)}"),
        reply_markup=kb.back_only(),
    )


@router.callback_query(ReviewCB.filter(F.action == "skip"))
async def skip(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(call, "Хорошо, без отзыва.", None, toast="Пропущено")

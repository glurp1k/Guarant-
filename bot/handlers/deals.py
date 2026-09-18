"""Гарант-сделки: создание, приглашение партнёра, оплата, закрытие, споры."""

from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import CommissionPayer, Deal, DealRole, DealStatus, User
from bot.keyboards import user as kb
from bot.keyboards.callbacks import DealCB
from bot.services import deals as deals_service
from bot.services.deals import DealError
from bot.services.settings import settings
from bot.states import DealSG
from bot.utils.money import fmt, parse_amount
from bot.utils.render import Event, show
from bot.utils.texts import esc

log = logging.getLogger(__name__)
router = Router(name="deals")

STATUS_TITLES = {
    DealStatus.WAITING_PARTNER.value: "⏳ Ждём вторую сторону",
    DealStatus.WAITING_PAYMENT.value: "💳 Ждём оплату покупателя",
    DealStatus.FUNDED.value: "🔒 Деньги у гаранта",
    DealStatus.COMPLETED.value: "✅ Завершена",
    DealStatus.CANCELLED.value: "❌ Отменена",
    DealStatus.REFUNDED.value: "↩️ Возврат покупателю",
    DealStatus.DISPUTE.value: "⚖️ Спор",
}

PAYER_TITLES = {
    CommissionPayer.SELLER.value: "продавец",
    CommissionPayer.BUYER.value: "покупатель",
    CommissionPayer.SPLIT.value: "пополам",
}


async def _notify(bot: Bot, user_id: int | None, text: str) -> None:
    if not user_id:
        return
    try:
        await bot.send_message(user_id, text)
    except Exception as exc:  # noqa: BLE001 - собеседник мог заблокировать бота
        log.debug("Уведомление по сделке не доставлено %s: %s", user_id, exc)


async def _share_url(bot: Bot, deal: Deal) -> str:
    me = await bot.me()
    return f"https://t.me/{me.username}?start=deal_{deal.code}"


def deal_text(deal: Deal, viewer_id: int | None = None, share_url: str | None = None) -> str:
    seller = deal.seller.mention if deal.seller else "—"
    buyer = deal.buyer.mention if deal.buyer else "—"

    lines = [
        f"🤝 <b>Сделка {deal.code}</b>",
        "",
        f"Статус: {STATUS_TITLES.get(deal.status, deal.status)}",
        f"Сумма: <b>{fmt(deal.amount)}</b>",
        f"Комиссия: <b>{fmt(deal.commission)}</b> (платит {PAYER_TITLES.get(deal.commission_payer, '—')})",
        "",
        f"🛍 Продавец: {esc(seller)}",
        f"💵 Покупатель: {esc(buyer)}",
    ]

    if deal.description:
        lines += ["", f"📝 Предмет сделки:\n{esc(deal.description)}"]

    if viewer_id is not None and viewer_id == deal.buyer_id:
        lines += ["", f"К оплате с вашего баланса: <b>{fmt(deal.buyer_charge)}</b>"]
    if viewer_id is not None and viewer_id == deal.seller_id:
        lines += ["", f"Вы получите: <b>{fmt(deal.seller_payout)}</b>"]

    if deal.dispute_reason:
        lines += ["", f"⚖️ Причина спора: {esc(deal.dispute_reason)}"]

    if share_url:
        lines += ["", "🔗 Ссылка для второй стороны:", f"<code>{share_url}</code>"]

    return "\n".join(lines)


def _card_markup(deal: Deal, viewer: User, share_url: str | None = None) -> object:
    return kb.deal_card(
        deal.id,
        can_pay=deal.status == DealStatus.WAITING_PAYMENT.value and viewer.tg_id == deal.buyer_id,
        can_confirm=deal.status == DealStatus.FUNDED.value and viewer.tg_id == deal.buyer_id,
        can_dispute=deal.status == DealStatus.FUNDED.value and viewer.tg_id in (deal.seller_id, deal.buyer_id),
        can_cancel=deal.status in (DealStatus.WAITING_PARTNER.value, DealStatus.WAITING_PAYMENT.value)
        and viewer.tg_id in (deal.seller_id, deal.buyer_id),
        share_url=share_url,
    )


# --------------------------------------------------------------------------- #
# Меню и создание
# --------------------------------------------------------------------------- #


async def render_deals_menu(event: Event) -> None:
    await show(
        event,
        "🤝 <b>Гарант-сделки</b>\n\n"
        "Покупатель оплачивает сделку — деньги хранятся у гаранта.\n"
        "Продавец получает их только после подтверждения покупателя.",
        kb.deals_menu(),
    )


@router.callback_query(DealCB.filter(F.action == "list"))
async def deals_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render_deals_menu(call)


@router.callback_query(DealCB.filter(F.action == "new"))
async def new_deal(call: CallbackQuery, state: FSMContext) -> None:
    if not settings.get_bool("deal_enabled"):
        await call.answer("Создание сделок временно отключено", show_alert=True)
        return

    await state.clear()
    await call.message.edit_text("Кем вы выступаете в сделке?", reply_markup=kb.deal_roles())
    await call.answer()


@router.callback_query(DealCB.filter(F.action == "role"))
async def pick_role(call: CallbackQuery, callback_data: DealCB, state: FSMContext) -> None:
    await state.set_state(DealSG.amount)
    await state.update_data(role=callback_data.value)

    minimum = settings.get_decimal("deal_min_amount")
    maximum = settings.get_decimal("deal_max_amount")
    limits = []
    if minimum > 0:
        limits.append(f"от {fmt(minimum)}")
    if maximum > 0:
        limits.append(f"до {fmt(maximum)}")
    hint = f"\nДопустимо: {', '.join(limits)}" if limits else ""

    await call.message.edit_text(
        f"💵 Введите сумму сделки в USDT.{hint}\n\n"
        f"Комиссия сервиса: <b>{settings.get_decimal('deal_commission_percent')}%</b>",
        reply_markup=kb.cancel(),
    )
    await call.answer()


@router.message(DealSG.amount)
async def got_amount(message: Message, state: FSMContext) -> None:
    amount = parse_amount(message.text or "")
    if amount is None:
        await message.answer("❌ Введите сумму числом, например <code>100</code>.")
        return

    await state.update_data(amount=str(amount))
    await state.set_state(DealSG.description)
    await message.answer(
        "📝 Опишите предмет сделки — что и на каких условиях передаётся.\n\n"
        "Этот текст увидит вторая сторона."
    )


@router.message(DealSG.description)
async def got_description(message: Message, state: FSMContext) -> None:
    description = (message.text or "").strip()
    if len(description) < 3:
        await message.answer("❌ Опишите сделку подробнее.")
        return

    await state.update_data(description=description)
    await state.set_state(None)

    amount = Decimal((await state.get_data())["amount"])
    commission = deals_service.commission_for(amount)
    await message.answer(
        f"Кто платит комиссию сервиса ({fmt(commission)})?",
        reply_markup=kb.deal_payers(),
    )


@router.callback_query(DealCB.filter(F.action == "payer"))
async def create_deal(
    call: CallbackQuery,
    callback_data: DealCB,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    if "amount" not in data or "description" not in data:
        await call.answer("Черновик устарел, начните заново", show_alert=True)
        return

    try:
        deal = await deals_service.create(
            session,
            creator=user,
            amount=Decimal(data["amount"]),
            description=data["description"],
            role=DealRole(data.get("role", DealRole.SELLER.value)),
            commission_payer=CommissionPayer(callback_data.value),
        )
    except DealError as exc:
        await state.clear()
        await call.message.edit_text(f"❌ {exc}", reply_markup=kb.back_only())
        await call.answer()
        return

    await state.clear()
    share_url = await _share_url(call.bot, deal)
    await call.message.edit_text(
        deal_text(deal, user.tg_id, share_url),
        reply_markup=_card_markup(deal, user, share_url),
    )
    await call.answer("Сделка создана")


# --------------------------------------------------------------------------- #
# Просмотр и участие
# --------------------------------------------------------------------------- #


async def show_join_offer(message: Message, session: AsyncSession, user: User, deal: Deal) -> None:
    """Приглашение по ссылке /start deal_CODE."""
    if deal.creator_id == user.tg_id:
        await message.answer(
            deal_text(deal, user.tg_id, await _share_url(message.bot, deal)),
            reply_markup=_card_markup(deal, user, await _share_url(message.bot, deal)),
        )
        return

    if deal.status != DealStatus.WAITING_PARTNER.value:
        await message.answer(deal_text(deal, user.tg_id), reply_markup=kb.back_only())
        return

    role = "покупателем" if deal.creator_role == DealRole.SELLER.value else "продавцом"
    await message.answer(
        f"{deal_text(deal, user.tg_id)}\n\n"
        f"Вы присоединяетесь как <b>{role}</b>. Подтвердить участие?",
        reply_markup=kb.join_deal(deal.id),
    )


@router.callback_query(DealCB.filter(F.action == "join"))
async def join_deal(call: CallbackQuery, callback_data: DealCB, session: AsyncSession, user: User) -> None:
    deal = await session.get(Deal, callback_data.deal_id)
    if deal is None:
        await call.answer("Сделка не найдена", show_alert=True)
        return

    try:
        deal = await deals_service.join(session, deal, user)
    except DealError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    await call.message.edit_text(deal_text(deal, user.tg_id), reply_markup=_card_markup(deal, user))
    await call.answer("Вы в сделке")

    await _notify(
        call.bot, deal.creator_id,
        f"✅ К сделке <b>{deal.code}</b> присоединился {esc(user.mention)}.\n"
        f"Статус: {STATUS_TITLES[deal.status]}",
    )


@router.callback_query(DealCB.filter(F.action == "my"))
async def my_deals(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    items = await deals_service.user_deals(session, user.tg_id, limit=10)
    if not items:
        await call.message.edit_text("📂 У вас пока нет сделок.", reply_markup=kb.deals_menu())
        await call.answer()
        return

    builder = InlineKeyboardBuilder()
    for deal in items:
        builder.button(
            text=f"{deal.code} · {fmt(deal.amount)} · {STATUS_TITLES.get(deal.status, '')}",
            callback_data=DealCB(action="view", deal_id=deal.id),
        )
    builder.button(text="⬅️ Назад", callback_data=DealCB(action="list"))
    builder.adjust(1)

    await call.message.edit_text("📂 <b>Ваши сделки</b>", reply_markup=builder.as_markup())
    await call.answer()


@router.callback_query(DealCB.filter(F.action == "view"))
async def view_deal(call: CallbackQuery, callback_data: DealCB, session: AsyncSession, user: User) -> None:
    deal = await session.get(Deal, callback_data.deal_id)
    if deal is None or user.tg_id not in (deal.seller_id, deal.buyer_id, deal.creator_id):
        await call.answer("Сделка не найдена", show_alert=True)
        return

    share_url = await _share_url(call.bot, deal) if deal.status == DealStatus.WAITING_PARTNER.value else None
    await call.message.edit_text(deal_text(deal, user.tg_id, share_url), reply_markup=_card_markup(deal, user, share_url))
    await call.answer()


# --------------------------------------------------------------------------- #
# Действия по сделке
# --------------------------------------------------------------------------- #


@router.callback_query(DealCB.filter(F.action == "pay"))
async def pay_deal(call: CallbackQuery, callback_data: DealCB, session: AsyncSession, user: User) -> None:
    deal = await session.get(Deal, callback_data.deal_id)
    if deal is None:
        await call.answer("Сделка не найдена", show_alert=True)
        return

    try:
        deal = await deals_service.fund(session, deal, user)
    except DealError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    await call.message.edit_text(deal_text(deal, user.tg_id), reply_markup=_card_markup(deal, user))
    await call.answer("Средства удержаны гарантом")

    await _notify(
        call.bot, deal.seller_id,
        f"💰 Сделка <b>{deal.code}</b> оплачена покупателем.\n"
        f"Деньги у гаранта — можно передавать товар или услугу.\n"
        f"К получению после подтверждения: <b>{fmt(deal.seller_payout)}</b>",
    )


@router.callback_query(DealCB.filter(F.action == "done"))
async def complete_deal(call: CallbackQuery, callback_data: DealCB, session: AsyncSession, user: User) -> None:
    deal = await session.get(Deal, callback_data.deal_id)
    if deal is None or user.tg_id != deal.buyer_id:
        await call.answer("Подтвердить может только покупатель", show_alert=True)
        return

    try:
        deal = await deals_service.complete(session, deal)
    except DealError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    await call.message.edit_text(deal_text(deal, user.tg_id), reply_markup=kb.back_only())
    await call.answer("Сделка закрыта")

    await _notify(
        call.bot, deal.seller_id,
        f"✅ Сделка <b>{deal.code}</b> завершена.\n"
        f"На баланс зачислено: <b>{fmt(deal.seller_payout)}</b>",
    )


@router.callback_query(DealCB.filter(F.action == "cancel"))
async def cancel_deal(call: CallbackQuery, callback_data: DealCB, session: AsyncSession, user: User) -> None:
    deal = await session.get(Deal, callback_data.deal_id)
    if deal is None or user.tg_id not in (deal.seller_id, deal.buyer_id, deal.creator_id):
        await call.answer("Сделка не найдена", show_alert=True)
        return

    counterpart = deal.counterpart_id(user.tg_id)
    try:
        deal = await deals_service.cancel(session, deal, reason=f"Отменил {user.mention}")
    except DealError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    await call.message.edit_text(deal_text(deal, user.tg_id), reply_markup=kb.back_only())
    await call.answer("Сделка отменена")
    await _notify(call.bot, counterpart, f"❌ Сделка <b>{deal.code}</b> отменена второй стороной.")


@router.callback_query(DealCB.filter(F.action == "dispute"))
async def ask_dispute_reason(call: CallbackQuery, callback_data: DealCB, state: FSMContext) -> None:
    await state.set_state(DealSG.dispute_reason)
    await state.update_data(deal_id=callback_data.deal_id)
    await call.message.edit_text(
        "⚖️ <b>Открытие спора</b>\n\n"
        "Опишите проблему: что было обещано, что пошло не так.\n"
        "Сообщение увидит администрация.",
        reply_markup=kb.cancel(),
    )
    await call.answer()


@router.message(DealSG.dispute_reason)
async def open_dispute(message: Message, session: AsyncSession, user: User, state: FSMContext) -> None:
    reason = (message.text or "").strip()
    if len(reason) < 5:
        await message.answer("❌ Опишите проблему подробнее.")
        return

    data = await state.get_data()
    deal = await session.get(Deal, data.get("deal_id", 0))
    if deal is None:
        await state.clear()
        await message.answer("❌ Сделка не найдена.")
        return

    try:
        deal = await deals_service.open_dispute(session, deal, user, reason)
    except DealError as exc:
        await state.clear()
        await message.answer(f"❌ {exc}")
        return

    await state.clear()
    await message.answer(
        f"⚖️ Спор по сделке <b>{deal.code}</b> открыт.\n"
        f"Администрация рассмотрит обращение и примет решение.",
        reply_markup=kb.back_only(),
    )

    await _notify(message.bot, deal.counterpart_id(user.tg_id),
                  f"⚖️ По сделке <b>{deal.code}</b> открыт спор. Ожидайте решения администрации.")

    from bot.handlers.withdraw import notify_admins

    await notify_admins(
        message.bot, session,
        f"⚖️ <b>Новый спор по сделке {deal.code}</b>\n\n"
        f"Сумма: {fmt(deal.amount)}\n"
        f"Продавец: {esc(deal.seller.mention if deal.seller else '—')}\n"
        f"Покупатель: {esc(deal.buyer.mention if deal.buyer else '—')}\n\n"
        f"Причина: {esc(reason)}",
    )

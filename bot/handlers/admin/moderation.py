"""Очереди на обработку: выводы, споры, счета."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Account, Deal, Invoice, InvoiceStatus, PayMethod, User, Withdrawal
from bot.keyboards import admin as akb
from bot.keyboards.callbacks import AdminCB, AdminItemCB
from bot.services import deals as deals_service
from bot.services import payments, withdrawals
from bot.services.deals import DealError
from bot.services.withdrawals import WithdrawError
from bot.states import AdminSG
from bot.utils.money import ZERO, fmt
from bot.utils.texts import esc, fmt_date

log = logging.getLogger(__name__)
router = Router(name="admin-moderation")


async def _notify(bot: Bot, user_id: int | None, text: str) -> None:
    if not user_id:
        return
    try:
        await bot.send_message(user_id, text)
    except Exception as exc:  # noqa: BLE001 - пользователь мог заблокировать бота
        log.debug("Уведомление %s не доставлено: %s", user_id, exc)


# --------------------------------------------------------------------------- #
# Заявки на вывод
# --------------------------------------------------------------------------- #


def withdrawal_text(request: Withdrawal) -> str:
    who = request.user.mention if request.user else f"ID {request.user_id}"
    source = "страховой депозит" if request.source == Account.DEPOSIT.value else "баланс"
    return (
        f"📤 <b>Заявка на вывод #{request.id}</b>\n\n"
        f"Пользователь: {esc(who)} (<code>{request.user_id}</code>)\n"
        f"Источник: {source}\n"
        f"Способ: <b>{payments.method_title(PayMethod(request.method))}</b>\n\n"
        f"Списано: {fmt(request.amount)}\n"
        f"Комиссия: {fmt(request.fee)}\n"
        f"<b>К отправке: {fmt(request.net_amount)}</b>\n\n"
        f"Реквизиты:\n<code>{esc(request.destination)}</code>\n\n"
        f"Создана: {fmt_date(request.created_at)} UTC"
    )


@router.callback_query(AdminCB.filter(F.action == "withdrawals"))
async def withdrawals_queue(call: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    items = await withdrawals.pending_requests(session, limit=20)

    if not items:
        await call.message.edit_text("📤 Заявок на вывод нет.", reply_markup=akb.back_to_admin())
        await call.answer()
        return

    builder = InlineKeyboardBuilder()
    for request in items:
        who = request.user.mention if request.user else request.user_id
        builder.button(
            text=f"#{request.id} · {fmt(request.net_amount)} · {who}",
            callback_data=AdminItemCB(action="wd_open", item_id=request.id),
        )
    builder.button(text="⬅️ В админку", callback_data=AdminCB(action="menu"))
    builder.adjust(1)

    await call.message.edit_text(
        f"📤 <b>Заявки на вывод</b> — в очереди {len(items)}",
        reply_markup=builder.as_markup(),
    )
    await call.answer()


@router.callback_query(AdminItemCB.filter(F.action == "wd_open"))
async def withdrawal_open(call: CallbackQuery, callback_data: AdminItemCB, session: AsyncSession) -> None:
    request = await session.get(Withdrawal, callback_data.item_id)
    if request is None:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    await call.message.edit_text(withdrawal_text(request), reply_markup=akb.withdrawal_card(request.id))
    await call.answer()


@router.callback_query(AdminItemCB.filter(F.action == "wd_paid"))
async def withdrawal_paid(call: CallbackQuery, callback_data: AdminItemCB, session: AsyncSession, user: User) -> None:
    request = await session.get(Withdrawal, callback_data.item_id)
    if request is None:
        await call.answer("Заявка не найдена", show_alert=True)
        return

    try:
        await withdrawals.mark_paid(session, request, admin_id=user.tg_id)
    except WithdrawError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    await call.message.edit_text(
        withdrawal_text(request) + "\n\n✅ <b>Отмечена как выплаченная</b>",
        reply_markup=akb.back_to_admin(),
    )
    await call.answer("Готово")

    await _notify(
        call.bot, request.user_id,
        f"✅ <b>Вывод #{request.id} выполнен</b>\n\n"
        f"Отправлено: <b>{fmt(request.net_amount)}</b>\n"
        f"Способ: {payments.method_title(PayMethod(request.method))}",
    )


@router.callback_query(AdminItemCB.filter(F.action == "wd_hash"))
async def withdrawal_ask_hash(call: CallbackQuery, callback_data: AdminItemCB, state: FSMContext) -> None:
    await state.set_state(AdminSG.withdraw_hash)
    await state.update_data(request_id=callback_data.item_id)
    await call.message.edit_text(
        "🧾 Пришлите хэш транзакции — он уйдёт пользователю вместе с подтверждением.",
        reply_markup=akb.back_to_admin(),
    )
    await call.answer()


@router.message(AdminSG.withdraw_hash)
async def withdrawal_save_hash(message: Message, session: AsyncSession, user: User, state: FSMContext) -> None:
    data = await state.get_data()
    request = await session.get(Withdrawal, data.get("request_id", 0))
    if request is None:
        await state.clear()
        await message.answer("❌ Заявка не найдена.", reply_markup=akb.back_to_admin())
        return

    tx_hash = (message.text or "").strip()
    try:
        await withdrawals.mark_paid(session, request, admin_id=user.tg_id, tx_hash=tx_hash)
    except WithdrawError as exc:
        await state.clear()
        await message.answer(f"❌ {exc}", reply_markup=akb.back_to_admin())
        return

    await state.clear()
    await message.answer(f"✅ Заявка #{request.id} закрыта.", reply_markup=akb.back_to_admin())

    await _notify(
        message.bot, request.user_id,
        f"✅ <b>Вывод #{request.id} выполнен</b>\n\n"
        f"Отправлено: <b>{fmt(request.net_amount)}</b>\n"
        f"Хэш: <code>{esc(tx_hash)}</code>",
    )


@router.callback_query(AdminItemCB.filter(F.action == "wd_reject"))
async def withdrawal_ask_reason(call: CallbackQuery, callback_data: AdminItemCB, state: FSMContext) -> None:
    await state.set_state(AdminSG.withdraw_reject)
    await state.update_data(request_id=callback_data.item_id)
    await call.message.edit_text(
        "❌ Пришлите причину отказа. Средства вернутся пользователю на тот же счёт.",
        reply_markup=akb.back_to_admin(),
    )
    await call.answer()


@router.message(AdminSG.withdraw_reject)
async def withdrawal_do_reject(message: Message, session: AsyncSession, user: User, state: FSMContext) -> None:
    data = await state.get_data()
    request = await session.get(Withdrawal, data.get("request_id", 0))
    if request is None:
        await state.clear()
        await message.answer("❌ Заявка не найдена.", reply_markup=akb.back_to_admin())
        return

    reason = (message.text or "").strip() or "без объяснения"
    try:
        await withdrawals.reject(session, request, admin_id=user.tg_id, comment=reason)
    except WithdrawError as exc:
        await state.clear()
        await message.answer(f"❌ {exc}", reply_markup=akb.back_to_admin())
        return

    await state.clear()
    await message.answer(f"❌ Заявка #{request.id} отклонена, средства возвращены.", reply_markup=akb.back_to_admin())

    await _notify(
        message.bot, request.user_id,
        f"❌ <b>Вывод #{request.id} отклонён</b>\n\n"
        f"Причина: {esc(reason)}\n"
        f"Средства <b>{fmt(request.amount)}</b> возвращены на счёт.",
    )


# --------------------------------------------------------------------------- #
# Споры
# --------------------------------------------------------------------------- #


@router.callback_query(AdminCB.filter(F.action == "disputes"))
async def disputes_queue(call: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    items = await deals_service.disputes(session)

    if not items:
        await call.message.edit_text("⚖️ Открытых споров нет.", reply_markup=akb.back_to_admin())
        await call.answer()
        return

    builder = InlineKeyboardBuilder()
    for deal in items:
        builder.button(
            text=f"{deal.code} · {fmt(deal.amount)}",
            callback_data=AdminItemCB(action="deal_open", item_id=deal.id),
        )
    builder.button(text="⬅️ В админку", callback_data=AdminCB(action="menu"))
    builder.adjust(1)

    await call.message.edit_text(f"⚖️ <b>Споры</b> — открыто {len(items)}", reply_markup=builder.as_markup())
    await call.answer()


@router.callback_query(AdminItemCB.filter(F.action == "deal_open"))
async def dispute_open(call: CallbackQuery, callback_data: AdminItemCB, session: AsyncSession) -> None:
    from bot.handlers.deals import deal_text

    deal = await session.get(Deal, callback_data.item_id)
    if deal is None:
        await call.answer("Сделка не найдена", show_alert=True)
        return

    await call.message.edit_text(deal_text(deal), reply_markup=akb.dispute_card(deal.id))
    await call.answer()


@router.callback_query(AdminItemCB.filter(F.action == "deal_release"))
async def dispute_release(call: CallbackQuery, callback_data: AdminItemCB, session: AsyncSession, user: User) -> None:
    deal = await session.get(Deal, callback_data.item_id)
    if deal is None:
        await call.answer("Сделка не найдена", show_alert=True)
        return

    try:
        deal = await deals_service.complete(session, deal)
    except DealError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    log.info("Админ %s отдал средства продавцу по сделке %s", user.tg_id, deal.code)
    await call.message.edit_text(
        f"✅ По сделке <b>{deal.code}</b> средства отправлены продавцу: {fmt(deal.seller_payout)}",
        reply_markup=akb.back_to_admin(),
    )
    await call.answer()

    await _notify(call.bot, deal.seller_id,
                  f"✅ Спор по сделке <b>{deal.code}</b> решён в вашу пользу.\n"
                  f"Зачислено: <b>{fmt(deal.seller_payout)}</b>")
    await _notify(call.bot, deal.buyer_id,
                  f"⚖️ Спор по сделке <b>{deal.code}</b> решён в пользу продавца.")


@router.callback_query(AdminItemCB.filter(F.action == "deal_refund"))
async def dispute_refund(call: CallbackQuery, callback_data: AdminItemCB, session: AsyncSession, user: User) -> None:
    deal = await session.get(Deal, callback_data.item_id)
    if deal is None:
        await call.answer("Сделка не найдена", show_alert=True)
        return

    try:
        deal = await deals_service.refund(session, deal, reason=f"Решение админа {user.tg_id}")
    except DealError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    log.info("Админ %s вернул средства покупателю по сделке %s", user.tg_id, deal.code)
    await call.message.edit_text(
        f"↩️ По сделке <b>{deal.code}</b> средства возвращены покупателю: {fmt(deal.buyer_charge)}",
        reply_markup=akb.back_to_admin(),
    )
    await call.answer()

    await _notify(call.bot, deal.buyer_id,
                  f"↩️ Спор по сделке <b>{deal.code}</b> решён в вашу пользу.\n"
                  f"Возвращено: <b>{fmt(deal.buyer_charge)}</b>")
    await _notify(call.bot, deal.seller_id,
                  f"⚖️ Спор по сделке <b>{deal.code}</b> решён в пользу покупателя.")


# --------------------------------------------------------------------------- #
# Счета на пополнение
# --------------------------------------------------------------------------- #


@router.callback_query(AdminCB.filter(F.action == "invoices"))
async def invoices_queue(call: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    items = await payments.pending_invoices(session)

    if not items:
        await call.message.edit_text("🧾 Неоплаченных счетов нет.", reply_markup=akb.back_to_admin())
        await call.answer()
        return

    builder = InlineKeyboardBuilder()
    for invoice in items[:20]:
        who = invoice.user.mention if invoice.user else invoice.user_id
        builder.button(
            text=f"#{invoice.id} · {invoice.pay_amount:.6f} · {who}",
            callback_data=AdminItemCB(action="inv_open", item_id=invoice.id),
        )
    builder.button(text="⬅️ В админку", callback_data=AdminCB(action="menu"))
    builder.adjust(1)

    await call.message.edit_text(
        f"🧾 <b>Ожидают оплаты</b> — {len(items)}\n\n"
        f"Если платёж пришёл не той суммой, его можно зачислить вручную.",
        reply_markup=builder.as_markup(),
    )
    await call.answer()


@router.callback_query(AdminItemCB.filter(F.action == "inv_open"))
async def invoice_open(call: CallbackQuery, callback_data: AdminItemCB, session: AsyncSession) -> None:
    invoice = await session.get(Invoice, callback_data.item_id)
    if invoice is None:
        await call.answer("Счёт не найден", show_alert=True)
        return

    who = invoice.user.mention if invoice.user else invoice.user_id
    target = "депозит" if invoice.purpose == Account.DEPOSIT.value else "баланс"
    await call.message.edit_text(
        f"🧾 <b>Счёт #{invoice.id}</b>\n\n"
        f"Пользователь: {esc(who)} (<code>{invoice.user_id}</code>)\n"
        f"Назначение: {target}\n"
        f"Способ: {payments.method_title(PayMethod(invoice.method))}\n"
        f"Ожидается: <code>{invoice.pay_amount:.6f}</code>\n"
        f"Адрес: <code>{esc(invoice.address or '—')}</code>\n"
        f"Создан: {fmt_date(invoice.created_at)} UTC",
        reply_markup=akb.invoice_card(invoice.id),
    )
    await call.answer()


@router.callback_query(AdminItemCB.filter(F.action == "inv_confirm"))
async def invoice_confirm(call: CallbackQuery, callback_data: AdminItemCB, session: AsyncSession, user: User) -> None:
    invoice = await session.get(Invoice, callback_data.item_id)
    if invoice is None:
        await call.answer("Счёт не найден", show_alert=True)
        return
    if invoice.status != InvoiceStatus.PENDING.value:
        await call.answer("Счёт уже обработан", show_alert=True)
        return

    credited = await payments.mark_paid(session, invoice, tx_hash=f"manual:{user.tg_id}")
    if credited <= ZERO:
        await call.answer("Не удалось зачислить", show_alert=True)
        return

    log.info("Админ %s зачислил счёт #%s вручную", user.tg_id, invoice.id)
    await call.message.edit_text(
        f"✅ Счёт #{invoice.id} зачислен вручную: {fmt(credited)}",
        reply_markup=akb.back_to_admin(),
    )
    await call.answer()

    where = "депозит" if invoice.purpose == Account.DEPOSIT.value else "баланс"
    await _notify(call.bot, invoice.user_id, f"✅ На ваш {where} зачислено <b>{fmt(credited)}</b>.")


@router.callback_query(AdminItemCB.filter(F.action == "inv_cancel"))
async def invoice_cancel(call: CallbackQuery, callback_data: AdminItemCB, session: AsyncSession) -> None:
    invoice = await session.get(Invoice, callback_data.item_id)
    if invoice is None:
        await call.answer("Счёт не найден", show_alert=True)
        return

    await payments.cancel_invoice(session, invoice)
    await call.message.edit_text(f"❌ Счёт #{invoice.id} отменён.", reply_markup=akb.back_to_admin())
    await call.answer()

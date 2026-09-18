"""Пополнение баланса и страхового депозита."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Account, Invoice, InvoiceStatus, PayMethod, User
from bot.keyboards import user as kb
from bot.keyboards.callbacks import TopupCB
from bot.services import payments
from bot.services.payments import PaymentError
from bot.services.settings import settings
from bot.states import TopupSG
from bot.utils.money import ZERO, fmt, parse_amount
from bot.utils.texts import esc, fmt_date

log = logging.getLogger(__name__)
router = Router(name="topup")


def _limits_text(purpose: Account) -> str:
    if purpose is Account.DEPOSIT:
        minimum = settings.get_decimal("deposit_min")
        maximum = settings.get_decimal("deposit_max")
        head = "🛡 <b>Пополнение страхового депозита</b>"
    else:
        minimum = settings.get_decimal("topup_min")
        maximum = settings.get_decimal("topup_max")
        head = "💳 <b>Пополнение баланса</b>"

    lines = [head, ""]
    if minimum > ZERO:
        lines.append(f"Минимум: <b>{fmt(minimum)}</b>")
    if maximum > ZERO:
        lines.append(f"Максимум: <b>{fmt(maximum)}</b>")
    lines += ["", "Выберите способ оплаты:"]
    return "\n".join(lines)


def invoice_text(invoice: Invoice) -> str:
    method = PayMethod(invoice.method)
    target = "страхового депозита" if invoice.purpose == Account.DEPOSIT.value else "баланса"

    if method is PayMethod.CRYPTOBOT:
        return (
            f"🧾 <b>Счёт #{invoice.id}</b> на пополнение {target}\n\n"
            f"Сумма: <b>{fmt(invoice.pay_amount)}</b>\n"
            f"Способ: CryptoBot\n"
            f"Действует до: {fmt_date(invoice.expires_at)} UTC\n\n"
            f"Нажмите «Оплатить», средства зачислятся автоматически."
        )

    return (
        f"🧾 <b>Счёт #{invoice.id}</b> на пополнение {target}\n\n"
        f"Сеть: <b>{payments.method_title(method)}</b>\n"
        f"Адрес:\n<code>{esc(invoice.address)}</code>\n\n"
        f"Сумма к отправке (ровно):\n<code>{invoice.pay_amount:.6f}</code>\n\n"
        f"⚠️ Отправьте <b>точную</b> сумму — по ней бот опознает ваш платёж.\n"
        f"Счёт действует до {fmt_date(invoice.expires_at)} UTC.\n"
        f"Зачисление — после подтверждения сети."
    )


@router.callback_query(TopupCB.filter(F.action == "choose"))
async def choose_method(call: CallbackQuery, callback_data: TopupCB, state: FSMContext) -> None:
    await state.clear()
    purpose = Account(callback_data.purpose)

    if not any(payments.method_enabled(method) for method in PayMethod):
        await call.answer("Пополнение сейчас недоступно", show_alert=True)
        return

    await call.message.edit_text(_limits_text(purpose), reply_markup=kb.pay_methods(purpose.value))
    await call.answer()


@router.callback_query(TopupCB.filter(F.action == "method"))
async def ask_amount(call: CallbackQuery, callback_data: TopupCB, state: FSMContext) -> None:
    method = PayMethod(callback_data.method)
    if not payments.method_enabled(method):
        await call.answer("Этот способ отключён", show_alert=True)
        return

    await state.set_state(TopupSG.amount)
    await state.update_data(method=method.value, purpose=callback_data.purpose)

    await call.message.edit_text(
        f"💵 Введите сумму пополнения в USDT.\n\n"
        f"Способ: <b>{payments.method_title(method)}</b>\n"
        f"Например: <code>25</code> или <code>10.5</code>",
        reply_markup=kb.cancel(),
    )
    await call.answer()


@router.message(TopupSG.amount)
async def create_invoice(message: Message, session: AsyncSession, user: User, state: FSMContext) -> None:
    amount = parse_amount(message.text or "")
    if amount is None:
        await message.answer("❌ Не похоже на сумму. Введите число, например <code>25</code>.")
        return

    data = await state.get_data()
    method = PayMethod(data.get("method", PayMethod.CRYPTOBOT.value))
    purpose = Account(data.get("purpose", Account.BALANCE.value))

    try:
        invoice = await payments.create_invoice(session, user, amount, method, purpose)
    except PaymentError as exc:
        await message.answer(f"❌ {exc}")
        return

    await state.clear()
    await message.answer(invoice_text(invoice), reply_markup=kb.invoice_actions(invoice.id, invoice.pay_url))


@router.callback_query(TopupCB.filter(F.action == "check"))
async def check_invoice(call: CallbackQuery, callback_data: TopupCB, session: AsyncSession, user: User) -> None:
    invoice = await session.get(Invoice, callback_data.invoice_id)
    if invoice is None or invoice.user_id != user.tg_id:
        await call.answer("Счёт не найден", show_alert=True)
        return

    if invoice.status == InvoiceStatus.PAID.value:
        await call.answer("Этот счёт уже оплачен", show_alert=True)
        return
    if invoice.status != InvoiceStatus.PENDING.value:
        await call.answer("Счёт больше не активен", show_alert=True)
        return

    await call.answer("Проверяю…")
    try:
        credited = await payments.recheck(session, invoice)
    except Exception as exc:  # noqa: BLE001 - показываем пользователю понятный текст
        log.warning("Проверка счёта #%s не удалась: %s", invoice.id, exc)
        await call.message.answer("⚠️ Платёжный сервис временно недоступен, попробуйте через минуту.")
        return

    if credited > ZERO:
        await session.refresh(user)
        where = "депозит" if invoice.purpose == Account.DEPOSIT.value else "баланс"
        await call.message.edit_text(
            f"✅ <b>Оплата получена</b>\n\n"
            f"Зачислено на {where}: <b>{fmt(credited)}</b>\n"
            f"Текущий баланс: <b>{fmt(user.balance)}</b>",
            reply_markup=kb.back_only(),
        )
    else:
        await call.message.answer(
            "⏳ Платёж пока не найден.\n"
            "Если вы уже отправили перевод — подождите подтверждения сети и нажмите проверку ещё раз."
        )


@router.callback_query(TopupCB.filter(F.action == "cancel"))
async def cancel_invoice(call: CallbackQuery, callback_data: TopupCB, session: AsyncSession, user: User) -> None:
    invoice = await session.get(Invoice, callback_data.invoice_id)
    if invoice is None or invoice.user_id != user.tg_id:
        await call.answer("Счёт не найден", show_alert=True)
        return

    await payments.cancel_invoice(session, invoice)
    await call.message.edit_text("❌ Счёт отменён.", reply_markup=kb.back_only())
    await call.answer()

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
from bot.services.templates import templates
from bot.states import TopupSG
from bot.utils.money import ZERO, fmt, parse_amount
from bot.utils.render import reply, screen
from bot.utils.texts import esc, fmt_date

log = logging.getLogger(__name__)
router = Router(name="topup")


def _limits(purpose: Account) -> dict[str, object]:
    if purpose is Account.DEPOSIT:
        minimum, maximum = settings.get_decimal("deposit_min"), settings.get_decimal("deposit_max")
    else:
        minimum, maximum = settings.get_decimal("topup_min"), settings.get_decimal("topup_max")
    return {
        "min": fmt(minimum) if minimum > ZERO else "без ограничений",
        "max": fmt(maximum) if maximum > ZERO else "без ограничений",
    }


def invoice_values(invoice: Invoice) -> dict[str, object]:
    method = PayMethod(invoice.method)
    return {
        "id": invoice.id,
        "network": payments.method_title(method),
        "address": esc(invoice.address or "—"),
        "amount": f"{invoice.pay_amount:.6f}" if method is not PayMethod.CRYPTOBOT else fmt(invoice.pay_amount),
        "expires": fmt_date(invoice.expires_at),
        "target": "депозита" if invoice.purpose == Account.DEPOSIT.value else "баланса",
    }


def invoice_template(invoice: Invoice) -> str:
    return "invoice_cryptobot" if invoice.method == PayMethod.CRYPTOBOT.value else "invoice_crypto"


def invoice_text(invoice: Invoice) -> str:
    text, _ = templates.render(invoice_template(invoice), **invoice_values(invoice))
    return text


@router.callback_query(TopupCB.filter(F.action == "choose"))
async def choose_method(call: CallbackQuery, callback_data: TopupCB, user: User, state: FSMContext) -> None:
    await state.clear()
    purpose = Account(callback_data.purpose)

    if not any(payments.method_enabled(method) for method in PayMethod):
        await call.answer("Пополнение сейчас недоступно", show_alert=True)
        return

    await screen(call, "topup_choose", kb.pay_methods(purpose.value),
                 **_limits(purpose), balance=fmt(user.balance), deposit=fmt(user.deposit))


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
    text, photo = templates.render(invoice_template(invoice), **invoice_values(invoice))
    await reply(message, text, kb.invoice_actions(invoice.id, invoice.pay_url), photo=photo)


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

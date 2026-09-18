"""Вывод средств: CryptoBot и напрямую на TRC20 / BEP20."""

from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Account, PayMethod, User
from bot.keyboards import user as kb
from bot.keyboards.callbacks import WithdrawCB
from bot.services import payments, users as users_service, withdrawals
from bot.services.settings import settings
from bot.services.withdrawals import WithdrawError
from bot.states import WithdrawSG
from bot.utils.money import ZERO, fmt, parse_amount
from bot.utils.texts import esc

log = logging.getLogger(__name__)
router = Router(name="withdraw")


def _available(user: User, source: Account) -> Decimal:
    return user.deposit if source is Account.DEPOSIT else user.balance


def _fees_overview(source: Account) -> str:
    rows = []
    for method in PayMethod:
        if not withdrawals.method_enabled(method):
            continue
        percent = settings.get_decimal(f"withdraw_fee_{method.value}_percent")
        if source is Account.DEPOSIT:
            percent += settings.get_decimal("deposit_withdraw_fee_percent")
        fixed = settings.get_decimal(f"withdraw_fee_{method.value}_fixed")

        parts = []
        if percent > ZERO:
            parts.append(f"{percent}%")
        if fixed > ZERO:
            parts.append(f"+{fixed:.2f} USDT")
        rows.append(f"• {payments.method_title(method)} — {' '.join(parts) or 'без комиссии'}")
    return "\n".join(rows)


async def notify_admins(bot: Bot, session: AsyncSession, text: str) -> None:
    for admin_id in await users_service.admin_ids(session):
        try:
            await bot.send_message(admin_id, text)
        except Exception as exc:  # noqa: BLE001 - админ мог не запускать бота
            log.debug("Уведомление админу %s не доставлено: %s", admin_id, exc)


@router.callback_query(WithdrawCB.filter(F.action == "menu"))
async def withdraw_menu(call: CallbackQuery, callback_data: WithdrawCB, user: User, state: FSMContext) -> None:
    await state.clear()

    if not settings.get_bool("withdraw_enabled"):
        await call.answer("Вывод временно отключён", show_alert=True)
        return

    source = Account(callback_data.source)
    available = _available(user, source)
    where = "депозита" if source is Account.DEPOSIT else "баланса"

    minimum = settings.get_decimal("withdraw_min")
    maximum = settings.get_decimal("withdraw_max")
    limits = []
    if minimum > ZERO:
        limits.append(f"минимум {fmt(minimum)}")
    if maximum > ZERO:
        limits.append(f"максимум {fmt(maximum)}")

    text = [
        f"📤 <b>Вывод {where}</b>",
        "",
        f"Доступно: <b>{fmt(available)}</b>",
    ]
    if limits:
        text.append("Лимиты: " + ", ".join(limits))
    text += ["", "<b>Комиссии:</b>", _fees_overview(source), "", "Выберите способ вывода:"]

    await call.message.edit_text("\n".join(text), reply_markup=kb.withdraw_methods(source.value))
    await call.answer()


@router.callback_query(WithdrawCB.filter(F.action == "method"))
async def ask_amount(call: CallbackQuery, callback_data: WithdrawCB, user: User, state: FSMContext) -> None:
    method = PayMethod(callback_data.method)
    if not withdrawals.method_enabled(method):
        await call.answer("Этот способ отключён", show_alert=True)
        return

    source = Account(callback_data.source)
    await state.set_state(WithdrawSG.amount)
    await state.update_data(method=method.value, source=source.value)

    await call.message.edit_text(
        f"💵 Введите сумму вывода в USDT.\n\n"
        f"Способ: <b>{payments.method_title(method)}</b>\n"
        f"Доступно: <b>{fmt(_available(user, source))}</b>\n\n"
        f"Комиссия будет удержана из суммы вывода.",
        reply_markup=kb.cancel(),
    )
    await call.answer()


@router.message(WithdrawSG.amount)
async def got_amount(message: Message, user: User, state: FSMContext) -> None:
    amount = parse_amount(message.text or "")
    if amount is None:
        await message.answer("❌ Введите сумму числом, например <code>25</code>.")
        return

    data = await state.get_data()
    method = PayMethod(data["method"])
    source = Account(data["source"])

    available = _available(user, source)
    if amount > available:
        await message.answer(f"❌ Недостаточно средств: доступно {fmt(available)}.")
        return

    await state.update_data(amount=str(amount))

    if method is PayMethod.CRYPTOBOT:
        # Выплата приходит на тот же Telegram-аккаунт через @CryptoBot.
        await state.update_data(destination=str(user.tg_id))
        await _show_confirmation(message, user, state)
        return

    await state.set_state(WithdrawSG.destination)
    hint = "начинается с <code>T</code>, 34 символа" if method is PayMethod.TRC20 else "начинается с <code>0x</code>, 42 символа"
    await message.answer(
        f"📬 Пришлите адрес кошелька <b>{payments.method_title(method)}</b>\n\n"
        f"Формат: {hint}\n"
        f"⚠️ Проверьте сеть — перевод в другую сеть вернуть невозможно."
    )


@router.message(WithdrawSG.destination)
async def got_destination(message: Message, user: User, state: FSMContext) -> None:
    data = await state.get_data()
    method = PayMethod(data["method"])

    try:
        destination = withdrawals.validate_destination(method, message.text or "")
    except WithdrawError as exc:
        await message.answer(f"❌ {exc}")
        return

    await state.update_data(destination=destination)
    await _show_confirmation(message, user, state)


async def _show_confirmation(message: Message, user: User, state: FSMContext) -> None:
    data = await state.get_data()
    method = PayMethod(data["method"])
    source = Account(data["source"])
    amount = Decimal(data["amount"])
    destination = data["destination"]

    fee = withdrawals.calc_fee(amount, method, source)
    net = withdrawals.calc_net(amount, method, source)

    where = "депозита" if source is Account.DEPOSIT else "баланса"
    target = "ваш аккаунт CryptoBot" if method is PayMethod.CRYPTOBOT else f"<code>{esc(destination)}</code>"

    await state.set_state(None)
    await message.answer(
        f"📤 <b>Подтверждение вывода</b>\n\n"
        f"Источник: {where}\n"
        f"Способ: <b>{payments.method_title(method)}</b>\n"
        f"Получатель: {target}\n\n"
        f"Списывается: <b>{fmt(amount)}</b>\n"
        f"Комиссия: <b>{fmt(fee)}</b>\n"
        f"К получению: <b>{fmt(net)}</b>",
        reply_markup=kb.confirm_withdraw(method.value, source.value),
    )


@router.callback_query(WithdrawCB.filter(F.action == "confirm"))
async def confirm(
    call: CallbackQuery,
    callback_data: WithdrawCB,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    if "amount" not in data or "destination" not in data:
        await call.answer("Заявка устарела, начните заново", show_alert=True)
        return

    method = PayMethod(callback_data.method)
    source = Account(callback_data.source)
    amount = Decimal(data["amount"])

    try:
        request = await withdrawals.create_request(session, user, amount, method, data["destination"], source)
    except WithdrawError as exc:
        await state.clear()
        await call.message.edit_text(f"❌ {exc}", reply_markup=kb.back_only())
        await call.answer()
        return

    await state.clear()
    await call.answer()

    paid_automatically = False
    try:
        paid_automatically = await withdrawals.try_auto_payout(session, request)
    except Exception:  # noqa: BLE001 - остаётся ручная обработка
        log.exception("Автовыплата по заявке #%s не удалась", request.id)

    if paid_automatically:
        await call.message.edit_text(
            f"✅ <b>Выплата отправлена</b>\n\n"
            f"Заявка #{request.id}\n"
            f"Получено: <b>{fmt(request.net_amount)}</b>\n"
            f"Проверьте @CryptoBot.",
            reply_markup=kb.back_only(),
        )
        return

    await call.message.edit_text(
        f"✅ <b>Заявка #{request.id} создана</b>\n\n"
        f"Сумма к выплате: <b>{fmt(request.net_amount)}</b>\n"
        f"Способ: {payments.method_title(method)}\n\n"
        f"Заявка отправлена администратору. Уведомление придёт сюда же.",
        reply_markup=kb.back_only(),
    )

    await notify_admins(
        call.bot, session,
        f"📤 <b>Новая заявка на вывод #{request.id}</b>\n\n"
        f"Пользователь: {user.mention} (<code>{user.tg_id}</code>)\n"
        f"Источник: {'депозит' if source is Account.DEPOSIT else 'баланс'}\n"
        f"Способ: {payments.method_title(method)}\n"
        f"Списано: {fmt(request.amount)} · комиссия {fmt(request.fee)}\n"
        f"К отправке: <b>{fmt(request.net_amount)}</b>\n"
        f"Реквизиты: <code>{esc(request.destination)}</code>",
    )

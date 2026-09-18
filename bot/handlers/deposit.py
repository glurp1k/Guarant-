"""Страховой депозит: просмотр, пополнение, снятие."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.keyboards import user as kb
from bot.keyboards.callbacks import DepositCB
from bot.services import deposits
from bot.services.placeholders import user_values
from bot.services.deposits import DepositError
from bot.services.settings import settings
from bot.states import DepositSG
from bot.utils.money import ZERO, fmt, parse_amount
from bot.utils.render import Event, deny, screen
from bot.utils.texts import fmt_date

router = Router(name="deposit")


async def render_deposit_menu(event: Event, user: User) -> None:
    if not settings.get_bool("deposit_enabled"):
        await deny(event, "Страховой депозит сейчас отключён")
        return

    unlock_at = deposits.locked_until(user)
    await screen(
        event, "deposit", kb.deposit_menu(user.deposit > ZERO),
        **user_values(user),
        min=fmt(settings.get_decimal("deposit_min")),
        max=fmt(settings.get_decimal("deposit_max")),
        locked_until=fmt_date(unlock_at) if unlock_at else "—",
    )


@router.callback_query(DepositCB.filter(F.action == "menu"))
async def deposit_menu(call: CallbackQuery, user: User, state: FSMContext) -> None:
    await state.clear()
    await render_deposit_menu(call, user)


@router.callback_query(DepositCB.filter(F.action == "from_balance"))
async def ask_from_balance(call: CallbackQuery, user: User, state: FSMContext) -> None:
    if not settings.get_bool("deposit_from_balance"):
        await call.answer("Перевод с баланса отключён", show_alert=True)
        return

    await state.set_state(DepositSG.to_deposit)
    await call.message.edit_text(
        f"🔁 <b>Перевод с баланса в депозит</b>\n\n"
        f"Доступно на балансе: <b>{fmt(user.balance)}</b>\n\n"
        f"Введите сумму:",
        reply_markup=kb.cancel(),
    )
    await call.answer()


@router.message(DepositSG.to_deposit)
async def do_from_balance(message: Message, session: AsyncSession, user: User, state: FSMContext) -> None:
    amount = parse_amount(message.text or "")
    if amount is None:
        await message.answer("❌ Введите сумму числом, например <code>50</code>.")
        return

    try:
        moved = await deposits.top_up_from_balance(session, user, amount)
    except DepositError as exc:
        await message.answer(f"❌ {exc}")
        return

    await state.clear()
    await message.answer(
        f"✅ В депозит переведено <b>{fmt(moved)}</b>\n\n"
        f"🛡 Депозит: <b>{fmt(user.deposit)}</b>\n"
        f"💰 Баланс: <b>{fmt(user.balance)}</b>",
        reply_markup=kb.main_menu(user.is_admin),
    )


@router.callback_query(DepositCB.filter(F.action == "to_balance"))
async def ask_to_balance(call: CallbackQuery, user: User, state: FSMContext) -> None:
    if not settings.get_bool("deposit_withdraw_enabled"):
        await call.answer("Снятие депозита отключено", show_alert=True)
        return

    unlock_at = deposits.locked_until(user)
    if unlock_at is not None:
        await call.answer(f"Депозит заморожен до {fmt_date(unlock_at)} UTC", show_alert=True)
        return

    fee_percent = settings.get_decimal("deposit_withdraw_fee_percent")
    fee_note = f"\nКомиссия за снятие: <b>{fee_percent}%</b>" if fee_percent > ZERO else ""

    await state.set_state(DepositSG.to_balance)
    await call.message.edit_text(
        f"↩️ <b>Снятие депозита на баланс</b>\n\n"
        f"В депозите: <b>{fmt(user.deposit)}</b>{fee_note}\n\n"
        f"Введите сумму:",
        reply_markup=kb.cancel(),
    )
    await call.answer()


@router.message(DepositSG.to_balance)
async def do_to_balance(message: Message, session: AsyncSession, user: User, state: FSMContext) -> None:
    amount = parse_amount(message.text or "")
    if amount is None:
        await message.answer("❌ Введите сумму числом, например <code>50</code>.")
        return

    try:
        credited = await deposits.move_to_balance(session, user, amount)
    except DepositError as exc:
        await message.answer(f"❌ {exc}")
        return

    await state.clear()
    await message.answer(
        f"✅ На баланс зачислено <b>{fmt(credited)}</b>\n\n"
        f"🛡 Депозит: <b>{fmt(user.deposit)}</b>\n"
        f"💰 Баланс: <b>{fmt(user.balance)}</b>",
        reply_markup=kb.main_menu(user.is_admin),
    )

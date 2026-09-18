"""Главное меню, профиль, информация, история операций."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Account, TxKind, User
from bot.keyboards import reply as rkb
from bot.keyboards import user as kb
from bot.keyboards.callbacks import MenuCB
from bot.services import deals as deals_service
from bot.services import deposits, ledger
from bot.services.settings import settings
from bot.utils.money import fmt
from bot.utils.render import Event, show
from bot.utils.style import block, mono, note, title, tree
from bot.utils.texts import esc, fmt_date

log = logging.getLogger(__name__)
router = Router(name="common")

KIND_TITLES = {
    TxKind.TOPUP.value: "Пополнение",
    TxKind.WITHDRAW.value: "Вывод",
    TxKind.WITHDRAW_REFUND.value: "Возврат вывода",
    TxKind.DEPOSIT_IN.value: "Депозит +",
    TxKind.DEPOSIT_OUT.value: "Депозит −",
    TxKind.DEAL_HOLD.value: "Оплата сделки",
    TxKind.DEAL_RELEASE.value: "Выплата по сделке",
    TxKind.DEAL_REFUND.value: "Возврат по сделке",
    TxKind.COMMISSION.value: "Комиссия",
    TxKind.ADMIN_CREDIT.value: "Начисление админом",
    TxKind.ADMIN_DEBIT.value: "Списание админом",
}


def greeting(user: User) -> str:
    return settings.get("text_start").replace("{name}", esc(user.full_name or "друг"))


def profile_text(user: User) -> str:
    account_rows = [("Баланс", mono(fmt(user.balance)))]
    if settings.get_bool("deposit_enabled"):
        account_rows.append(("Страховой депозит", mono(fmt(user.deposit))))

    parts = [
        title("👤", "Профиль") + "\n" + tree([
            ("Имя", esc(user.full_name) or "—"),
            ("Юзернейм", mono(f"@{esc(user.username)}") if user.username else "—"),
            ("ID", mono(user.tg_id)),
            ("В сервисе с", fmt_date(user.created_at)),
        ]),
        title("💰", "Счёт") + "\n" + tree(account_rows),
        title("🤝", "Сделки") + "\n" + tree([
            ("Закрыто", mono(user.deals_done)),
            ("Оборот", mono(fmt(user.deals_volume))),
        ]),
    ]

    unlock_at = deposits.locked_until(user)
    if unlock_at is not None:
        parts.append(note("🔒", f"Депозит заморожен до {fmt_date(unlock_at)} UTC"))

    return block(*parts)


# --------------------------------------------------------------------------- #
# Экраны. Каждый открывается и с инлайн-кнопки, и с нижней клавиатуры.
# --------------------------------------------------------------------------- #


async def render_main(event: Event, user: User, state: FSMContext) -> None:
    await state.clear()
    await show(event, greeting(user), kb.main_menu(user.is_admin))


async def render_profile(event: Event, user: User, state: FSMContext) -> None:
    await state.clear()
    await show(event, profile_text(user), kb.profile_menu())


async def render_info(event: Event) -> None:
    await show(event, settings.get("text_info"), kb.info_menu())


async def render_rules(event: Event) -> None:
    await show(event, settings.get("text_rules"), kb.back_only())


async def render_projects(event: Event) -> None:
    await show(event, settings.get("text_projects"), kb.back_only())


async def render_history(event: Event, session: AsyncSession, user: User) -> None:
    entries = await ledger.history(session, user.tg_id, limit=15)

    if not entries:
        text = block(title("🧾", "История операций"), "Пока пусто.")
    else:
        rows = []
        for entry in entries:
            sign = "＋" if entry.amount > 0 else "−"
            account = "🛡" if entry.account == Account.DEPOSIT.value else "💰"
            kind = KIND_TITLES.get(entry.kind, entry.kind)
            rows.append(f"{account} {sign}{abs(entry.amount):.2f} — {kind} · {fmt_date(entry.created_at)}")
        text = block(title("🧾", "История операций"), "\n".join(rows))

    await show(event, text, kb.profile_menu())


# --------------------------------------------------------------------------- #
# Точки входа
# --------------------------------------------------------------------------- #


async def greet(message: Message, user: User) -> None:
    """Приветствие вместе с постоянной клавиатурой внизу экрана."""
    await message.answer(greeting(user), reply_markup=rkb.main_keyboard(user.is_admin))


@router.message(CommandStart(deep_link=True))
async def start_deep_link(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    """Вход по ссылке-приглашению в сделку: /start deal_XXXXXXXX."""
    await state.clear()
    payload = (command.args or "").strip()

    if payload.lower().startswith("deal_"):
        from bot.handlers.deals import show_join_offer  # локальный импорт: избегаем цикла

        deal = await deals_service.by_code(session, payload[5:])
        if deal is not None:
            await greet(message, user)
            await show_join_offer(message, session, user, deal)
            return
        await message.answer("❌ Сделка не найдена или ссылка устарела.")

    await greet(message, user)


@router.message(CommandStart())
async def start(message: Message, user: User, state: FSMContext) -> None:
    await state.clear()
    await greet(message, user)


@router.message(Command("menu"))
async def menu_command(message: Message, user: User, state: FSMContext) -> None:
    await state.clear()
    await greet(message, user)


@router.callback_query(MenuCB.filter(F.action == "main"))
async def main_menu(call: CallbackQuery, user: User, state: FSMContext) -> None:
    await render_main(call, user, state)


@router.callback_query(MenuCB.filter(F.action == "cancel"))
async def cancel(call: CallbackQuery, state: FSMContext) -> None:
    """Инлайн «✕ Отмена» под диалогом: выходим, навигация остаётся внизу."""
    await state.clear()
    await show(call, "✕ Отменено.", None, toast="Отменено")


@router.callback_query(MenuCB.filter(F.action == "profile"))
async def show_profile(call: CallbackQuery, user: User, state: FSMContext) -> None:
    await render_profile(call, user, state)


@router.callback_query(MenuCB.filter(F.action == "info"))
async def show_info(call: CallbackQuery) -> None:
    await render_info(call)


@router.callback_query(MenuCB.filter(F.action == "rules"))
async def show_rules(call: CallbackQuery) -> None:
    await render_rules(call)


@router.callback_query(MenuCB.filter(F.action == "projects"))
async def show_projects(call: CallbackQuery) -> None:
    await render_projects(call)


@router.callback_query(MenuCB.filter(F.action == "history"))
async def show_history(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    await render_history(call, session, user)


@router.callback_query(MenuCB.filter(F.action == "deposit"))
async def to_deposit_menu(call: CallbackQuery, user: User, state: FSMContext) -> None:
    from bot.handlers.deposit import render_deposit_menu

    await state.clear()
    await render_deposit_menu(call, user)

"""Проверка пользователя по юзернейму.

Ввёл @username — увидел страховой депозит, количество сделок и статус
доверия. Состав карточки настраивается в админке (раздел «Проверка»).
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.keyboards import user as kb
from bot.keyboards.callbacks import MenuCB
from bot.services import deposits, users as users_service
from bot.services.settings import settings
from bot.states import CheckSG
from bot.utils.render import Event, deny, show
from bot.utils.texts import esc, normalize_username

router = Router(name="check")

PROMPT = (
    "🔍 <b>Проверка пользователя</b>\n\n"
    "Пришлите юзернейм — покажу страховой депозит и историю сделок.\n\n"
    "Например: <code>@username</code>"
)

NOT_FOUND = (
    "❌ <b>Пользователь не найден</b>\n\n"
    "Юзернейм <b>{username}</b> не зарегистрирован в сервисе: "
    "страхового депозита и истории сделок у него нет.\n\n"
    "⚠️ Будьте осторожны — работайте только через гаранта."
)


async def _reply_card(message: Message, session: AsyncSession, raw: str) -> None:
    username = normalize_username(raw)
    if username is None:
        await message.answer(
            "❌ Это не похоже на юзернейм.\n"
            "Пришлите в формате <code>@username</code> (латиница, цифры и «_», от 4 символов)."
        )
        return

    target = await users_service.find_by_username(session, username)
    if target is None:
        await message.answer(NOT_FOUND.format(username=f"@{esc(username)}"), reply_markup=kb.back_only())
        return

    await message.answer(deposits.build_card(target), reply_markup=kb.back_only())


async def render_check_prompt(event: Event, state: FSMContext) -> None:
    if not settings.get_bool("check_enabled"):
        await deny(event, "Проверка сейчас отключена")
        return

    await state.set_state(CheckSG.username)
    await show(event, PROMPT, kb.back_only())


@router.callback_query(MenuCB.filter(F.action == "check"))
async def ask_username(call: CallbackQuery, state: FSMContext) -> None:
    await render_check_prompt(call, state)


@router.message(CheckSG.username)
async def check_from_state(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not settings.get_bool("check_enabled"):
        await state.clear()
        await message.answer("Проверка сейчас отключена.")
        return
    await _reply_card(message, session, message.text or "")


@router.message(Command("check"))
async def check_command(message: Message, command: CommandObject, session: AsyncSession, state: FSMContext) -> None:
    """Быстрая проверка: /check @username"""
    if not settings.get_bool("check_enabled"):
        await message.answer("Проверка сейчас отключена.")
        return

    if not command.args:
        await state.set_state(CheckSG.username)
        await message.answer(PROMPT)
        return

    await _reply_card(message, session, command.args)


@router.message(StateFilter(None), F.text.regexp(r"^@[A-Za-z0-9_]{4,32}$"))
async def check_by_bare_username(message: Message, session: AsyncSession, user: User) -> None:
    """Просто прислали @username вне диалога — тоже показываем карточку."""
    if not settings.get_bool("check_enabled"):
        return
    await _reply_card(message, session, message.text or "")

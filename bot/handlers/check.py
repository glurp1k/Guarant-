"""Проверка пользователя.

Ввёл ID, юзернейм или ссылку — увидел страховой депозит, историю сделок
и статус доверия. Состав карточки настраивается в админке («Проверка»).
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import user as kb
from bot.keyboards.callbacks import MenuCB
from bot.services import deposits, users as users_service
from bot.services.placeholders import user_values
from bot.services.templates import templates
from bot.services.settings import settings
from bot.states import CheckSG
from bot.utils.render import Event, deny, reply, screen
from bot.utils.texts import esc

router = Router(name="check")

async def render_check_prompt(event: Event, state: FSMContext) -> None:
    if not settings.get_bool("check_enabled"):
        await deny(event, "Проверка сейчас отключена")
        return

    await state.set_state(CheckSG.username)
    await screen(event, "check_prompt", kb.cancel())


async def _reply_card(message: Message, session: AsyncSession, raw: str) -> None:
    query = (raw or "").strip()
    target = await users_service.find_any(session, query)

    if target is None:
        text, photo = templates.render("check_not_found", query=esc(query[:64]) or "—")
        await reply(message, text, kb.cancel(), photo=photo)
        return

    values = user_values(target)
    values["status_line"] = deposits.trust_badge(target)
    text, photo = templates.render("check_found", **values)
    await reply(message, text, kb.back_only(), photo=photo)


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
    """Быстрая проверка: /check @username, /check 8561401973"""
    if not settings.get_bool("check_enabled"):
        await message.answer("Проверка сейчас отключена.")
        return

    if not command.args:
        await render_check_prompt(message, state)
        return

    await _reply_card(message, session, command.args)


@router.message(StateFilter(None), F.text.regexp(r"^@[A-Za-z0-9_]{4,32}$"))
async def check_by_bare_username(message: Message, session: AsyncSession) -> None:
    """Просто прислали @username вне диалога — тоже показываем карточку."""
    if not settings.get_bool("check_enabled"):
        return
    await _reply_card(message, session, message.text or "")

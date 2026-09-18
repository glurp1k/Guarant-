"""Обработка кнопок постоянной клавиатуры.

Роутер подключается первым: нажатие на нижнюю клавиатуру должно уводить
из любого диалога, а не попадать в него ответом.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.keyboards.reply import BUTTONS
from bot.services.settings import settings

router = Router(name="reply-menu")


@router.message(F.text == BUTTONS["deals"])
async def open_deals(message: Message, state: FSMContext) -> None:
    from bot.handlers.deals import render_deals_menu

    await state.clear()
    await render_deals_menu(message)


@router.message(F.text == BUTTONS["balance"])
async def open_balance(message: Message, user: User, state: FSMContext) -> None:
    from bot.handlers.common import render_balance

    await render_balance(message, user, state)


@router.message(F.text == BUTTONS["deposit"])
async def open_deposit(message: Message, user: User, state: FSMContext) -> None:
    from bot.handlers.deposit import render_deposit_menu

    await state.clear()
    await render_deposit_menu(message, user)


@router.message(F.text == BUTTONS["check"])
async def open_check(message: Message, state: FSMContext) -> None:
    from bot.handlers.check import render_check_prompt

    await render_check_prompt(message, state)


@router.message(F.text == BUTTONS["rules"])
async def open_rules(message: Message, state: FSMContext) -> None:
    from bot.handlers.common import render_rules

    await state.clear()
    await render_rules(message)


@router.message(F.text == BUTTONS["support"])
async def open_support(message: Message, state: FSMContext) -> None:
    await state.clear()
    support = settings.get("support_username").strip().lstrip("@")
    if not support:
        await message.answer("Поддержка пока не указана.")
        return
    await message.answer(f"💬 Поддержка: @{support}")


@router.message(F.text == BUTTONS["admin"])
async def open_admin(message: Message, session: AsyncSession, user: User, state: FSMContext) -> None:
    from bot.handlers.admin.menu import render_admin_menu

    if not user.is_admin:
        return
    await render_admin_menu(message, state)

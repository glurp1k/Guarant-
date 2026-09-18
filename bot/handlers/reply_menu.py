"""Кнопки нижней клавиатуры.

Роутер подключается первым: нижняя клавиатура видна всегда, её нажатие
должно уводить из любого диалога, а не попадать в него ответом.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.filters_reply import ReplyButton
from bot.keyboards import reply as rkb
from bot.keyboards import user as kb
from bot.services.settings import settings

router = Router(name="reply-menu")


@router.message(ReplyButton(rkb.DEAL))
async def open_deal(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not settings.get_bool("deal_enabled"):
        await message.answer("Создание сделок временно отключено.")
        return
    await message.answer("Кем вы выступаете в сделке?", reply_markup=kb.deal_roles())


@router.message(ReplyButton(rkb.DEPOSIT))
async def open_deposit(message: Message, user: User, state: FSMContext) -> None:
    from bot.handlers.deposit import render_deposit_menu

    await state.clear()
    await render_deposit_menu(message, user)


@router.message(ReplyButton(rkb.CHECK))
async def open_check(message: Message, state: FSMContext) -> None:
    from bot.handlers.check import render_check_prompt

    await render_check_prompt(message, state)


@router.message(ReplyButton(rkb.PROFILE))
async def open_profile(message: Message, session: AsyncSession, user: User, state: FSMContext) -> None:
    from bot.handlers.common import render_profile

    await render_profile(message, session, user, state)


@router.message(ReplyButton(rkb.INFO))
async def open_info(message: Message, state: FSMContext) -> None:
    from bot.handlers.common import render_info

    await state.clear()
    await render_info(message)


@router.message(ReplyButton(rkb.PROJECTS))
async def open_projects(message: Message, state: FSMContext) -> None:
    from bot.handlers.common import render_projects

    await state.clear()
    await render_projects(message)


@router.message(ReplyButton(rkb.ADMIN))
async def open_admin(message: Message, user: User, state: FSMContext) -> None:
    from bot.handlers.admin.menu import render_admin_menu

    if not user.is_admin:
        return
    await render_admin_menu(message, state)

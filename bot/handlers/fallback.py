"""Последний роутер: отвечает на то, что не разобрали остальные.

Нужен, чтобы у пользователя не «зависала» кнопка и чтобы случайный текст
не оставался без ответа.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot.db.models import User
from bot.keyboards import user as kb

log = logging.getLogger(__name__)
router = Router(name="fallback")


@router.callback_query()
async def unknown_callback(call: CallbackQuery) -> None:
    log.debug("Необработанная кнопка: %s", call.data)
    await call.answer("Кнопка устарела — откройте меню заново", show_alert=True)


@router.message(F.text)
async def unknown_text(message: Message, user: User) -> None:
    await message.answer(
        "Не понял команду. Вот главное меню:",
        reply_markup=kb.main_menu(user.is_admin),
    )

"""Показ экрана из любого источника.

Один и тот же раздел открывается двумя путями: нажатием инлайн-кнопки
(тогда правим текущее сообщение) и нажатием кнопки на реплай-клавиатуре
(тогда шлём новое). Чтобы не дублировать каждый экран, хендлеры зовут
`show()` и не думают, откуда пришёл пользователь.
"""

from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

log = logging.getLogger(__name__)

Event = Message | CallbackQuery


async def show(
    event: Event,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
    *,
    toast: str | None = None,
) -> None:
    """Открыть экран: правкой сообщения или новым — смотря откуда пришли."""
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as exc:
            # Повторное нажатие той же кнопки — Telegram отвечает
            # «message is not modified». Это не ошибка, экран уже открыт.
            if "message is not modified" not in str(exc):
                raise
        await event.answer(toast or "")
        return

    await event.answer(text, reply_markup=markup)


async def reply(event: Event, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Прислать новое сообщение независимо от источника.

    Нужно там, где ответ дополняет экран, а не заменяет его: карточка
    проверки, созданный счёт, подтверждение операции.
    """
    target = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(event, CallbackQuery):
        await event.answer()
    await target.answer(text, reply_markup=markup)


def actor(event: Event) -> Message:
    """Сообщение, рядом с которым отвечаем."""
    return event.message if isinstance(event, CallbackQuery) else event


async def deny(event: Event, text: str) -> None:
    """Отказ: из инлайна — всплывашкой, из текста — сообщением."""
    if isinstance(event, CallbackQuery):
        await event.answer(text[:200], show_alert=True)
        return
    await event.answer(text)

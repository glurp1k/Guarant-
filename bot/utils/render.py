"""Показ экрана из любого источника.

Один и тот же раздел открывается двумя путями: нажатием инлайн-кнопки
(тогда правим текущее сообщение) и нажатием кнопки на реплай-клавиатуре
(тогда шлём новое). Чтобы не дублировать каждый экран, хендлеры зовут
`show()` и не думают, откуда пришёл пользователь.
"""

from __future__ import annotations

import logging
import re

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

log = logging.getLogger(__name__)

Event = Message | CallbackQuery

CUSTOM_EMOJI_TAG = re.compile(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", re.DOTALL | re.IGNORECASE)


def strip_custom_emoji(text: str) -> str:
    """Заменить премиум-эмодзи на обычное, которое лежит внутри тега."""
    return CUSTOM_EMOJI_TAG.sub(r"\1", text)


def _is_emoji_error(exc: TelegramBadRequest) -> bool:
    """Telegram отказал из-за премиум-эмодзи.

    Отправлять <tg-emoji> может только бот с юзернеймом, купленным на
    Fragment. Если админ вставил премиум-эмодзи в иконку раздела, а права
    нет, отклоняется всё сообщение — и экран перестаёт открываться.
    """
    return "emoji" in str(exc).lower()


async def _send(coro_factory, text: str):
    """Отправить, а при отказе из-за эмодзи — повторить без премиум-эмодзи."""
    try:
        return await coro_factory(text)
    except TelegramBadRequest as exc:
        if not _is_emoji_error(exc):
            raise
        fallback = strip_custom_emoji(text)
        if fallback == text:
            raise
        log.warning("Премиум-эмодзи отклонено Telegram, отправляю без него: %s", exc)
        return await coro_factory(fallback)


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
            await _send(lambda body: event.message.edit_text(body, reply_markup=markup), text)
        except TelegramBadRequest as exc:
            # Повторное нажатие той же кнопки — Telegram отвечает
            # «message is not modified». Это не ошибка, экран уже открыт.
            if "message is not modified" not in str(exc):
                raise
        await event.answer(toast or "")
        return

    await _send(lambda body: event.answer(body, reply_markup=markup), text)


async def reply(event: Event, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Прислать новое сообщение независимо от источника.

    Нужно там, где ответ дополняет экран, а не заменяет его: карточка
    проверки, созданный счёт, подтверждение операции.
    """
    target = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(event, CallbackQuery):
        await event.answer()
    await _send(lambda body: target.answer(body, reply_markup=markup), text)


def actor(event: Event) -> Message:
    """Сообщение, рядом с которым отвечаем."""
    return event.message if isinstance(event, CallbackQuery) else event


async def deny(event: Event, text: str) -> None:
    """Отказ: из инлайна — всплывашкой, из текста — сообщением."""
    if isinstance(event, CallbackQuery):
        await event.answer(text[:200], show_alert=True)
        return
    await event.answer(text)

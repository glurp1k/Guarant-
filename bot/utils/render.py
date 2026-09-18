"""Показ экрана из любого источника.

Один и тот же раздел открывается двумя путями: нажатием инлайн-кнопки
(тогда правим текущее сообщение) и нажатием кнопки на нижней клавиатуре
(тогда шлём новое). Чтобы не дублировать каждый экран, хендлеры зовут
`show()` и не думают, откуда пришёл пользователь.

Здесь же страховка на премиум-эмодзи: отправлять их может не каждый бот,
а отказ Telegram кладёт всё сообщение целиком. Поэтому при таком отказе
текст и клавиатура переотправляются без премиум-эмодзи.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, ReplyKeyboardMarkup

from bot.utils.style import strip_custom_emoji

log = logging.getLogger(__name__)

Event = Message | CallbackQuery
Markup = InlineKeyboardMarkup | ReplyKeyboardMarkup | None


def strip_markup_icons(markup: Markup) -> Markup:
    """Снять премиум-иконки с кнопок, оставив подписи как есть."""
    if isinstance(markup, InlineKeyboardMarkup):
        rows = markup.inline_keyboard
    elif isinstance(markup, ReplyKeyboardMarkup):
        rows = markup.keyboard
    else:
        return markup

    if not any(getattr(button, "icon_custom_emoji_id", None) for row in rows for button in row):
        return markup

    cleaned = [
        [
            button.model_copy(update={"icon_custom_emoji_id": None})
            if getattr(button, "icon_custom_emoji_id", None)
            else button
            for button in row
        ]
        for row in rows
    ]

    if isinstance(markup, InlineKeyboardMarkup):
        return markup.model_copy(update={"inline_keyboard": cleaned})
    return markup.model_copy(update={"keyboard": cleaned})


def _mentions_emoji(exc: TelegramBadRequest) -> bool:
    """Telegram отказал из-за премиум-эмодзи.

    Слать премиум-эмодзи — и в тексте, и иконкой на кнопке — может не
    каждый бот. Отказ прилетает на всё сообщение, поэтому ловим его и
    повторяем без премиум-эмодзи, чтобы экран открылся хоть как-то.
    """
    return "emoji" in str(exc).lower()


async def _send(
    call: Callable[[str, Markup], Awaitable[Any]],
    text: str,
    markup: Markup,
) -> Any:
    try:
        return await call(text, markup)
    except TelegramBadRequest as exc:
        if not _mentions_emoji(exc):
            raise
        plain_text = strip_custom_emoji(text)
        plain_markup = strip_markup_icons(markup)
        if plain_text == text and plain_markup is markup:
            raise
        log.warning("Премиум-эмодзи отклонено Telegram, отправляю без него: %s", exc)
        return await call(plain_text, plain_markup)


async def show(event: Event, text: str, markup: Markup = None, *, toast: str | None = None) -> None:
    """Открыть экран: правкой сообщения или новым — смотря откуда пришли."""
    if isinstance(event, CallbackQuery):
        try:
            await _send(
                lambda body, kb: event.message.edit_text(body, reply_markup=kb),
                text, markup,
            )
        except TelegramBadRequest as exc:
            # Повторное нажатие той же кнопки — Telegram отвечает
            # «message is not modified». Это не ошибка, экран уже открыт.
            if "message is not modified" not in str(exc):
                raise
        await event.answer(toast or "")
        return

    await _send(lambda body, kb: event.answer(body, reply_markup=kb), text, markup)


async def reply(event: Event, text: str, markup: Markup = None) -> None:
    """Прислать новое сообщение независимо от источника.

    Нужно там, где ответ дополняет экран, а не заменяет его: карточка
    проверки, созданный счёт, подтверждение операции.
    """
    target = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(event, CallbackQuery):
        await event.answer()
    await _send(lambda body, kb: target.answer(body, reply_markup=kb), text, markup)


async def deny(event: Event, text: str) -> None:
    """Отказ: из инлайна — всплывашкой, из текста — сообщением."""
    if isinstance(event, CallbackQuery):
        await event.answer(text[:200], show_alert=True)
        return
    await event.answer(text)


def actor(event: Event) -> Message:
    """Сообщение, рядом с которым отвечаем."""
    return event.message if isinstance(event, CallbackQuery) else event

"""Фильтр нажатия кнопки нижней клавиатуры.

Подписи кнопок меняются в админке, поэтому сравнивать надо с текущим
значением настройки, а не с константой, зашитой при импорте.
"""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import Message

from bot.keyboards.reply import button_text


class ReplyButton(BaseFilter):
    def __init__(self, key: str) -> None:
        self.key = key

    async def __call__(self, message: Message) -> bool:  # noqa: D102
        current = button_text(self.key)
        return bool(current) and (message.text or "").strip() == current

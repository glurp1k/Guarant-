"""Фильтры доступа."""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from bot.db.models import User


class IsAdmin(BaseFilter):
    """Пропускает только администраторов.

    Фильтр обязан быть без побочных эффектов: он проверяется для каждого
    апдейта, дошедшего до админского роутера, в том числе для чужих кнопок.
    Пользователь уже загружен внешним middleware, поэтому в БД не ходим.
    """

    async def __call__(self, event: TelegramObject, user: User | None = None) -> bool:  # noqa: D102
        return user is not None and user.is_admin

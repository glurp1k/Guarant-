"""Загрузка пользователя, бан-лист и режим технических работ."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User as TgUser

from bot.services import users as users_service
from bot.services.settings import settings
from bot.services.templates import templates
from bot.utils.texts import esc


class UserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        if tg_user is None or tg_user.is_bot:
            return await handler(event, data)

        session = data["session"]
        user = await users_service.get_or_create(
            session,
            tg_id=tg_user.id,
            username=tg_user.username,
            full_name=tg_user.full_name,
        )
        data["user"] = user
        data["is_admin"] = user.is_admin

        if user.is_banned:
            reason = f"\nПричина: {esc(user.ban_reason)}" if user.ban_reason else ""
            await self._reject(event, f"⛔️ Доступ к боту закрыт.{reason}")
            return None

        if settings.get_bool("maintenance") and not user.is_admin:
            await self._reject(event, templates.text("maintenance"))
            return None

        return await handler(event, data)

    @staticmethod
    async def _reject(event: TelegramObject, text: str) -> None:
        if isinstance(event, CallbackQuery):
            await event.answer(text[:200], show_alert=True)
        elif isinstance(event, Message):
            await event.answer(text)

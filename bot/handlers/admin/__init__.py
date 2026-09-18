"""Админка. Все роутеры здесь закрыты фильтром IsAdmin."""

from __future__ import annotations

from aiogram import Router

from bot.filters import IsAdmin
from bot.handlers.admin import menu, moderation, settings_panel, templates_panel, users

router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

router.include_router(menu.router)
router.include_router(settings_panel.router)
router.include_router(templates_panel.router)
router.include_router(moderation.router)
router.include_router(users.router)

__all__ = ["router"]

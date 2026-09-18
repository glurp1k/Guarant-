"""Сборка роутеров.

Порядок важен: админские роутеры идут первыми, чтобы их состояния FSM
не перехватывались пользовательскими обработчиками текста.
"""

from __future__ import annotations

from aiogram import Router

from bot.handlers import check, common, deals, deposit, fallback, topup, withdraw
from bot.handlers.admin import router as admin_router


def build_router() -> Router:
    router = Router(name="root")
    router.include_router(admin_router)
    router.include_router(common.router)
    router.include_router(topup.router)
    router.include_router(deposit.router)
    router.include_router(withdraw.router)
    router.include_router(check.router)
    router.include_router(deals.router)
    router.include_router(fallback.router)
    return router

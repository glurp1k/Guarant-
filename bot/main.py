"""Точка входа."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from bot.config import get_config
from bot.db import base as db_base
from bot.db import close_db, init_db, session_scope
from bot.handlers import build_router
from bot.middlewares import DbSessionMiddleware, UserMiddleware
from bot.session import build_session
from bot.services import deals as deals_service
from bot.services.settings import settings
from bot.services.templates import templates
from bot.services.watcher import run_watcher

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="check", description="Проверить пользователя по юзернейму"),
    BotCommand(command="admin", description="Панель администратора"),
]


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


async def deals_housekeeper() -> None:
    """Раз в 10 минут отменяет сделки, которые так и не оплатили."""
    while True:
        await asyncio.sleep(600)
        try:
            async with session_scope() as session:
                stale = await deals_service.expire_unpaid(session)
            if stale:
                log.info("Автоотмена сделок: %s", len(stale))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - задача не должна умирать
            log.exception("Ошибка автоотмены сделок")


async def main() -> None:
    config = get_config()
    setup_logging(config.log_level)

    await init_db()
    async with session_scope() as session:
        await settings.load(session)
        await templates.load(session)

    bot = Bot(
        token=config.bot_token,
        session=build_session(ca_file=config.ssl_cert_file, proxy=config.bot_proxy),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())

    # Именно outer: фильтры (например, IsAdmin) проверяются раньше внутренних
    # middleware, поэтому user и session должны попасть в данные до фильтров.
    for observer in (dispatcher.message, dispatcher.callback_query):
        observer.outer_middleware(DbSessionMiddleware(db_base.sessionmaker))
        observer.outer_middleware(UserMiddleware())

    dispatcher.include_router(build_router())

    background = [
        asyncio.create_task(run_watcher(bot)),
        asyncio.create_task(deals_housekeeper()),
    ]

    try:
        await bot.set_my_commands(COMMANDS)
        me = await bot.me()
        log.info("Бот запущен: @%s", me.username)

        await bot.delete_webhook(drop_pending_updates=True)
        await dispatcher.start_polling(bot)
    finally:
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        await bot.session.close()
        await close_db()
        log.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass

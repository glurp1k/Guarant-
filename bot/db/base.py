"""Движок БД и фабрика сессий."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from bot.config import get_config

log = logging.getLogger(__name__)

_engine = None
sessionmaker: async_sessionmaker[AsyncSession] | None = None


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    """Создать движок, файл БД (для SQLite) и недостающие таблицы."""
    global _engine, sessionmaker

    config = get_config()

    sqlite_path = config.sqlite_path
    if sqlite_path is not None:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    _engine = create_async_engine(config.database_url, echo=False, pool_pre_ping=True)
    sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)

    from bot.db import models  # noqa: F401 - регистрация моделей в метаданных

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    log.info("База данных готова: %s", config.database_url)


async def close_db() -> None:
    if _engine is not None:
        await _engine.dispose()


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Сессия с автокоммитом — для фоновых задач вне хендлеров."""
    if sessionmaker is None:
        raise RuntimeError("init_db() не был вызван")
    async with sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

"""Конфигурация уровня процесса.

Здесь живёт только то, без чего бот не стартует: токен, админы, БД.
Всё, что должно меняться на лету (адреса кошельков, проценты, лимиты,
тексты), хранится в таблице `settings` и правится из админки.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str
    database_url: str = "sqlite+aiosqlite:///data/guarant.db"
    log_level: str = "INFO"

    # Прокси до api.telegram.org. Нужен там, где Telegram закрыт.
    # Для работы требуется пакет aiohttp-socks.
    bot_proxy: str = ""

    # Свой центр сертификации: корпоративная сеть, песочница, свой egress.
    # Переменная стандартная, её же читают curl, pip и прочие.
    ssl_cert_file: str = Field(default="", validation_alias="SSL_CERT_FILE")

    # Строкой, а не списком: так значение читается одинаково и из .env,
    # и из переменных окружения, без JSON-синтаксиса.
    admin_ids_raw: str = Field(default="", validation_alias="ADMIN_IDS")

    @property
    def admin_ids(self) -> list[int]:
        """Главные администраторы — их права нельзя снять из бота."""
        result = []
        for part in self.admin_ids_raw.replace(";", ",").split(","):
            part = part.strip()
            if part.lstrip("-").isdigit():
                result.append(int(part))
        return result

    @property
    def sqlite_path(self) -> Path | None:
        """Путь к файлу SQLite, если используется SQLite."""
        prefix = "sqlite+aiosqlite:///"
        if not self.database_url.startswith(prefix):
            return None
        return BASE_DIR / self.database_url[len(prefix):]


@lru_cache
def get_config() -> Config:
    return Config()

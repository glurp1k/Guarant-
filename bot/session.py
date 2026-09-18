"""Сессия Telegram с учётом окружения.

aiogram создаёт HTTPS-сессию с жёстко прибитым бандлом certifi:

    self._connector_init = {"ssl": ssl.create_default_context(cafile=certifi.where())}

Из-за этого стандартная переменная SSL_CERT_FILE не работает, и бот не
поднимается там, где трафик идёт через прокси с собственным центром
сертификации: корпоративная сеть, песочница, свой egress. Поэтому берём
сертификаты из окружения, если оно их задаёт.
"""

from __future__ import annotations

import logging
import ssl

from aiogram.client.session.aiohttp import AiohttpSession

log = logging.getLogger(__name__)


class EnvAwareSession(AiohttpSession):
    """Сессия, уважающая SSL_CERT_FILE и прокси из настроек."""

    def __init__(self, ca_file: str | None = None, proxy: str | None = None) -> None:
        super().__init__(proxy=proxy or None)

        if ca_file:
            # _connector_init — то, с чем aiogram создаёт TCPConnector.
            # Публичной точки для своего SSL-контекста у него нет.
            self._connector_init["ssl"] = ssl.create_default_context(cafile=ca_file)
            log.info("TLS: доверяем сертификатам из %s", ca_file)

        if proxy:
            log.info("Telegram API через прокси %s", proxy)


def build_session(ca_file: str | None = None, proxy: str | None = None) -> AiohttpSession:
    return EnvAwareSession(ca_file=ca_file, proxy=proxy)

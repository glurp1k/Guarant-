"""Клиент Crypto Pay API (@CryptoBot).

Документация: https://help.crypt.bot/crypto-pay-api
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

MAINNET_URL = "https://pay.crypt.bot/api"
TESTNET_URL = "https://testnet-pay.crypt.bot/api"
TIMEOUT = aiohttp.ClientTimeout(total=25)


class CryptoBotError(Exception):
    """Ошибка на стороне Crypto Pay."""


class CryptoBotClient:
    def __init__(self, token: str, testnet: bool = False) -> None:
        if not token:
            raise CryptoBotError("Токен Crypto Pay не задан в админке")
        self._token = token
        self._base = TESTNET_URL if testnet else MAINNET_URL

    async def _call(self, method: str, **params: Any) -> Any:
        payload = {k: v for k, v in params.items() if v is not None}
        headers = {"Crypto-Pay-API-Token": self._token}

        async with aiohttp.ClientSession(timeout=TIMEOUT) as http:
            async with http.post(f"{self._base}/{method}", json=payload, headers=headers) as response:
                try:
                    data = await response.json(content_type=None)
                except Exception as exc:  # noqa: BLE001 - ответ может быть не-JSON
                    raise CryptoBotError(f"Некорректный ответ Crypto Pay ({response.status})") from exc

        if not isinstance(data, dict) or not data.get("ok"):
            error = (data or {}).get("error") if isinstance(data, dict) else None
            name = (error or {}).get("name") if isinstance(error, dict) else error
            raise CryptoBotError(str(name or "неизвестная ошибка Crypto Pay"))

        return data.get("result")

    # ------------------------------------------------------------------ #

    async def get_me(self) -> dict:
        """Проверка токена — используется кнопкой «Проверить» в админке."""
        return await self._call("getMe")

    async def get_balance(self) -> dict[str, Decimal]:
        result = await self._call("getBalance") or []
        return {item["currency_code"]: Decimal(str(item["available"])) for item in result}

    async def create_invoice(
        self,
        amount: Decimal,
        asset: str = "USDT",
        description: str | None = None,
        payload: str | None = None,
        expires_in: int | None = None,
    ) -> dict:
        return await self._call(
            "createInvoice",
            currency_type="crypto",
            asset=asset,
            amount=f"{amount:.6f}".rstrip("0").rstrip("."),
            description=description,
            payload=payload,
            expires_in=expires_in,
            allow_comments=False,
            allow_anonymous=False,
        )

    async def get_invoices(self, invoice_ids: list[str] | None = None, status: str | None = None) -> list[dict]:
        result = await self._call(
            "getInvoices",
            invoice_ids=",".join(invoice_ids) if invoice_ids else None,
            status=status,
            count=1000,
        )
        if isinstance(result, dict):
            return result.get("items", [])
        return result or []

    async def transfer(self, user_id: int, amount: Decimal, spend_id: str, asset: str = "USDT",
                       comment: str | None = None) -> dict:
        """Прямой перевод на кошелёк CryptoBot.

        Работает, только если получатель хотя бы раз запускал @CryptoBot.
        `spend_id` защищает от двойной отправки при повторе запроса.
        """
        return await self._call(
            "transfer",
            user_id=user_id,
            asset=asset,
            amount=f"{amount:.6f}".rstrip("0").rstrip("."),
            spend_id=spend_id,
            comment=comment,
            disable_send_notification=False,
        )

    async def create_check(self, amount: Decimal, asset: str = "USDT", pin_to_user_id: int | None = None) -> dict:
        """Чек-ссылка. Запасной вариант, когда transfer недоступен."""
        return await self._call(
            "createCheck",
            asset=asset,
            amount=f"{amount:.6f}".rstrip("0").rstrip("."),
            pin_to_user_id=pin_to_user_id,
        )

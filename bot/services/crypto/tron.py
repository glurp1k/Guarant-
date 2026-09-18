"""Чтение входящих USDT TRC20 через TronGrid.

Приватные ключи бот не хранит: сеть только читается, выплаты подтверждает
администратор. Ключ TronGrid необязателен, но без него быстро упираемся
в публичные лимиты.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

import aiohttp

from bot.services.crypto.types import IncomingTransfer

log = logging.getLogger(__name__)

BASE_URL = "https://api.trongrid.io"
USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
USDT_DECIMALS = Decimal(10) ** 6
TIMEOUT = aiohttp.ClientTimeout(total=25)


class TronClient:
    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    async def incoming_usdt(self, address: str, limit: int = 50) -> list[IncomingTransfer]:
        """Последние входящие переводы USDT на адрес."""
        if not address:
            return []

        params = {
            "only_to": "true",
            "limit": str(min(limit, 200)),
            "contract_address": USDT_CONTRACT,
            "order_by": "block_timestamp,desc",
        }
        headers = {"TRON-PRO-API-KEY": self._api_key} if self._api_key else {}
        url = f"{BASE_URL}/v1/accounts/{address}/transactions/trc20"

        async with aiohttp.ClientSession(timeout=TIMEOUT) as http:
            async with http.get(url, params=params, headers=headers) as response:
                if response.status != 200:
                    log.warning("TronGrid ответил %s для %s", response.status, address)
                    return []
                data = await response.json(content_type=None)

        if not isinstance(data, dict) or not data.get("success", True):
            log.warning("TronGrid вернул ошибку: %s", data)
            return []

        transfers: list[IncomingTransfer] = []
        for item in data.get("data", []):
            try:
                if item.get("type") not in (None, "Transfer"):
                    continue
                if (item.get("to") or "").strip() != address:
                    continue
                token = item.get("token_info") or {}
                decimals = Decimal(10) ** int(token.get("decimals", 6))
                transfers.append(
                    IncomingTransfer(
                        tx_hash=item["transaction_id"],
                        amount=Decimal(str(item["value"])) / decimals,
                        from_address=item.get("from", ""),
                        to_address=item.get("to", ""),
                        timestamp=datetime.fromtimestamp(int(item["block_timestamp"]) / 1000, tz=timezone.utc),
                    )
                )
            except (KeyError, ValueError, TypeError, ArithmeticError):
                log.debug("Пропущен некорректный TRC20-перевод: %s", item)

        return transfers

    @staticmethod
    def is_valid_address(address: str) -> bool:
        address = (address or "").strip()
        return len(address) == 34 and address.startswith("T") and address.isalnum()

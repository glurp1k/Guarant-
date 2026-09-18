"""Чтение входящих USDT BEP20 через Etherscan V2 (BscScan).

Etherscan V2 обслуживает BNB Chain по chainid=56 тем же ключом, что и
остальные сети. Ключ обязателен — без него API отдаёт ошибку лимита.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

import aiohttp

from bot.services.crypto.types import IncomingTransfer

log = logging.getLogger(__name__)

BASE_URL = "https://api.etherscan.io/v2/api"
CHAIN_ID = 56
USDT_CONTRACT = "0x55d398326f99059ff775485246999027b3197955"  # BSC-USD, 18 знаков
TIMEOUT = aiohttp.ClientTimeout(total=25)


class BscClient:
    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    async def incoming_usdt(self, address: str, limit: int = 50) -> list[IncomingTransfer]:
        if not address or not self._api_key:
            if address and not self._api_key:
                log.warning("Не задан ключ BscScan — BEP20 платежи не отслеживаются")
            return []

        params = {
            "chainid": str(CHAIN_ID),
            "module": "account",
            "action": "tokentx",
            "contractaddress": USDT_CONTRACT,
            "address": address,
            "page": "1",
            "offset": str(min(limit, 100)),
            "sort": "desc",
            "apikey": self._api_key,
        }

        async with aiohttp.ClientSession(timeout=TIMEOUT) as http:
            async with http.get(BASE_URL, params=params) as response:
                if response.status != 200:
                    log.warning("BscScan ответил %s", response.status)
                    return []
                data = await response.json(content_type=None)

        if not isinstance(data, dict):
            return []
        if str(data.get("status")) != "1":
            message = data.get("message", "")
            # «No transactions found» — обычная ситуация для пустого кошелька.
            if "No transactions found" not in str(message):
                log.warning("BscScan вернул ошибку: %s / %s", message, data.get("result"))
            return []

        transfers: list[IncomingTransfer] = []
        for item in data.get("result", []):
            try:
                if (item.get("to") or "").lower() != address.lower():
                    continue
                decimals = Decimal(10) ** int(item.get("tokenDecimal", 18))
                transfers.append(
                    IncomingTransfer(
                        tx_hash=item["hash"],
                        amount=Decimal(str(item["value"])) / decimals,
                        from_address=item.get("from", ""),
                        to_address=item.get("to", ""),
                        timestamp=datetime.fromtimestamp(int(item["timeStamp"]), tz=timezone.utc),
                        confirmations=int(item.get("confirmations", 0) or 0),
                    )
                )
            except (KeyError, ValueError, TypeError, ArithmeticError):
                log.debug("Пропущен некорректный BEP20-перевод: %s", item)

        return transfers

    @staticmethod
    def is_valid_address(address: str) -> bool:
        address = (address or "").strip()
        if not address.startswith("0x") or len(address) != 42:
            return False
        try:
            int(address[2:], 16)
        except ValueError:
            return False
        return True

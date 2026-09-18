"""Общие типы для крипто-клиентов."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class IncomingTransfer:
    """Входящий перевод USDT, приведённый к единому виду."""

    tx_hash: str
    amount: Decimal
    from_address: str
    to_address: str
    timestamp: datetime
    confirmations: int = 1

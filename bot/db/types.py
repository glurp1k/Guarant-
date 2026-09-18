"""Денежный тип.

Деньги нельзя хранить во float. Сумма пишется в БД как целое число
микро-единиц (6 знаков после запятой), а в Python отдаётся как Decimal.
Так арифметика остаётся точной, а SQL-сортировки и SUM() продолжают
работать на числовой колонке.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import BigInteger
from sqlalchemy.types import TypeDecorator

SCALE = Decimal(10) ** 6
QUANT = Decimal("0.000001")


class Money(TypeDecorator):  # noqa: D101 - см. модуль
    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001, D102
        if value is None:
            return None
        if not isinstance(value, Decimal):
            value = Decimal(str(value))
        return int(value.quantize(QUANT) * SCALE)

    def process_result_value(self, value, dialect):  # noqa: ANN001, D102
        if value is None:
            return None
        return (Decimal(value) / SCALE).quantize(QUANT)


def from_micro(value: int | None) -> Decimal:
    """Перевести сырые микро-единицы (например, результат SUM()) в Decimal."""
    if not value:
        return Decimal("0")
    return (Decimal(value) / SCALE).quantize(QUANT)

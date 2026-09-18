"""Работа с суммами."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP

CENT = Decimal("0.01")
MICRO = Decimal("0.000001")
ZERO = Decimal("0")


def q2(value: Decimal) -> Decimal:
    """Округлить до 2 знаков (для отображения и списаний)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def floor2(value: Decimal) -> Decimal:
    """Округлить вниз — используется там, где нельзя выдать лишнее."""
    return value.quantize(CENT, rounding=ROUND_DOWN)


def fmt(value: Decimal | int | float, asset: str = "USDT") -> str:
    """`12.5` → `12.50 USDT`."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    text = f"{q2(value):.2f}"
    return f"{text} {asset}" if asset else text


def parse_amount(text: str) -> Decimal | None:
    """Разобрать сумму из пользовательского ввода. None — если это не сумма."""
    cleaned = (text or "").strip().replace(",", ".").replace(" ", "")
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or value <= 0:
        return None
    return q2(value)


def percent_of(amount: Decimal, percent: Decimal) -> Decimal:
    return q2(amount * percent / Decimal(100))

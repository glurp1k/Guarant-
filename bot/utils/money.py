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


def plain(value: Decimal | int | float) -> str:
    """Голое число с двумя знаками, без символа валюты."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return f"{q2(value):.2f}"


def fmt(value: Decimal | int | float) -> str:
    """Сумма в оформлении из админки: `$ 12,50` или `12.50 USDT`.

    Символ, его сторона и разделитель дробной части настраиваются
    в разделе «Основное». Импорт настроек внутри функции — чтобы
    utils не зависел от services на уровне модуля.
    """
    from bot.services.settings import settings

    text = plain(value)
    if settings.get_bool("currency_comma"):
        text = text.replace(".", ",")

    symbol = settings.get("currency_symbol").strip()
    if not symbol:
        return text
    if settings.get("currency_position").strip() == "after":
        return f"{text} {symbol}"
    return f"{symbol} {text}"


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

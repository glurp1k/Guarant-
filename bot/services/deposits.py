"""Страховой депозит.

Депозит — это замороженная сумма, которая видна другим пользователям
при проверке по юзернейму. Пополнить его можно криптой напрямую
(см. payments.py) или переводом с баланса.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Account, Deal, DealStatus, TxKind, User
from bot.services import ledger
from bot.services.settings import settings
from bot.utils.money import ZERO, floor2, q2
from bot.utils.style import block, mono, note, title, tree
from bot.utils.texts import esc, fmt_date


class DepositError(Exception):
    """Операция с депозитом невозможна — текст показывается пользователю."""


def _ensure_enabled() -> None:
    if not settings.get_bool("deposit_enabled"):
        raise DepositError("Страховой депозит сейчас отключён")


def locked_until(user: User) -> datetime | None:
    value = user.deposit_locked_until
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value if value > datetime.now(timezone.utc) else None


async def top_up_from_balance(session: AsyncSession, user: User, amount: Decimal) -> Decimal:
    """Перевести сумму с баланса в страховой депозит."""
    _ensure_enabled()
    if not settings.get_bool("deposit_from_balance"):
        raise DepositError("Пополнение депозита с баланса отключено")

    amount = q2(amount)
    minimum = settings.get_decimal("deposit_min")
    if minimum > ZERO and amount < minimum:
        raise DepositError(f"Минимальное пополнение депозита — {minimum:.2f} USDT")

    maximum = settings.get_decimal("deposit_max")
    if maximum > ZERO and (user.deposit + amount) > maximum:
        raise DepositError(f"Максимальный размер депозита — {maximum:.2f} USDT")

    if user.balance < amount:
        raise DepositError(f"На балансе только {user.balance:.2f} USDT")

    await ledger.debit(session, user, amount, TxKind.DEPOSIT_IN,
                       account=Account.BALANCE, comment="Перевод в страховой депозит")
    await ledger.credit(session, user, amount, TxKind.DEPOSIT_IN,
                        account=Account.DEPOSIT, comment="Пополнение с баланса")

    lock_days = settings.get_int("deposit_lock_days")
    if lock_days > 0:
        user.deposit_locked_until = datetime.now(timezone.utc) + timedelta(days=lock_days)

    await session.commit()
    return amount


async def move_to_balance(session: AsyncSession, user: User, amount: Decimal) -> Decimal:
    """Снять часть депозита обратно на баланс. Возвращает зачисленную сумму."""
    _ensure_enabled()
    if not settings.get_bool("deposit_withdraw_enabled"):
        raise DepositError("Снятие депозита сейчас отключено")

    unlock_at = locked_until(user)
    if unlock_at is not None:
        raise DepositError(f"Депозит заморожен до {unlock_at:%d.%m.%Y %H:%M} UTC")

    amount = q2(amount)
    if amount > user.deposit:
        raise DepositError(f"В депозите только {user.deposit:.2f} USDT")

    active = await session.scalar(
        select(func.count(Deal.id)).where(
            or_(Deal.seller_id == user.tg_id, Deal.buyer_id == user.tg_id),
            Deal.status.in_((DealStatus.FUNDED.value, DealStatus.DISPUTE.value)),
        )
    )
    if active:
        raise DepositError("Нельзя снять депозит, пока есть активные сделки — дождитесь их завершения")

    percent = settings.get_decimal("deposit_withdraw_fee_percent")
    fee = q2(amount * percent / Decimal(100))
    net = floor2(amount - fee)
    if net <= ZERO:
        raise DepositError(f"Сумма слишком мала: комиссия составит {fee:.2f} USDT")

    await ledger.debit(session, user, amount, TxKind.DEPOSIT_OUT,
                       account=Account.DEPOSIT, comment="Снятие депозита на баланс")
    await ledger.credit(session, user, net, TxKind.DEPOSIT_OUT,
                        account=Account.BALANCE,
                        comment=f"Снятие депозита (комиссия {fee:.2f} USDT)" if fee > ZERO else "Снятие депозита")

    await session.commit()
    return net


# --------------------------------------------------------------------------- #
# Карточка проверки по юзернейму
# --------------------------------------------------------------------------- #


def trust_badge(user: User) -> str:
    if user.is_banned:
        return "⛔️ <b>В чёрном списке</b>"
    if user.is_verified:
        return "🔷 <b>Верифицирован администрацией</b>"

    threshold = settings.get_decimal("check_trusted_from")
    if threshold > ZERO and user.deposit >= threshold:
        return "✅ <b>Надёжный</b> — депозит выше порога доверия"
    if user.deposit > ZERO:
        return "🟡 <b>Есть депозит</b>"
    return "⚪️ <b>Без депозита</b> — работайте только через гаранта"


def trust_label(user: User) -> str:
    """Короткий статус — для строки в профиле."""
    if user.is_banned:
        return "в чёрном списке"
    if user.is_verified:
        return "верифицирован"

    threshold = settings.get_decimal("check_trusted_from")
    if threshold > ZERO and user.deposit >= threshold:
        return "надёжный"
    return "есть депозит" if user.deposit > ZERO else "без депозита"


def build_card(user: User) -> str:
    """Публичная карточка пользователя. Состав полей задаётся в админке."""
    identity = [
        ("Имя", esc(user.full_name) or "—"),
        ("Юзернейм", mono(f"@{esc(user.username)}") if user.username else "—"),
        ("ID", mono(user.tg_id)),
    ]
    if settings.get_bool("check_show_registered"):
        identity.append(("В сервисе с", fmt_date(user.created_at)))

    guarantees: list[tuple[str, object]] = []
    if settings.get_bool("check_show_deposit"):
        guarantees.append(("Страховой депозит", mono(f"{user.deposit:.2f} USDT")))
    if settings.get_bool("check_show_deals"):
        guarantees.append(("Закрытых сделок", mono(user.deals_done)))
        guarantees.append(("Оборот", mono(f"{user.deals_volume:.2f} USDT")))

    parts = [
        title("🔍", "Проверка пользователя") + "\n" + tree(identity),
        trust_badge(user),
    ]
    if guarantees:
        parts.append(title("🛡", "Гарантии") + "\n" + tree(guarantees))
    if user.is_banned and user.ban_reason:
        parts.append(note("⚠️", f"Причина блокировки: {esc(user.ban_reason)}"))

    return block(*parts)

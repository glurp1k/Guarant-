"""Вывод средств.

Два сценария, оба настраиваются в админке:
  • CryptoBot — можно включить автовыплату: бот сам создаёт перевод/чек;
  • напрямую (TRC20 / BEP20) — заявка уходит администратору, он платит
    со своего кошелька и отмечает заявку выполненной.

Приватные ключи в боте не хранятся, поэтому on-chain выплаты
подтверждает человек. Деньги списываются сразу при создании заявки
и возвращаются, если заявку отклонили.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Account, PayMethod, TxKind, User, Withdrawal, WithdrawStatus
from bot.services import ledger, payments
from bot.services.crypto import BscClient, TronClient
from bot.services.settings import settings
from bot.utils.money import ZERO, floor2, q2

log = logging.getLogger(__name__)


class WithdrawError(Exception):
    """Вывод невозможен — текст показывается пользователю."""


def method_enabled(method: PayMethod) -> bool:
    return settings.get_bool(f"withdraw_{method.value}_enabled")


def calc_fee(amount: Decimal, method: PayMethod, source: Account = Account.BALANCE) -> Decimal:
    """Комиссия сервиса: процент по способу + фикс за сеть (+ процент за снятие депозита)."""
    percent = settings.get_decimal(f"withdraw_fee_{method.value}_percent")
    if source is Account.DEPOSIT:
        percent += settings.get_decimal("deposit_withdraw_fee_percent")
    fixed = settings.get_decimal(f"withdraw_fee_{method.value}_fixed")
    return q2(amount * percent / Decimal(100) + fixed)


def calc_net(amount: Decimal, method: PayMethod, source: Account = Account.BALANCE) -> Decimal:
    return floor2(amount - calc_fee(amount, method, source))


def validate_destination(method: PayMethod, destination: str) -> str:
    destination = (destination or "").strip()
    if method is PayMethod.TRC20:
        if not TronClient.is_valid_address(destination):
            raise WithdrawError("Это не похоже на адрес TRC20 (должен начинаться с T и быть длиной 34 символа)")
    elif method is PayMethod.BEP20:
        if not BscClient.is_valid_address(destination):
            raise WithdrawError("Это не похоже на адрес BEP20 (должен начинаться с 0x и быть длиной 42 символа)")
    return destination


def _check_deposit_lock(user: User) -> None:
    if not settings.get_bool("deposit_withdraw_enabled"):
        raise WithdrawError("Вывод страхового депозита сейчас отключён")
    locked_until = user.deposit_locked_until
    if locked_until is None:
        return
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    if locked_until > datetime.now(timezone.utc):
        raise WithdrawError(f"Депозит заморожен до {locked_until:%d.%m.%Y %H:%M} UTC")


async def create_request(
    session: AsyncSession,
    user: User,
    amount: Decimal,
    method: PayMethod,
    destination: str,
    source: Account = Account.BALANCE,
) -> Withdrawal:
    """Создать заявку на вывод и сразу списать сумму со счёта."""
    if not settings.get_bool("withdraw_enabled"):
        raise WithdrawError("Вывод временно отключён")
    if not method_enabled(method):
        raise WithdrawError(f"Вывод через «{payments.method_title(method)}» сейчас недоступен")

    amount = q2(amount)
    minimum = settings.get_decimal("withdraw_min")
    maximum = settings.get_decimal("withdraw_max")
    if minimum > ZERO and amount < minimum:
        raise WithdrawError(f"Минимальная сумма вывода — {minimum:.2f} USDT")
    if maximum > ZERO and amount > maximum:
        raise WithdrawError(f"Максимальная сумма вывода за раз — {maximum:.2f} USDT")

    if source is Account.DEPOSIT:
        _check_deposit_lock(user)
        available = user.deposit
    else:
        available = user.balance

    if amount > available:
        raise WithdrawError(f"Недостаточно средств: доступно {available:.2f} USDT")

    fee = calc_fee(amount, method, source)
    net = floor2(amount - fee)
    if net <= ZERO:
        raise WithdrawError(f"Сумма слишком мала: комиссия составит {fee:.2f} USDT")

    destination = validate_destination(method, destination)

    kind = TxKind.DEPOSIT_OUT if source is Account.DEPOSIT else TxKind.WITHDRAW
    await ledger.debit(
        session, user, amount, kind,
        account=source,
        comment=f"Заявка на вывод ({payments.method_title(method)})",
    )

    request = Withdrawal(
        user_id=user.tg_id,
        source=source.value,
        method=method.value,
        destination=destination,
        amount=amount,
        fee=fee,
        net_amount=net,
    )
    session.add(request)
    await session.commit()
    await session.refresh(request)

    log.info("Заявка на вывод #%s: %s USDT, %s", request.id, amount, method)
    return request


async def mark_paid(
    session: AsyncSession,
    request: Withdrawal,
    admin_id: int | None = None,
    tx_hash: str | None = None,
    comment: str | None = None,
) -> None:
    """Отметить заявку выполненной. Средства уже списаны при создании."""
    if request.status != WithdrawStatus.PENDING.value:
        raise WithdrawError("Заявка уже обработана")
    request.status = WithdrawStatus.PAID.value
    request.admin_id = admin_id
    request.tx_hash = tx_hash
    request.admin_comment = comment
    request.processed_at = datetime.now(timezone.utc)
    await session.commit()


async def reject(
    session: AsyncSession,
    request: Withdrawal,
    admin_id: int | None = None,
    comment: str | None = None,
) -> None:
    """Отклонить заявку и вернуть сумму на исходный счёт."""
    if request.status != WithdrawStatus.PENDING.value:
        raise WithdrawError("Заявка уже обработана")

    user = await session.get(User, request.user_id)
    if user is not None:
        source = Account(request.source)
        kind = TxKind.DEPOSIT_IN if source is Account.DEPOSIT else TxKind.WITHDRAW_REFUND
        await ledger.credit(
            session, user, request.amount, kind,
            account=source,
            comment=f"Возврат по отклонённой заявке #{request.id}",
            ref_type="withdrawal", ref_id=request.id,
        )

    request.status = WithdrawStatus.REJECTED.value
    request.admin_id = admin_id
    request.admin_comment = comment
    request.processed_at = datetime.now(timezone.utc)
    await session.commit()


async def try_auto_payout(session: AsyncSession, request: Withdrawal) -> bool:
    """Автовыплата через CryptoBot, если она включена в админке.

    Сначала пробуем прямой перевод, он работает только для тех, кто уже
    запускал @CryptoBot. Если не вышло — выписываем именной чек.
    """
    if request.method != PayMethod.CRYPTOBOT.value:
        return False
    if not settings.get_bool("withdraw_auto_cryptobot"):
        return False

    client = payments.cryptobot_client()
    spend_id = f"wd{request.id}-{uuid.uuid4().hex[:8]}"

    try:
        result = await client.transfer(
            user_id=request.user_id,
            amount=request.net_amount,
            spend_id=spend_id,
            comment=f"Вывод #{request.id}",
        )
        await mark_paid(session, request, tx_hash=f"transfer:{result.get('transfer_id')}", comment="Автовыплата")
        return True
    except Exception as exc:  # noqa: BLE001 - падаем в запасной сценарий
        log.warning("Прямой перевод CryptoBot не прошёл (#%s): %s", request.id, exc)

    try:
        check = await client.create_check(amount=request.net_amount, pin_to_user_id=request.user_id)
        await mark_paid(
            session, request,
            tx_hash=check.get("bot_check_url") or f"check:{check.get('check_id')}",
            comment="Автовыплата чеком",
        )
        return True
    except Exception as exc:  # noqa: BLE001 - остаётся ручная обработка
        log.warning("Чек CryptoBot не создан (#%s): %s", request.id, exc)
        return False


async def pending_requests(session: AsyncSession, limit: int = 20, offset: int = 0) -> list[Withdrawal]:
    stmt = (
        select(Withdrawal)
        .where(Withdrawal.status == WithdrawStatus.PENDING.value)
        .order_by(Withdrawal.id)
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars().all())


async def user_requests(session: AsyncSession, user_id: int, limit: int = 10) -> list[Withdrawal]:
    stmt = select(Withdrawal).where(Withdrawal.user_id == user_id).order_by(Withdrawal.id.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())

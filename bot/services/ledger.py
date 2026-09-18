"""Движение средств по счетам пользователя.

Единственная точка, где меняются balance и deposit. Каждое изменение
пишется в журнал `transactions`, чтобы любую сумму можно было объяснить.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Account, Transaction, TxKind, User
from bot.utils.money import ZERO, q2


class InsufficientFunds(Exception):
    """Не хватает средств на счёте."""

    def __init__(self, available: Decimal, needed: Decimal) -> None:
        self.available = available
        self.needed = needed
        super().__init__(f"Недостаточно средств: есть {available}, нужно {needed}")


def _read(user: User, account: Account) -> Decimal:
    return user.deposit if account is Account.DEPOSIT else user.balance


def _write(user: User, account: Account, value: Decimal) -> None:
    if account is Account.DEPOSIT:
        user.deposit = value
    else:
        user.balance = value


async def apply(
    session: AsyncSession,
    user: User,
    *,
    amount: Decimal,
    kind: TxKind,
    account: Account = Account.BALANCE,
    comment: str = "",
    ref_type: str | None = None,
    ref_id: int | None = None,
) -> Transaction:
    """Изменить счёт на `amount` (со знаком) и записать это в журнал.

    Коммит остаётся на вызывающей стороне: так списание и создание заявки
    попадают в одну транзакцию БД.
    """
    amount = q2(amount)
    current = _read(user, account)
    new_value = q2(current + amount)

    if new_value < ZERO:
        raise InsufficientFunds(available=current, needed=abs(amount))

    _write(user, account, new_value)

    entry = Transaction(
        user_id=user.tg_id,
        kind=kind.value,
        account=account.value,
        amount=amount,
        balance_after=new_value,
        comment=comment[:255],
        ref_type=ref_type,
        ref_id=ref_id,
    )
    session.add(entry)
    return entry


async def credit(session: AsyncSession, user: User, amount: Decimal, kind: TxKind, **kwargs) -> Transaction:
    return await apply(session, user, amount=abs(q2(amount)), kind=kind, **kwargs)


async def debit(session: AsyncSession, user: User, amount: Decimal, kind: TxKind, **kwargs) -> Transaction:
    return await apply(session, user, amount=-abs(q2(amount)), kind=kind, **kwargs)


async def history(session: AsyncSession, user_id: int, limit: int = 10, offset: int = 0) -> list[Transaction]:
    stmt = (
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars().all())

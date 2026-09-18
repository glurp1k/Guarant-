"""Гарант-сделки.

Жизненный цикл:
    waiting_partner → waiting_payment → funded → completed
                    ↘ cancelled                ↘ refunded / dispute

Деньги покупателя списываются в момент оплаты сделки и лежат «у гаранта»
(не на счету ни одной из сторон) до подтверждения или решения админа.
"""

from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import (
    Account,
    CommissionPayer,
    Deal,
    DealRole,
    DealStatus,
    TxKind,
    User,
)
from bot.services import ledger
from bot.services.settings import settings
from bot.utils.money import ZERO, q2

log = logging.getLogger(__name__)

CODE_ALPHABET = string.ascii_uppercase + string.digits
ACTIVE_STATUSES = (
    DealStatus.WAITING_PARTNER.value,
    DealStatus.WAITING_PAYMENT.value,
    DealStatus.FUNDED.value,
    DealStatus.DISPUTE.value,
)


class DealError(Exception):
    """Действие со сделкой невозможно — текст показывается пользователю."""


def commission_for(amount: Decimal) -> Decimal:
    """Комиссия гаранта по настройкам из админки."""
    percent = settings.get_decimal("deal_commission_percent")
    value = q2(amount * percent / Decimal(100))

    minimum = settings.get_decimal("deal_commission_min")
    if minimum > ZERO:
        value = max(value, minimum)

    maximum = settings.get_decimal("deal_commission_max")
    if maximum > ZERO:
        value = min(value, maximum)

    return value


async def _generate_code(session: AsyncSession) -> str:
    for _ in range(30):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        exists = await session.scalar(select(Deal.id).where(Deal.code == code))
        if not exists:
            return code
    raise DealError("Не удалось создать сделку, попробуйте ещё раз")


async def create(
    session: AsyncSession,
    creator: User,
    amount: Decimal,
    description: str,
    role: DealRole,
    commission_payer: CommissionPayer | None = None,
) -> Deal:
    if not settings.get_bool("deal_enabled"):
        raise DealError("Создание сделок временно отключено")

    amount = q2(amount)
    minimum = settings.get_decimal("deal_min_amount")
    maximum = settings.get_decimal("deal_max_amount")
    if minimum > ZERO and amount < minimum:
        raise DealError(f"Минимальная сумма сделки — {minimum:.2f} USDT")
    if maximum > ZERO and amount > maximum:
        raise DealError(f"Максимальная сумма сделки — {maximum:.2f} USDT")

    required_deposit = settings.get_decimal("deposit_required_for_deal")
    if required_deposit > ZERO and creator.deposit < required_deposit:
        raise DealError(
            f"Для создания сделки нужен страховой депозит от {required_deposit:.2f} USDT.\n"
            f"Ваш депозит: {creator.deposit:.2f} USDT"
        )

    if commission_payer is None:
        raw = settings.get("deal_commission_payer").strip().lower()
        commission_payer = CommissionPayer(raw) if raw in {c.value for c in CommissionPayer} else CommissionPayer.SELLER

    deal = Deal(
        code=await _generate_code(session),
        creator_id=creator.tg_id,
        creator_role=role.value,
        seller_id=creator.tg_id if role is DealRole.SELLER else None,
        buyer_id=creator.tg_id if role is DealRole.BUYER else None,
        amount=amount,
        commission=commission_for(amount),
        commission_payer=commission_payer.value,
        description=description.strip()[:1000],
        status=DealStatus.WAITING_PARTNER.value,
    )
    session.add(deal)
    await session.commit()
    await session.refresh(deal)
    return deal


async def join(session: AsyncSession, deal: Deal, user: User) -> Deal:
    """Вторая сторона открыла ссылку и подтвердила участие."""
    if deal.status != DealStatus.WAITING_PARTNER.value:
        raise DealError("К этой сделке уже нельзя присоединиться")
    if user.tg_id == deal.creator_id:
        raise DealError("Нельзя участвовать в собственной сделке с двух сторон")

    if deal.seller_id is None:
        deal.seller_id = user.tg_id
    elif deal.buyer_id is None:
        deal.buyer_id = user.tg_id
    else:
        raise DealError("В сделке уже есть обе стороны")

    deal.status = DealStatus.WAITING_PAYMENT.value
    await session.commit()
    await session.refresh(deal)
    return deal


async def fund(session: AsyncSession, deal: Deal, buyer: User) -> Deal:
    """Покупатель оплачивает сделку — деньги уходят под удержание."""
    if deal.status != DealStatus.WAITING_PAYMENT.value:
        raise DealError("Сделка не ожидает оплаты")
    if buyer.tg_id != deal.buyer_id:
        raise DealError("Оплатить сделку может только покупатель")

    charge = q2(deal.buyer_charge)
    if buyer.balance < charge:
        raise DealError(
            f"Не хватает средств: нужно {charge:.2f} USDT, на балансе {buyer.balance:.2f} USDT.\n"
            f"Пополните баланс и попробуйте снова."
        )

    await ledger.debit(
        session, buyer, charge, TxKind.DEAL_HOLD,
        account=Account.BALANCE,
        comment=f"Оплата сделки {deal.code}",
        ref_type="deal", ref_id=deal.id,
    )

    deal.status = DealStatus.FUNDED.value
    deal.funded_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(deal)
    return deal


async def complete(session: AsyncSession, deal: Deal) -> Deal:
    """Покупатель подтвердил — деньги уходят продавцу за вычетом комиссии."""
    if deal.status not in (DealStatus.FUNDED.value, DealStatus.DISPUTE.value):
        raise DealError("Сделка не находится в стадии удержания средств")

    seller = await session.get(User, deal.seller_id) if deal.seller_id else None
    if seller is None:
        raise DealError("Продавец не найден")

    payout = q2(deal.seller_payout)
    await ledger.credit(
        session, seller, payout, TxKind.DEAL_RELEASE,
        account=Account.BALANCE,
        comment=f"Выплата по сделке {deal.code}",
        ref_type="deal", ref_id=deal.id,
    )

    buyer = await session.get(User, deal.buyer_id) if deal.buyer_id else None
    for participant in (seller, buyer):
        if participant is not None:
            participant.deals_done += 1
            participant.deals_volume = q2(participant.deals_volume + deal.amount)

    deal.status = DealStatus.COMPLETED.value
    deal.closed_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(deal)
    log.info("Сделка %s завершена, продавцу %s USDT", deal.code, payout)
    return deal


async def refund(session: AsyncSession, deal: Deal, reason: str = "") -> Deal:
    """Вернуть удержанные средства покупателю."""
    if deal.status not in (DealStatus.FUNDED.value, DealStatus.DISPUTE.value):
        raise DealError("По этой сделке нечего возвращать")

    buyer = await session.get(User, deal.buyer_id) if deal.buyer_id else None
    if buyer is None:
        raise DealError("Покупатель не найден")

    await ledger.credit(
        session, buyer, q2(deal.buyer_charge), TxKind.DEAL_REFUND,
        account=Account.BALANCE,
        comment=f"Возврат по сделке {deal.code}",
        ref_type="deal", ref_id=deal.id,
    )

    deal.status = DealStatus.REFUNDED.value
    deal.closed_at = datetime.now(timezone.utc)
    if reason:
        deal.admin_comment = reason[:1000]
    await session.commit()
    await session.refresh(deal)
    return deal


async def cancel(session: AsyncSession, deal: Deal, reason: str = "") -> Deal:
    """Отменить сделку до оплаты."""
    if deal.status not in (DealStatus.WAITING_PARTNER.value, DealStatus.WAITING_PAYMENT.value):
        raise DealError("Сделку уже нельзя отменить — обратитесь в поддержку")
    deal.status = DealStatus.CANCELLED.value
    deal.closed_at = datetime.now(timezone.utc)
    if reason:
        deal.admin_comment = reason[:1000]
    await session.commit()
    await session.refresh(deal)
    return deal


async def open_dispute(session: AsyncSession, deal: Deal, user: User, reason: str) -> Deal:
    if deal.status != DealStatus.FUNDED.value:
        raise DealError("Спор можно открыть только по оплаченной сделке")
    if user.tg_id not in (deal.seller_id, deal.buyer_id):
        raise DealError("Вы не участник этой сделки")

    deal.status = DealStatus.DISPUTE.value
    deal.dispute_reason = f"{user.mention}: {reason}"[:1000]
    await session.commit()
    await session.refresh(deal)
    return deal


async def by_code(session: AsyncSession, code: str) -> Deal | None:
    stmt = select(Deal).where(Deal.code == code.strip().upper())
    return (await session.execute(stmt)).scalar_one_or_none()


async def user_deals(session: AsyncSession, user_id: int, active_only: bool = False, limit: int = 10) -> list[Deal]:
    stmt = select(Deal).where(or_(Deal.seller_id == user_id, Deal.buyer_id == user_id))
    if active_only:
        stmt = stmt.where(Deal.status.in_(ACTIVE_STATUSES))
    stmt = stmt.order_by(Deal.id.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def disputes(session: AsyncSession, limit: int = 20) -> list[Deal]:
    stmt = select(Deal).where(Deal.status == DealStatus.DISPUTE.value).order_by(Deal.id).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def expire_unpaid(session: AsyncSession) -> list[Deal]:
    """Автоотмена сделок, которые так и не оплатили."""
    hours = settings.get_int("deal_auto_cancel_hours")
    if hours <= 0:
        return []

    deadline = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = select(Deal).where(
        Deal.status.in_((DealStatus.WAITING_PARTNER.value, DealStatus.WAITING_PAYMENT.value)),
        Deal.created_at < deadline,
    )
    stale = list((await session.execute(stmt)).scalars().all())
    for deal in stale:
        deal.status = DealStatus.CANCELLED.value
        deal.closed_at = datetime.now(timezone.utc)
        deal.admin_comment = "Автоотмена: сделка не была оплачена вовремя"
    if stale:
        await session.commit()
    return stale

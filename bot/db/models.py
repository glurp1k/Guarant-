"""Модели данных."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.db.base import Base
from bot.db.types import Money

ZERO = Decimal("0")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Справочники состояний
# --------------------------------------------------------------------------- #


class Account(str, Enum):
    """Счёт пользователя, по которому идёт движение средств."""

    BALANCE = "balance"
    DEPOSIT = "deposit"


class PayMethod(str, Enum):
    CRYPTOBOT = "cryptobot"
    TRC20 = "trc20"
    BEP20 = "bep20"


class InvoiceStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class WithdrawStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class DealRole(str, Enum):
    SELLER = "seller"
    BUYER = "buyer"


class CommissionPayer(str, Enum):
    SELLER = "seller"
    BUYER = "buyer"
    SPLIT = "split"


class DealStatus(str, Enum):
    WAITING_PARTNER = "waiting_partner"   # ждём вторую сторону по ссылке
    WAITING_PAYMENT = "waiting_payment"   # покупатель подтвердил, нужна оплата
    FUNDED = "funded"                     # деньги удержаны гарантом
    COMPLETED = "completed"               # средства отданы продавцу
    CANCELLED = "cancelled"               # отменена до оплаты
    REFUNDED = "refunded"                 # средства возвращены покупателю
    DISPUTE = "dispute"                   # спор, решает администрация


class TxKind(str, Enum):
    TOPUP = "topup"
    WITHDRAW = "withdraw"
    WITHDRAW_REFUND = "withdraw_refund"
    DEPOSIT_IN = "deposit_in"
    DEPOSIT_OUT = "deposit_out"
    DEAL_HOLD = "deal_hold"
    DEAL_RELEASE = "deal_release"
    DEAL_REFUND = "deal_refund"
    COMMISSION = "commission"
    ADMIN_CREDIT = "admin_credit"
    ADMIN_DEBIT = "admin_debit"


# --------------------------------------------------------------------------- #
# Таблицы
# --------------------------------------------------------------------------- #


class User(Base):
    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")

    balance: Mapped[Decimal] = mapped_column(Money, default=ZERO)
    deposit: Mapped[Decimal] = mapped_column(Money, default=ZERO)
    deposit_locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    deals_done: Mapped[int] = mapped_column(Integer, default=0)
    deals_volume: Mapped[Decimal] = mapped_column(Money, default=ZERO)

    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    ban_reason: Mapped[str | None] = mapped_column(String(255))
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def mention(self) -> str:
        return f"@{self.username}" if self.username else f"ID {self.tg_id}"


class Setting(Base):
    """Настройки, редактируемые прямо из админки."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by: Mapped[int | None] = mapped_column(BigInteger)


class Invoice(Base):
    """Счёт на пополнение баланса или страхового депозита."""

    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id"), index=True)

    purpose: Mapped[str] = mapped_column(String(16), default=Account.BALANCE.value)
    method: Mapped[str] = mapped_column(String(16))

    amount: Mapped[Decimal] = mapped_column(Money)       # сколько зачислим
    pay_amount: Mapped[Decimal] = mapped_column(Money)   # уникальная сумма к оплате
    asset: Mapped[str] = mapped_column(String(16), default="USDT")

    address: Mapped[str | None] = mapped_column(String(128))
    external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    pay_url: Mapped[str | None] = mapped_column(String(512))

    status: Mapped[str] = mapped_column(String(16), default=InvoiceStatus.PENDING.value, index=True)
    tx_hash: Mapped[str | None] = mapped_column(String(128), index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(lazy="selectin")


Index("ix_invoices_watch", Invoice.status, Invoice.method)


class Withdrawal(Base):
    """Заявка на вывод средств."""

    __tablename__ = "withdrawals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id"), index=True)

    source: Mapped[str] = mapped_column(String(16), default=Account.BALANCE.value)
    method: Mapped[str] = mapped_column(String(16))
    destination: Mapped[str] = mapped_column(String(128))  # адрес кошелька либо @username

    amount: Mapped[Decimal] = mapped_column(Money)       # списано со счёта
    fee: Mapped[Decimal] = mapped_column(Money, default=ZERO)
    net_amount: Mapped[Decimal] = mapped_column(Money)   # отправлено пользователю

    status: Mapped[str] = mapped_column(String(16), default=WithdrawStatus.PENDING.value, index=True)
    tx_hash: Mapped[str | None] = mapped_column(String(256))
    admin_id: Mapped[int | None] = mapped_column(BigInteger)
    admin_comment: Mapped[str | None] = mapped_column(String(512))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(lazy="selectin")


class Deal(Base):
    """Гарант-сделка."""

    __tablename__ = "deals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)

    creator_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id"), index=True)
    creator_role: Mapped[str] = mapped_column(String(8))
    seller_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.tg_id"), index=True)
    buyer_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.tg_id"), index=True)

    amount: Mapped[Decimal] = mapped_column(Money)
    commission: Mapped[Decimal] = mapped_column(Money, default=ZERO)
    commission_payer: Mapped[str] = mapped_column(String(8), default=CommissionPayer.SELLER.value)
    description: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[str] = mapped_column(String(20), default=DealStatus.WAITING_PARTNER.value, index=True)
    dispute_reason: Mapped[str | None] = mapped_column(Text)
    admin_comment: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    funded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    seller: Mapped[User | None] = relationship(foreign_keys=[seller_id], lazy="selectin")
    buyer: Mapped[User | None] = relationship(foreign_keys=[buyer_id], lazy="selectin")
    creator: Mapped[User] = relationship(foreign_keys=[creator_id], lazy="selectin")

    def counterpart_id(self, user_id: int) -> int | None:
        if user_id == self.seller_id:
            return self.buyer_id
        if user_id == self.buyer_id:
            return self.seller_id
        return None

    @property
    def seller_payout(self) -> Decimal:
        """Сколько получит продавец после удержания комиссии."""
        if self.commission_payer == CommissionPayer.BUYER.value:
            return self.amount
        if self.commission_payer == CommissionPayer.SPLIT.value:
            return self.amount - (self.commission / 2)
        return self.amount - self.commission

    @property
    def buyer_charge(self) -> Decimal:
        """Сколько спишется с покупателя при оплате сделки."""
        if self.commission_payer == CommissionPayer.SELLER.value:
            return self.amount
        if self.commission_payer == CommissionPayer.SPLIT.value:
            return self.amount + (self.commission / 2)
        return self.amount + self.commission


class Transaction(Base):
    """Журнал движения средств — источник правды для сверки."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id"), index=True)

    kind: Mapped[str] = mapped_column(String(24), index=True)
    account: Mapped[str] = mapped_column(String(16), default=Account.BALANCE.value)
    amount: Mapped[Decimal] = mapped_column(Money)          # со знаком
    balance_after: Mapped[Decimal] = mapped_column(Money)

    comment: Mapped[str] = mapped_column(String(255), default="")
    ref_type: Mapped[str | None] = mapped_column(String(16))
    ref_id: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

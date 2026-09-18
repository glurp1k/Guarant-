"""Пополнение баланса и страхового депозита.

Поддерживаются три способа, все крипто:
  • CryptoBot  — счёт создаётся через Crypto Pay, оплата отслеживается по его статусу;
  • USDT TRC20 — общий кошелёк, платёж опознаётся по уникальной сумме;
  • USDT BEP20 — так же, но в BNB Chain.

Уникальная сумма — это запрошенная сумма плюс случайные микро-центы
(10.00 → 10.004217). Такой «хвост» позволяет отличить платежи разных
пользователей на один и тот же адрес.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Account, Invoice, InvoiceStatus, PayMethod, TxKind, User
from bot.services import ledger
from bot.services.crypto import BscClient, CryptoBotClient, TronClient
from bot.services.settings import settings
from bot.utils.money import ZERO, q2

log = logging.getLogger(__name__)

TAIL_MAX = 999_999  # микро-хвост: до 0.999999 USDT


class PaymentError(Exception):
    """Пополнение невозможно — текст исключения показывается пользователю."""


# --------------------------------------------------------------------------- #
# Клиенты
# --------------------------------------------------------------------------- #


def cryptobot_client() -> CryptoBotClient:
    return CryptoBotClient(settings.get("cryptobot_token").strip())


def tron_client() -> TronClient:
    return TronClient(settings.get("trongrid_api_key").strip())


def bsc_client() -> BscClient:
    return BscClient(settings.get("bscscan_api_key").strip())


def method_enabled(method: PayMethod) -> bool:
    return settings.get_bool(f"topup_{method.value}_enabled")


def method_title(method: PayMethod) -> str:
    return {
        PayMethod.CRYPTOBOT: "CryptoBot",
        PayMethod.TRC20: "USDT TRC20",
        PayMethod.BEP20: "USDT BEP20",
    }[method]


def deposit_address(method: PayMethod) -> str:
    key = {PayMethod.TRC20: "usdt_trc20_address", PayMethod.BEP20: "usdt_bep20_address"}.get(method)
    return settings.get(key).strip() if key else ""


# --------------------------------------------------------------------------- #
# Создание счёта
# --------------------------------------------------------------------------- #


async def _unique_pay_amount(session: AsyncSession, base: Decimal, method: PayMethod) -> Decimal:
    """Подобрать сумму с хвостом, которую сейчас не ждёт другой счёт."""
    stmt = select(Invoice.pay_amount).where(
        Invoice.status == InvoiceStatus.PENDING.value,
        Invoice.method == method.value,
    )
    taken = {q2(value) if value is None else value for value in (await session.execute(stmt)).scalars().all()}

    for _ in range(50):
        candidate = (base + Decimal(random.randint(1, TAIL_MAX)) / Decimal(1_000_000)).quantize(Decimal("0.000001"))
        if candidate not in taken:
            return candidate
    raise PaymentError("Сейчас слишком много активных счетов, попробуйте через минуту")


def validate_amount(amount: Decimal, purpose: Account, user: User | None = None) -> None:
    """Проверить сумму по лимитам из админки."""
    if purpose is Account.DEPOSIT:
        if not settings.get_bool("deposit_enabled"):
            raise PaymentError("Пополнение депозита сейчас отключено")
        minimum = settings.get_decimal("deposit_min")
        maximum = settings.get_decimal("deposit_max")
        if minimum > ZERO and amount < minimum:
            raise PaymentError(f"Минимальное пополнение депозита — {minimum:.2f} USDT")
        if maximum > ZERO and user is not None and (user.deposit + amount) > maximum:
            raise PaymentError(f"Максимальный размер депозита — {maximum:.2f} USDT")
        return

    if not settings.get_bool("topup_enabled"):
        raise PaymentError("Пополнение сейчас отключено")
    minimum = settings.get_decimal("topup_min")
    maximum = settings.get_decimal("topup_max")
    if minimum > ZERO and amount < minimum:
        raise PaymentError(f"Минимальная сумма пополнения — {minimum:.2f} USDT")
    if maximum > ZERO and amount > maximum:
        raise PaymentError(f"Максимальная сумма пополнения — {maximum:.2f} USDT")


async def create_invoice(
    session: AsyncSession,
    user: User,
    amount: Decimal,
    method: PayMethod,
    purpose: Account = Account.BALANCE,
) -> Invoice:
    """Создать счёт на пополнение и вернуть его."""
    amount = q2(amount)
    validate_amount(amount, purpose, user)

    if not method_enabled(method):
        raise PaymentError(f"Способ «{method_title(method)}» сейчас недоступен")

    lifetime = max(settings.get_int("invoice_lifetime_min"), 5)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=lifetime)

    invoice = Invoice(
        user_id=user.tg_id,
        purpose=purpose.value,
        method=method.value,
        amount=amount,
        pay_amount=amount,
        expires_at=expires_at,
    )

    if method is PayMethod.CRYPTOBOT:
        client = cryptobot_client()
        target = "депозита" if purpose is Account.DEPOSIT else "баланса"
        try:
            result = await client.create_invoice(
                amount=amount,
                description=f"Пополнение {target}",
                payload=f"{user.tg_id}:{purpose.value}",
                expires_in=lifetime * 60,
            )
        except Exception as exc:
            log.exception("Не удалось создать счёт CryptoBot")
            raise PaymentError(f"CryptoBot недоступен: {exc}") from exc

        invoice.external_id = str(result.get("invoice_id"))
        invoice.pay_url = result.get("bot_invoice_url") or result.get("pay_url") or result.get("mini_app_invoice_url")
    else:
        address = deposit_address(method)
        if not address:
            raise PaymentError(f"Адрес {method_title(method)} не настроен — напишите в поддержку")
        invoice.address = address
        invoice.pay_amount = await _unique_pay_amount(session, amount, method)

    session.add(invoice)
    await session.commit()
    await session.refresh(invoice)
    return invoice


# --------------------------------------------------------------------------- #
# Зачисление
# --------------------------------------------------------------------------- #


async def mark_paid(
    session: AsyncSession,
    invoice: Invoice,
    received: Decimal | None = None,
    tx_hash: str | None = None,
) -> Decimal:
    """Зачислить оплаченный счёт. Возвращает зачисленную сумму.

    Зачисляем то, что реально пришло: при переплате пользователь не теряет
    разницу. Повторный вызов для уже оплаченного счёта ничего не делает.
    """
    if invoice.status == InvoiceStatus.PAID.value:
        return ZERO

    credited = q2(received if received is not None else invoice.pay_amount)
    if credited <= ZERO:
        return ZERO

    user = await session.get(User, invoice.user_id)
    if user is None:
        log.error("Счёт #%s без пользователя %s", invoice.id, invoice.user_id)
        return ZERO

    purpose = Account(invoice.purpose)
    if purpose is Account.DEPOSIT:
        await ledger.credit(
            session, user, credited, TxKind.DEPOSIT_IN,
            account=Account.DEPOSIT,
            comment=f"Пополнение депозита ({method_title(PayMethod(invoice.method))})",
            ref_type="invoice", ref_id=invoice.id,
        )
        lock_days = settings.get_int("deposit_lock_days")
        if lock_days > 0:
            user.deposit_locked_until = datetime.now(timezone.utc) + timedelta(days=lock_days)
    else:
        await ledger.credit(
            session, user, credited, TxKind.TOPUP,
            account=Account.BALANCE,
            comment=f"Пополнение баланса ({method_title(PayMethod(invoice.method))})",
            ref_type="invoice", ref_id=invoice.id,
        )

    invoice.status = InvoiceStatus.PAID.value
    invoice.paid_at = datetime.now(timezone.utc)
    if tx_hash:
        invoice.tx_hash = tx_hash

    await session.commit()
    log.info("Счёт #%s оплачен: %s USDT пользователю %s", invoice.id, credited, user.tg_id)
    return credited


async def cancel_invoice(session: AsyncSession, invoice: Invoice) -> None:
    if invoice.status != InvoiceStatus.PENDING.value:
        return
    invoice.status = InvoiceStatus.CANCELLED.value
    await session.commit()


async def pending_invoices(session: AsyncSession, method: PayMethod | None = None) -> list[Invoice]:
    stmt = select(Invoice).where(Invoice.status == InvoiceStatus.PENDING.value)
    if method is not None:
        stmt = stmt.where(Invoice.method == method.value)
    return list((await session.execute(stmt.order_by(Invoice.id))).scalars().all())


async def user_invoices(session: AsyncSession, user_id: int, limit: int = 10) -> list[Invoice]:
    stmt = select(Invoice).where(Invoice.user_id == user_id).order_by(Invoice.id.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def recheck(session: AsyncSession, invoice: Invoice) -> Decimal:
    """Проверить один счёт по кнопке «Я оплатил». Возвращает зачисленную сумму."""
    if invoice.status != InvoiceStatus.PENDING.value:
        return ZERO

    method = PayMethod(invoice.method)

    if method is PayMethod.CRYPTOBOT:
        if not invoice.external_id:
            return ZERO
        remote = await cryptobot_client().get_invoices(invoice_ids=[invoice.external_id])
        for item in remote:
            if str(item.get("invoice_id")) != invoice.external_id:
                continue
            if item.get("status") == "paid":
                paid = Decimal(str(item.get("paid_amount") or item.get("amount") or invoice.amount))
                return await mark_paid(session, invoice, paid, tx_hash=item.get("hash"))
            if item.get("status") == "expired":
                invoice.status = InvoiceStatus.EXPIRED.value
                await session.commit()
        return ZERO

    client = tron_client() if method is PayMethod.TRC20 else bsc_client()
    transfers = await client.incoming_usdt(invoice.address or "")
    used = set(
        (await session.execute(select(Invoice.tx_hash).where(Invoice.tx_hash.is_not(None)))).scalars().all()
    )
    tolerance = Decimal("0.000001")
    for transfer in transfers:
        if transfer.tx_hash in used:
            continue
        if abs(transfer.amount - invoice.pay_amount) <= tolerance:
            return await mark_paid(session, invoice, transfer.amount, tx_hash=transfer.tx_hash)
    return ZERO

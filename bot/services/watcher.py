"""Фоновая проверка платежей.

Раз в N секунд (настраивается в админке) бот:
  • закрывает просроченные счета;
  • спрашивает у CryptoBot статусы его инвойсов;
  • читает входящие TRC20/BEP20 переводы и сопоставляет их
    с ожидаемыми уникальными суммами.

Комиссии и адреса берутся из настроек на каждой итерации, поэтому
правки в админке подхватываются без перезапуска.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

from aiogram import Bot
from sqlalchemy import select

from bot.db import session_scope
from bot.db.models import Account, Invoice, InvoiceStatus, PayMethod
from bot.services import payments
from bot.services.settings import settings
from bot.utils.money import fmt

log = logging.getLogger(__name__)

MATCH_TOLERANCE = Decimal("0.000001")


async def _notify(bot: Bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text)
    except Exception as exc:  # noqa: BLE001 - пользователь мог заблокировать бота
        log.debug("Не доставлено уведомление %s: %s", user_id, exc)


def _paid_text(invoice: Invoice, credited: Decimal) -> str:
    if invoice.purpose == Account.DEPOSIT.value:
        return (
            f"✅ <b>Страховой депозит пополнен</b>\n\n"
            f"Зачислено: <b>{fmt(credited)}</b>\n"
            f"Способ: {payments.method_title(PayMethod(invoice.method))}"
        )
    return (
        f"✅ <b>Баланс пополнен</b>\n\n"
        f"Зачислено: <b>{fmt(credited)}</b>\n"
        f"Способ: {payments.method_title(PayMethod(invoice.method))}"
    )


# --------------------------------------------------------------------------- #
# Шаги проверки
# --------------------------------------------------------------------------- #


async def expire_stale_invoices(bot: Bot) -> None:
    now = datetime.now(timezone.utc)
    async with session_scope() as session:
        stmt = select(Invoice).where(
            Invoice.status == InvoiceStatus.PENDING.value,
            Invoice.expires_at.is_not(None),
            Invoice.expires_at < now,
        )
        for invoice in (await session.execute(stmt)).scalars().all():
            invoice.status = InvoiceStatus.EXPIRED.value
            log.info("Счёт #%s просрочен", invoice.id)


async def check_cryptobot(bot: Bot) -> None:
    if not settings.get("cryptobot_token").strip():
        return

    async with session_scope() as session:
        invoices = await payments.pending_invoices(session, PayMethod.CRYPTOBOT)
        pending = {inv.external_id: inv for inv in invoices if inv.external_id}
        if not pending:
            return

        try:
            remote = await payments.cryptobot_client().get_invoices(invoice_ids=list(pending))
        except Exception as exc:  # noqa: BLE001 - сеть/токен
            log.warning("CryptoBot недоступен: %s", exc)
            return

        for item in remote:
            invoice = pending.get(str(item.get("invoice_id")))
            if invoice is None:
                continue
            status = item.get("status")

            if status == "paid":
                paid_amount = Decimal(str(item.get("paid_amount") or item.get("amount") or invoice.amount))
                credited = await payments.mark_paid(session, invoice, paid_amount, tx_hash=item.get("hash"))
                if credited > 0:
                    await _notify(bot, invoice.user_id, _paid_text(invoice, credited))
            elif status == "expired":
                invoice.status = InvoiceStatus.EXPIRED.value


async def check_onchain(bot: Bot, method: PayMethod) -> None:
    address = payments.deposit_address(method)
    if not address:
        return

    async with session_scope() as session:
        invoices = await payments.pending_invoices(session, method)
        if not invoices:
            return

        client = payments.tron_client() if method is PayMethod.TRC20 else payments.bsc_client()
        try:
            transfers = await client.incoming_usdt(address)
        except Exception as exc:  # noqa: BLE001 - сеть/лимиты API
            log.warning("Не удалось прочитать переводы %s: %s", method.value, exc)
            return
        if not transfers:
            return

        used = set(
            (await session.execute(
                select(Invoice.tx_hash).where(Invoice.tx_hash.is_not(None))
            )).scalars().all()
        )
        min_confirmations = max(settings.get_int("topup_min_confirmations"), 0)

        for transfer in transfers:
            if transfer.tx_hash in used:
                continue
            if transfer.confirmations and transfer.confirmations < min_confirmations:
                continue

            match = next(
                (
                    inv for inv in invoices
                    if inv.status == InvoiceStatus.PENDING.value
                    and abs(transfer.amount - inv.pay_amount) <= MATCH_TOLERANCE
                ),
                None,
            )
            if match is None:
                log.info(
                    "Входящий перевод %s на %s USDT (%s) не совпал ни с одним счётом",
                    transfer.tx_hash, transfer.amount, method.value,
                )
                continue

            credited = await payments.mark_paid(session, match, transfer.amount, tx_hash=transfer.tx_hash)
            used.add(transfer.tx_hash)
            if credited > 0:
                await _notify(bot, match.user_id, _paid_text(match, credited))


# --------------------------------------------------------------------------- #
# Цикл
# --------------------------------------------------------------------------- #


async def run_watcher(bot: Bot) -> None:
    log.info("Наблюдатель платежей запущен")
    while True:
        try:
            await expire_stale_invoices(bot)
            await check_cryptobot(bot)
            await check_onchain(bot, PayMethod.TRC20)
            await check_onchain(bot, PayMethod.BEP20)
        except asyncio.CancelledError:
            log.info("Наблюдатель платежей остановлен")
            raise
        except Exception:  # noqa: BLE001 - цикл не должен умирать
            log.exception("Ошибка в наблюдателе платежей")

        interval = max(settings.get_int("payment_check_interval"), 10)
        await asyncio.sleep(interval)

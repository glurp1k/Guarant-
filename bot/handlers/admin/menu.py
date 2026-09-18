"""Главное меню админки, статистика, проверка внешних API."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import BigInteger, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Deal, DealStatus, Invoice, InvoiceStatus, User, Withdrawal, WithdrawStatus
from bot.db.types import from_micro
from bot.keyboards import admin as akb
from bot.keyboards.callbacks import AdminCB, MenuCB
from bot.services import payments
from bot.services.settings import settings
from bot.utils.money import fmt

log = logging.getLogger(__name__)
router = Router(name="admin-menu")

HEADER = "⚙️ <b>Панель администратора</b>\n\nВсе параметры бота меняются здесь и применяются сразу."


@router.message(Command("admin"))
async def admin_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(HEADER, reply_markup=akb.admin_main())


@router.callback_query(MenuCB.filter(F.action == "admin"))
@router.callback_query(AdminCB.filter(F.action == "menu"))
async def admin_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text(HEADER, reply_markup=akb.admin_main())
    await call.answer()


async def _sum(session: AsyncSession, column) -> object:
    return from_micro(await session.scalar(select(func.sum(cast(column, BigInteger)))) or 0)


@router.callback_query(AdminCB.filter(F.action == "stats"))
async def stats(call: CallbackQuery, session: AsyncSession) -> None:
    day_ago = datetime.now(timezone.utc) - timedelta(days=1)

    total_users = await session.scalar(select(func.count(User.tg_id))) or 0
    new_users = await session.scalar(select(func.count(User.tg_id)).where(User.created_at >= day_ago)) or 0
    banned = await session.scalar(select(func.count(User.tg_id)).where(User.is_banned.is_(True))) or 0

    total_balance = await _sum(session, User.balance)
    total_deposit = await _sum(session, User.deposit)

    deals_total = await session.scalar(select(func.count(Deal.id))) or 0
    deals_done = await session.scalar(
        select(func.count(Deal.id)).where(Deal.status == DealStatus.COMPLETED.value)
    ) or 0
    deals_open = await session.scalar(
        select(func.count(Deal.id)).where(Deal.status.in_(
            (DealStatus.WAITING_PARTNER.value, DealStatus.WAITING_PAYMENT.value, DealStatus.FUNDED.value)
        ))
    ) or 0
    disputes = await session.scalar(
        select(func.count(Deal.id)).where(Deal.status == DealStatus.DISPUTE.value)
    ) or 0

    held = from_micro(await session.scalar(
        select(func.sum(cast(Deal.amount, BigInteger))).where(Deal.status.in_(
            (DealStatus.FUNDED.value, DealStatus.DISPUTE.value)
        ))
    ) or 0)
    earned = from_micro(await session.scalar(
        select(func.sum(cast(Deal.commission, BigInteger))).where(Deal.status == DealStatus.COMPLETED.value)
    ) or 0)

    paid_in = from_micro(await session.scalar(
        select(func.sum(cast(Invoice.amount, BigInteger))).where(Invoice.status == InvoiceStatus.PAID.value)
    ) or 0)
    pending_wd = await session.scalar(
        select(func.count(Withdrawal.id)).where(Withdrawal.status == WithdrawStatus.PENDING.value)
    ) or 0
    paid_out = from_micro(await session.scalar(
        select(func.sum(cast(Withdrawal.net_amount, BigInteger))).where(
            Withdrawal.status == WithdrawStatus.PAID.value
        )
    ) or 0)

    text = (
        "📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: <b>{total_users}</b> (+{new_users} за сутки)\n"
        f"⛔️ В бане: <b>{banned}</b>\n\n"
        f"💰 На балансах: <b>{fmt(total_balance)}</b>\n"
        f"🛡 В депозитах: <b>{fmt(total_deposit)}</b>\n"
        f"🔒 Удержано по сделкам: <b>{fmt(held)}</b>\n\n"
        f"🤝 Сделок всего: <b>{deals_total}</b>\n"
        f"✅ Завершено: <b>{deals_done}</b> · 🔄 в работе: <b>{deals_open}</b> · ⚖️ споров: <b>{disputes}</b>\n"
        f"💵 Заработано на комиссии: <b>{fmt(earned)}</b>\n\n"
        f"📥 Принято пополнений: <b>{fmt(paid_in)}</b>\n"
        f"📤 Выплачено: <b>{fmt(paid_out)}</b> · заявок в очереди: <b>{pending_wd}</b>"
    )

    await call.message.edit_text(text, reply_markup=akb.back_to_admin())
    await call.answer()


@router.callback_query(AdminCB.filter(F.action == "healthcheck"))
async def healthcheck(call: CallbackQuery) -> None:
    """Быстрая проверка, что реквизиты и ключи из настроек рабочие."""
    await call.answer("Проверяю…")
    lines = ["🔌 <b>Проверка интеграций</b>", ""]

    token = settings.get("cryptobot_token").strip()
    if not token:
        lines.append("⚪️ CryptoBot — токен не задан")
    else:
        try:
            me = await payments.cryptobot_client().get_me()
            balances = await payments.cryptobot_client().get_balance()
            usdt = balances.get("USDT", 0)
            lines.append(f"✅ CryptoBot — приложение «{me.get('name', '?')}», USDT: {usdt}")
        except Exception as exc:  # noqa: BLE001 - показываем причину админу
            lines.append(f"❌ CryptoBot — {exc}")

    trc = settings.get("usdt_trc20_address").strip()
    if not trc:
        lines.append("⚪️ TRC20 — адрес не задан")
    else:
        try:
            transfers = await payments.tron_client().incoming_usdt(trc, limit=1)
            lines.append(f"✅ TRC20 — кошелёк читается (последних переводов: {len(transfers)})")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"❌ TRC20 — {exc}")

    bep = settings.get("usdt_bep20_address").strip()
    if not bep:
        lines.append("⚪️ BEP20 — адрес не задан")
    elif not settings.get("bscscan_api_key").strip():
        lines.append("⚠️ BEP20 — адрес задан, но нет ключа BscScan: платежи не отследятся")
    else:
        try:
            transfers = await payments.bsc_client().incoming_usdt(bep, limit=1)
            lines.append(f"✅ BEP20 — кошелёк читается (последних переводов: {len(transfers)})")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"❌ BEP20 — {exc}")

    await call.message.edit_text("\n".join(lines), reply_markup=akb.back_to_admin())

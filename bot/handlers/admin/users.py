"""Управление пользователями и рассылка."""

from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_config
from bot.db.models import Account, TxKind, User
from bot.keyboards import admin as akb
from bot.keyboards.callbacks import AdminCB, AdminItemCB
from bot.services import ledger
from bot.services import users as users_service
from bot.services.ledger import InsufficientFunds
from bot.states import AdminSG
from bot.utils.money import fmt, parse_amount
from bot.utils.texts import esc, fmt_date

log = logging.getLogger(__name__)
router = Router(name="admin-users")

BROADCAST_DELAY = 0.05  # ~20 сообщений в секунду — в пределах лимитов Telegram


def user_text(target: User) -> str:
    flags = []
    if target.is_admin:
        flags.append("👑 админ")
    if target.is_verified:
        flags.append("🔷 верифицирован")
    if target.is_banned:
        flags.append("⛔️ забанен")

    lines = [
        f"👤 <b>{esc(target.full_name) or 'Без имени'}</b>",
        f"{esc(target.mention)} · <code>{target.tg_id}</code>",
        "",
        f"💰 Баланс: <b>{fmt(target.balance)}</b>",
        f"🛡 Депозит: <b>{fmt(target.deposit)}</b>",
        f"🤝 Сделок: <b>{target.deals_done}</b> · оборот <b>{fmt(target.deals_volume)}</b>",
        f"📅 Регистрация: {fmt_date(target.created_at)} UTC",
        f"👁 Был: {fmt_date(target.last_seen_at)} UTC",
    ]
    if flags:
        lines += ["", " · ".join(flags)]
    if target.is_banned and target.ban_reason:
        lines.append(f"Причина бана: {esc(target.ban_reason)}")
    return "\n".join(lines)


@router.callback_query(AdminCB.filter(F.action == "users"))
async def ask_user(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminSG.user_query)
    await call.message.edit_text(
        "👤 <b>Поиск пользователя</b>\n\n"
        "Пришлите <code>@username</code> или Telegram ID.",
        reply_markup=akb.back_to_admin(),
    )
    await call.answer()


@router.message(AdminSG.user_query)
async def find_user(message: Message, session: AsyncSession, state: FSMContext) -> None:
    target = await users_service.find_any(session, message.text or "")
    if target is None:
        await message.answer("❌ Пользователь не найден. Он должен хотя бы раз запустить бота.")
        return

    await state.clear()
    await message.answer(user_text(target), reply_markup=akb.user_card(target))


async def _load_target(call: CallbackQuery, session: AsyncSession, tg_id: int) -> User | None:
    target = await session.get(User, tg_id)
    if target is None:
        await call.answer("Пользователь не найден", show_alert=True)
    return target


@router.callback_query(AdminItemCB.filter(F.action.in_({"user_balance", "user_deposit"})))
async def ask_amount(call: CallbackQuery, callback_data: AdminItemCB, state: FSMContext) -> None:
    account = Account.DEPOSIT if callback_data.action == "user_deposit" else Account.BALANCE
    await state.set_state(AdminSG.amount_delta)
    await state.update_data(target_id=callback_data.item_id, account=account.value)

    where = "депозит" if account is Account.DEPOSIT else "баланс"
    await call.message.edit_text(
        f"💵 Изменение счёта «{where}»\n\n"
        f"Пришлите сумму со знаком:\n"
        f"<code>50</code> — начислить\n"
        f"<code>-50</code> — списать",
        reply_markup=akb.back_to_admin(),
    )
    await call.answer()


@router.message(AdminSG.amount_delta)
async def apply_amount(message: Message, session: AsyncSession, user: User, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    negative = raw.startswith("-")
    amount = parse_amount(raw.lstrip("+-"))
    if amount is None:
        await message.answer("❌ Введите сумму, например <code>50</code> или <code>-50</code>.")
        return

    data = await state.get_data()
    target = await session.get(User, data.get("target_id", 0))
    if target is None:
        await state.clear()
        await message.answer("❌ Пользователь не найден.", reply_markup=akb.back_to_admin())
        return

    account = Account(data.get("account", Account.BALANCE.value))
    delta = -amount if negative else amount
    kind = TxKind.ADMIN_DEBIT if negative else TxKind.ADMIN_CREDIT

    try:
        await ledger.apply(
            session, target,
            amount=delta, kind=kind, account=account,
            comment=f"Корректировка администратором {user.tg_id}",
        )
        await session.commit()
    except InsufficientFunds as exc:
        await session.rollback()
        await message.answer(f"❌ Недостаточно средств: доступно {fmt(exc.available)}.")
        return

    await state.clear()
    log.info("Админ %s изменил %s пользователя %s на %s", user.tg_id, account.value, target.tg_id, delta)
    await message.answer(user_text(target), reply_markup=akb.user_card(target))

    where = "депозит" if account is Account.DEPOSIT else "баланс"
    verb = "списано с" if negative else "зачислено на"
    try:
        await message.bot.send_message(
            target.tg_id,
            f"ℹ️ Администратор изменил ваш {where}: {verb} счёта <b>{fmt(amount)}</b>.",
        )
    except Exception as exc:  # noqa: BLE001 - пользователь мог заблокировать бота
        log.debug("Уведомление %s не доставлено: %s", target.tg_id, exc)


@router.callback_query(AdminItemCB.filter(F.action == "user_ban"))
async def toggle_ban(call: CallbackQuery, callback_data: AdminItemCB, session: AsyncSession, state: FSMContext) -> None:
    target = await _load_target(call, session, callback_data.item_id)
    if target is None:
        return

    if target.is_banned:
        target.is_banned = False
        target.ban_reason = None
        await session.commit()
        await call.message.edit_text(user_text(target), reply_markup=akb.user_card(target))
        await call.answer("Разблокирован")
        return

    await state.set_state(AdminSG.ban_reason)
    await state.update_data(target_id=target.tg_id)
    await call.message.edit_text(
        "🚫 Пришлите причину блокировки — её увидит пользователь.",
        reply_markup=akb.back_to_admin(),
    )
    await call.answer()


@router.message(AdminSG.ban_reason)
async def do_ban(message: Message, session: AsyncSession, user: User, state: FSMContext) -> None:
    data = await state.get_data()
    target = await session.get(User, data.get("target_id", 0))
    if target is None:
        await state.clear()
        await message.answer("❌ Пользователь не найден.", reply_markup=akb.back_to_admin())
        return

    if target.tg_id in get_config().admin_ids:
        await state.clear()
        await message.answer("❌ Главного администратора заблокировать нельзя.", reply_markup=akb.back_to_admin())
        return

    target.is_banned = True
    target.ban_reason = (message.text or "").strip()[:255] or None
    await session.commit()

    await state.clear()
    log.info("Админ %s заблокировал %s", user.tg_id, target.tg_id)
    await message.answer(user_text(target), reply_markup=akb.user_card(target))


@router.callback_query(AdminItemCB.filter(F.action == "user_verify"))
async def toggle_verify(call: CallbackQuery, callback_data: AdminItemCB, session: AsyncSession) -> None:
    target = await _load_target(call, session, callback_data.item_id)
    if target is None:
        return

    target.is_verified = not target.is_verified
    await session.commit()
    await call.message.edit_text(user_text(target), reply_markup=akb.user_card(target))
    await call.answer("Верифицирован" if target.is_verified else "Верификация снята")


@router.callback_query(AdminItemCB.filter(F.action == "user_admin"))
async def toggle_admin(call: CallbackQuery, callback_data: AdminItemCB, session: AsyncSession, user: User) -> None:
    target = await _load_target(call, session, callback_data.item_id)
    if target is None:
        return

    if target.tg_id in get_config().admin_ids and target.is_admin:
        await call.answer("Главный администратор задан в .env и не снимается здесь", show_alert=True)
        return

    target.is_admin = not target.is_admin
    await session.commit()
    log.info("Админ %s изменил права %s → is_admin=%s", user.tg_id, target.tg_id, target.is_admin)

    await call.message.edit_text(user_text(target), reply_markup=akb.user_card(target))
    await call.answer("Назначен админом" if target.is_admin else "Права сняты")


@router.callback_query(AdminItemCB.filter(F.action == "user_history"))
async def user_history(call: CallbackQuery, callback_data: AdminItemCB, session: AsyncSession) -> None:
    target = await _load_target(call, session, callback_data.item_id)
    if target is None:
        return

    entries = await ledger.history(session, target.tg_id, limit=20)
    if not entries:
        text = "🧾 Операций нет."
    else:
        rows = [
            f"{'＋' if item.amount > 0 else '−'}{abs(item.amount):.2f} "
            f"{'🛡' if item.account == Account.DEPOSIT.value else '💰'} "
            f"{item.kind} · {fmt_date(item.created_at)}"
            for item in entries
        ]
        text = f"🧾 <b>Операции {esc(target.mention)}</b>\n\n" + "\n".join(rows)

    await call.message.edit_text(text, reply_markup=akb.user_card(target))
    await call.answer()


# --------------------------------------------------------------------------- #
# Рассылка
# --------------------------------------------------------------------------- #


@router.callback_query(AdminCB.filter(F.action == "broadcast"))
async def ask_broadcast(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminSG.broadcast)
    await call.message.edit_text(
        "📣 <b>Рассылка</b>\n\n"
        "Пришлите текст сообщения. Разметка сохраняется.",
        reply_markup=akb.back_to_admin(),
    )
    await call.answer()


@router.message(AdminSG.broadcast)
async def preview_broadcast(message: Message, state: FSMContext) -> None:
    text = message.html_text or message.text or ""
    if not text.strip():
        await message.answer("❌ Пустое сообщение.")
        return

    await state.update_data(text=text)
    await state.set_state(None)
    await message.answer(
        f"Предпросмотр:\n\n{text}\n\n—\nОтправить всем пользователям?",
        reply_markup=akb.confirm_broadcast(),
    )


@router.callback_query(AdminItemCB.filter(F.action == "broadcast_go"))
async def do_broadcast(call: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    text = data.get("text", "")
    if not text:
        await call.answer("Сообщение потеряно, начните заново", show_alert=True)
        return

    await state.clear()
    await call.message.edit_text("📣 Рассылка запущена…", reply_markup=akb.back_to_admin())
    await call.answer()

    recipients = (await session.execute(
        select(User.tg_id).where(User.is_banned.is_(False))
    )).scalars().all()

    sent = failed = 0
    for tg_id in recipients:
        try:
            await call.bot.send_message(tg_id, text)
            sent += 1
        except Exception:  # noqa: BLE001 - заблокировавшие бота считаются недоставленными
            failed += 1
        await asyncio.sleep(BROADCAST_DELAY)

    await call.message.answer(
        f"📣 <b>Рассылка завершена</b>\n\n"
        f"Доставлено: <b>{sent}</b>\n"
        f"Не доставлено: <b>{failed}</b>",
        reply_markup=akb.back_to_admin(),
    )

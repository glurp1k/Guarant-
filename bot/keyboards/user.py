"""Клавиатуры пользовательской части."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.models import Account, PayMethod
from bot.keyboards.callbacks import DealCB, DepositCB, MenuCB, TopupCB, WithdrawCB
from bot.services import payments, withdrawals
from bot.services.settings import settings

BACK_TO_MENU = InlineKeyboardButton(text="⬅️ В меню", callback_data=MenuCB(action="main").pack())


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🤝 Сделки", callback_data=DealCB(action="list"))
    builder.button(text="💰 Баланс", callback_data=MenuCB(action="balance"))

    if settings.get_bool("deposit_enabled"):
        builder.button(text="🛡 Страховой депозит", callback_data=DepositCB(action="menu"))
    if settings.get_bool("check_enabled"):
        builder.button(text="🔍 Проверить по юзернейму", callback_data=MenuCB(action="check"))

    builder.button(text="📜 Правила", callback_data=MenuCB(action="rules"))

    support = settings.get("support_username").strip().lstrip("@")
    if support:
        builder.button(text="💬 Поддержка", url=f"https://t.me/{support}")

    if is_admin:
        builder.button(text="⚙️ Админка", callback_data=MenuCB(action="admin"))

    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def balance_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Пополнить", callback_data=TopupCB(action="choose", purpose=Account.BALANCE.value))
    builder.button(text="📤 Вывести", callback_data=WithdrawCB(action="menu", source=Account.BALANCE.value))
    builder.button(text="🧾 История", callback_data=MenuCB(action="history"))
    builder.row(BACK_TO_MENU)
    builder.adjust(2, 1)
    return builder.as_markup()


def deposit_menu(has_deposit: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Пополнить криптой", callback_data=TopupCB(action="choose", purpose=Account.DEPOSIT.value))

    if settings.get_bool("deposit_from_balance"):
        builder.button(text="🔁 Перевести с баланса", callback_data=DepositCB(action="from_balance"))
    if has_deposit and settings.get_bool("deposit_withdraw_enabled"):
        builder.button(text="↩️ Снять на баланс", callback_data=DepositCB(action="to_balance"))
        builder.button(text="📤 Вывести в крипту", callback_data=WithdrawCB(action="menu", source=Account.DEPOSIT.value))

    builder.row(BACK_TO_MENU)
    builder.adjust(1, 1, 2)
    return builder.as_markup()


def pay_methods(purpose: str) -> InlineKeyboardMarkup:
    """Способы пополнения — скрываем выключенные в админке."""
    builder = InlineKeyboardBuilder()
    for method in PayMethod:
        if payments.method_enabled(method):
            builder.button(
                text=payments.method_title(method),
                callback_data=TopupCB(action="method", method=method.value, purpose=purpose),
            )
    builder.button(text="⬅️ Назад", callback_data=MenuCB(action="balance" if purpose == "balance" else "deposit"))
    builder.adjust(1)
    return builder.as_markup()


def invoice_actions(invoice_id: int, pay_url: str | None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if pay_url:
        builder.button(text="💳 Оплатить", url=pay_url)
    builder.button(text="🔄 Я оплатил — проверить", callback_data=TopupCB(action="check", invoice_id=invoice_id))
    builder.button(text="❌ Отменить счёт", callback_data=TopupCB(action="cancel", invoice_id=invoice_id))
    builder.adjust(1)
    return builder.as_markup()


def withdraw_methods(source: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for method in PayMethod:
        if withdrawals.method_enabled(method):
            builder.button(
                text=payments.method_title(method),
                callback_data=WithdrawCB(action="method", method=method.value, source=source),
            )
    builder.button(text="⬅️ Назад", callback_data=MenuCB(action="balance" if source == "balance" else "deposit"))
    builder.adjust(1)
    return builder.as_markup()


def confirm_withdraw(method: str, source: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=WithdrawCB(action="confirm", method=method, source=source))
    builder.button(text="❌ Отмена", callback_data=MenuCB(action="main"))
    builder.adjust(2)
    return builder.as_markup()


def deal_roles() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🛍 Я продавец", callback_data=DealCB(action="role", value="seller"))
    builder.button(text="💵 Я покупатель", callback_data=DealCB(action="role", value="buyer"))
    builder.row(BACK_TO_MENU)
    builder.adjust(2, 1)
    return builder.as_markup()


def deal_payers() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Продавец", callback_data=DealCB(action="payer", value="seller"))
    builder.button(text="Покупатель", callback_data=DealCB(action="payer", value="buyer"))
    builder.button(text="Пополам", callback_data=DealCB(action="payer", value="split"))
    builder.adjust(3)
    return builder.as_markup()


def deals_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Создать сделку", callback_data=DealCB(action="new"))
    builder.button(text="📂 Мои сделки", callback_data=DealCB(action="my"))
    builder.row(BACK_TO_MENU)
    builder.adjust(2, 1)
    return builder.as_markup()


def deal_card(deal_id: int, *, can_pay: bool = False, can_confirm: bool = False,
              can_dispute: bool = False, can_cancel: bool = False,
              share_url: str | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if share_url:
        builder.button(text="🔗 Отправить ссылку партнёру", switch_inline_query=share_url)
    if can_pay:
        builder.button(text="💳 Оплатить сделку", callback_data=DealCB(action="pay", deal_id=deal_id))
    if can_confirm:
        builder.button(text="✅ Подтвердить получение", callback_data=DealCB(action="done", deal_id=deal_id))
    if can_dispute:
        builder.button(text="⚖️ Открыть спор", callback_data=DealCB(action="dispute", deal_id=deal_id))
    if can_cancel:
        builder.button(text="❌ Отменить сделку", callback_data=DealCB(action="cancel", deal_id=deal_id))
    builder.row(BACK_TO_MENU)
    builder.adjust(1)
    return builder.as_markup()


def join_deal(deal_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Присоединиться к сделке", callback_data=DealCB(action="join", deal_id=deal_id))
    builder.button(text="❌ Отказаться", callback_data=MenuCB(action="main"))
    builder.adjust(1)
    return builder.as_markup()


def back_only() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[BACK_TO_MENU]])

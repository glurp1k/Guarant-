"""Клавиатуры админки."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.models import User
from bot.keyboards.callbacks import AdminCB, AdminItemCB, MenuCB, SettingCB, TemplateCB
from bot.services.settings import CATEGORIES, DEFS_BY_KEY, SettingDef, settings

PAGE_SIZE = 8


def admin_main() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🛠 Настройки", callback_data=SettingCB(action="categories"))
    builder.button(text="✉️ Сообщения", callback_data=TemplateCB(action="categories"))
    builder.button(text="📊 Статистика", callback_data=AdminCB(action="stats"))
    builder.button(text="📤 Заявки на вывод", callback_data=AdminCB(action="withdrawals"))
    builder.button(text="⚖️ Споры", callback_data=AdminCB(action="disputes"))
    builder.button(text="👤 Пользователи", callback_data=AdminCB(action="users"))
    builder.button(text="🧾 Счета", callback_data=AdminCB(action="invoices"))
    builder.button(text="📣 Рассылка", callback_data=AdminCB(action="broadcast"))
    builder.button(text="🔌 Проверить API", callback_data=AdminCB(action="healthcheck"))
    builder.button(text="⬅️ В меню", callback_data=MenuCB(action="main"))
    builder.adjust(2, 2, 2, 2, 1, 1)
    return builder.as_markup()


def back_to_admin() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ В админку", callback_data=AdminCB(action="menu"))
    return builder.as_markup()


# --------------------------------------------------------------------------- #
# Настройки
# --------------------------------------------------------------------------- #


def settings_categories() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, title in CATEGORIES.items():
        builder.button(text=title, callback_data=SettingCB(action="list", category=key))
    builder.button(text="⬅️ В админку", callback_data=AdminCB(action="menu"))
    builder.adjust(2, 2, 2, 2, 1)
    return builder.as_markup()


def settings_list(category: str, items: list[SettingDef], page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    start = page * PAGE_SIZE
    chunk = items[start:start + PAGE_SIZE]

    for definition in chunk:
        value = settings.describe(definition.key)
        if definition.type == "bool":
            builder.button(
                text=f"{definition.title}: {value}",
                callback_data=SettingCB(action="toggle", key=definition.key, category=category, page=page),
            )
        else:
            builder.button(
                text=f"{definition.title}: {value}",
                callback_data=SettingCB(action="edit", key=definition.key, category=category, page=page),
            )
    builder.adjust(1)

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="◀️", callback_data=SettingCB(action="list", category=category, page=page - 1).pack()))
    if start + PAGE_SIZE < len(items):
        nav.append(InlineKeyboardButton(
            text="▶️", callback_data=SettingCB(action="list", category=category, page=page + 1).pack()))
    if nav:
        builder.row(*nav)

    builder.row(InlineKeyboardButton(
        text="⬅️ К разделам", callback_data=SettingCB(action="categories").pack()))
    return builder.as_markup()


def setting_edit(key: str, category: str, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    definition = DEFS_BY_KEY.get(key)

    if definition is not None and definition.choices:
        for choice in definition.choices:
            builder.button(
                text=("● " if settings.get(key) == choice else "○ ") + choice,
                callback_data=SettingCB(action="set", key=key, value=choice, category=category, page=page),
            )
        builder.adjust(len(definition.choices))

    builder.button(text="♻️ Сбросить", callback_data=SettingCB(action="reset", key=key, category=category, page=page))
    builder.button(text="⬅️ Назад", callback_data=SettingCB(action="list", category=category, page=page))
    builder.adjust(1)
    return builder.as_markup()


# --------------------------------------------------------------------------- #
# Заявки, споры, пользователи
# --------------------------------------------------------------------------- #


def withdrawal_card(request_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Выплачено", callback_data=AdminItemCB(action="wd_paid", item_id=request_id))
    builder.button(text="🧾 Указать хэш", callback_data=AdminItemCB(action="wd_hash", item_id=request_id))
    builder.button(text="❌ Отклонить", callback_data=AdminItemCB(action="wd_reject", item_id=request_id))
    builder.button(text="⬅️ К списку", callback_data=AdminCB(action="withdrawals"))
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def dispute_card(deal_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➡️ Отдать продавцу", callback_data=AdminItemCB(action="deal_release", item_id=deal_id))
    builder.button(text="↩️ Вернуть покупателю", callback_data=AdminItemCB(action="deal_refund", item_id=deal_id))
    builder.button(text="⬅️ К списку", callback_data=AdminCB(action="disputes"))
    builder.adjust(1)
    return builder.as_markup()


def invoice_card(invoice_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Зачислить вручную", callback_data=AdminItemCB(action="inv_confirm", item_id=invoice_id))
    builder.button(text="❌ Отменить счёт", callback_data=AdminItemCB(action="inv_cancel", item_id=invoice_id))
    builder.button(text="⬅️ К списку", callback_data=AdminCB(action="invoices"))
    builder.adjust(1)
    return builder.as_markup()


def user_card(user: User) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Изменить баланс", callback_data=AdminItemCB(action="user_balance", item_id=user.tg_id))
    builder.button(text="🛡 Изменить депозит", callback_data=AdminItemCB(action="user_deposit", item_id=user.tg_id))
    builder.button(
        text="🔓 Разблокировать" if user.is_banned else "🚫 Заблокировать",
        callback_data=AdminItemCB(action="user_ban", item_id=user.tg_id),
    )
    builder.button(
        text="🔷 Снять верификацию" if user.is_verified else "🔷 Верифицировать",
        callback_data=AdminItemCB(action="user_verify", item_id=user.tg_id),
    )
    builder.button(
        text="👑 Разжаловать" if user.is_admin else "👑 Сделать админом",
        callback_data=AdminItemCB(action="user_admin", item_id=user.tg_id),
    )
    builder.button(text="🧾 История операций", callback_data=AdminItemCB(action="user_history", item_id=user.tg_id))
    builder.button(text="⬅️ В админку", callback_data=AdminCB(action="menu"))
    builder.adjust(2, 1, 1, 1, 1)
    return builder.as_markup()


def confirm_broadcast() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📣 Отправить всем", callback_data=AdminItemCB(action="broadcast_go"))
    builder.button(text="❌ Отмена", callback_data=AdminCB(action="menu"))
    builder.adjust(1)
    return builder.as_markup()


# --------------------------------------------------------------------------- #
# Сообщения
# --------------------------------------------------------------------------- #


def template_categories() -> InlineKeyboardMarkup:
    from bot.services.templates import CATEGORIES as TPL_CATEGORIES

    builder = InlineKeyboardBuilder()
    for key, title in TPL_CATEGORIES.items():
        builder.button(text=title, callback_data=TemplateCB(action="list", category=key))
    builder.button(text="⬅️ В админку", callback_data=AdminCB(action="menu"))
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def template_list(category: str, items, page: int = 0) -> InlineKeyboardMarkup:
    from bot.services.templates import templates as tpl

    builder = InlineKeyboardBuilder()
    start = page * PAGE_SIZE
    for definition in items[start:start + PAGE_SIZE]:
        builder.button(
            text=f"{definition.title} · {tpl.preview(definition.key)}",
            callback_data=TemplateCB(action="open", category=category, key=definition.key, page=page),
        )
    builder.adjust(1)

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="◀️", callback_data=TemplateCB(action="list", category=category, page=page - 1).pack()))
    if start + PAGE_SIZE < len(items):
        nav.append(InlineKeyboardButton(
            text="▶️", callback_data=TemplateCB(action="list", category=category, page=page + 1).pack()))
    if nav:
        builder.row(*nav)

    builder.row(InlineKeyboardButton(
        text="⬅️ К разделам", callback_data=TemplateCB(action="categories").pack()))
    return builder.as_markup()


def template_card(key: str, category: str, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить", callback_data=TemplateCB(action="edit", category=category, key=key, page=page))
    builder.button(text="👁 Показать как есть",
                   callback_data=TemplateCB(action="preview", category=category, key=key, page=page))
    builder.button(text="♻️ Сбросить", callback_data=TemplateCB(action="reset", category=category, key=key, page=page))
    builder.button(text="⬅️ Назад", callback_data=TemplateCB(action="list", category=category, page=page))
    builder.adjust(2, 1, 1)
    return builder.as_markup()

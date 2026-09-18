"""Редактор настроек.

Список параметров берётся из реестра в bot/services/settings.py, так что
новый пункт появляется в админке автоматически.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.keyboards import admin as akb
from bot.keyboards.callbacks import SettingCB
from bot.services.settings import CATEGORIES, DEFINITIONS, DEFS_BY_KEY, SettingDef, settings
from bot.states import AdminSG
from bot.utils.texts import esc

log = logging.getLogger(__name__)
router = Router(name="admin-settings")

TYPE_HINTS = {
    "int": "целое число, например <code>30</code>",
    "decimal": "число, можно дробное: <code>2.5</code>",
    "bool": "<code>да</code> или <code>нет</code>",
    "str": "строка в одну строку",
    "text": "любой текст, поддерживается HTML-разметка Telegram",
    "emoji": "один эмодзи — обычный или премиум",
}


def category_items(category: str) -> list[SettingDef]:
    return [item for item in DEFINITIONS if item.category == category]


def _validate(definition: SettingDef, raw: str) -> str | None:
    """Привести ввод к хранимому виду. None — значение не подходит."""
    raw = raw.strip()

    if definition.type == "int":
        try:
            return str(int(Decimal(raw.replace(",", "."))))
        except (InvalidOperation, ValueError):
            return None

    if definition.type == "decimal":
        try:
            value = Decimal(raw.replace(",", "."))
        except (InvalidOperation, ValueError):
            return None
        if value < 0:
            return None
        return format(value.normalize(), "f")

    if definition.type == "bool":
        lowered = raw.lower()
        if lowered in {"1", "да", "вкл", "true", "yes", "on"}:
            return "1"
        if lowered in {"0", "нет", "выкл", "false", "no", "off"}:
            return "0"
        return None

    if definition.choices and raw not in definition.choices:
        return None

    if definition.type in ("str", "emoji") and "\n" in raw:
        return None
    if definition.type == "emoji" and len(raw) > 128:
        return None

    return raw


async def _render_list(call: CallbackQuery, category: str, page: int) -> None:
    items = category_items(category)
    title = CATEGORIES.get(category, category)
    await call.message.edit_text(
        f"{title}\n\nНажмите на параметр, чтобы изменить. Переключатели меняются одним нажатием.",
        reply_markup=akb.settings_list(category, items, page),
    )


@router.callback_query(SettingCB.filter(F.action == "categories"))
async def categories(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text(
        "🛠 <b>Настройки</b>\n\nВыберите раздел:",
        reply_markup=akb.settings_categories(),
    )
    await call.answer()


@router.callback_query(SettingCB.filter(F.action == "list"))
async def show_list(call: CallbackQuery, callback_data: SettingCB, state: FSMContext) -> None:
    await state.clear()
    await _render_list(call, callback_data.category, callback_data.page)
    await call.answer()


@router.callback_query(SettingCB.filter(F.action == "toggle"))
async def toggle(call: CallbackQuery, callback_data: SettingCB, session: AsyncSession, user: User) -> None:
    key = callback_data.key
    new_value = "0" if settings.get_bool(key) else "1"
    await settings.set(session, key, new_value, admin_id=user.tg_id)

    log.info("Админ %s: %s → %s", user.tg_id, key, new_value)
    await _render_list(call, callback_data.category, callback_data.page)
    await call.answer("✅ вкл" if new_value == "1" else "❌ выкл")


@router.callback_query(SettingCB.filter(F.action == "set"))
async def set_choice(call: CallbackQuery, callback_data: SettingCB, session: AsyncSession, user: User) -> None:
    await settings.set(session, callback_data.key, callback_data.value, admin_id=user.tg_id)
    await _render_list(call, callback_data.category, callback_data.page)
    await call.answer(f"Выбрано: {callback_data.value}")


@router.callback_query(SettingCB.filter(F.action == "reset"))
async def reset(call: CallbackQuery, callback_data: SettingCB, session: AsyncSession, user: User) -> None:
    definition = DEFS_BY_KEY.get(callback_data.key)
    if definition is None:
        await call.answer("Параметр не найден", show_alert=True)
        return

    await settings.set(session, definition.key, definition.default, admin_id=user.tg_id)
    await _render_list(call, callback_data.category, callback_data.page)
    await call.answer("Значение сброшено")


@router.callback_query(SettingCB.filter(F.action == "edit"))
async def edit(call: CallbackQuery, callback_data: SettingCB, state: FSMContext) -> None:
    definition = DEFS_BY_KEY.get(callback_data.key)
    if definition is None:
        await call.answer("Параметр не найден", show_alert=True)
        return

    await state.set_state(AdminSG.setting_value)
    await state.update_data(key=definition.key, category=callback_data.category, page=callback_data.page)

    current = settings.get(definition.key) or "— не задано"
    hint = TYPE_HINTS.get(definition.type, "")
    extra = f"\n\nℹ️ {esc(definition.hint)}" if definition.hint else ""
    choices = f"\n\nДопустимые значения: {', '.join(definition.choices)}" if definition.choices else ""

    await call.message.edit_text(
        f"✏️ <b>{esc(definition.title)}</b>\n"
        f"<code>{definition.key}</code>\n\n"
        f"Текущее значение:\n<code>{esc(current)}</code>\n\n"
        f"Формат: {hint}{choices}{extra}\n\n"
        f"Пришлите новое значение сообщением.",
        reply_markup=akb.setting_edit(definition.key, callback_data.category, callback_data.page),
    )
    await call.answer()


@router.message(AdminSG.setting_value)
async def save_value(message: Message, session: AsyncSession, user: User, state: FSMContext) -> None:
    data = await state.get_data()
    definition = DEFS_BY_KEY.get(data.get("key", ""))
    if definition is None:
        await state.clear()
        await message.answer("❌ Параметр не найден.", reply_markup=akb.back_to_admin())
        return

    # Для текстов сохраняем разметку как есть, чтобы админ мог верстать сообщения.
    # Для текстов и иконок берём HTML: только так переживает премиум-эмодзи.
    keep_html = definition.type in ("text", "emoji")
    raw = message.html_text if keep_html and message.html_text else (message.text or "")
    value = _validate(definition, raw)

    if value is None:
        await message.answer(
            f"❌ Не подходит. Ожидается: {TYPE_HINTS.get(definition.type, 'значение')}."
        )
        return

    await settings.set(session, definition.key, value, admin_id=user.tg_id)
    await state.clear()
    log.info("Админ %s изменил %s", user.tg_id, definition.key)

    items = category_items(data.get("category", definition.category))
    await message.answer(
        f"✅ <b>{esc(definition.title)}</b> обновлено.\n\nНовое значение: <code>{esc(settings.describe(definition.key))}</code>",
        reply_markup=akb.settings_list(data.get("category", definition.category), items, data.get("page", 0)),
    )

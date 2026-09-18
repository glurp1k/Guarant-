"""Редактор сообщений бота.

Админ выбирает экран и присылает новое сообщение — текстом или фото
с подписью. Что прислали, то и станет экраном: с форматированием,
премиум-эмодзи и картинкой. Подстановки вида {balance} подставляются
при показе, их список у каждого экрана свой.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.keyboards import admin as akb
from bot.keyboards.callbacks import TemplateCB
from bot.services.templates import (CAPTION_LIMIT, CATEGORIES, DEFINITIONS, DEFS_BY_KEY,
                                    TEXT_LIMIT, templates)
from bot.states import AdminSG
from bot.utils.render import show
from bot.utils.style import block, note, title
from bot.utils.texts import esc

log = logging.getLogger(__name__)
router = Router(name="admin-templates")


def category_items(category: str):
    return [item for item in DEFINITIONS if item.category == category]


def card_text(key: str) -> str:
    definition = DEFS_BY_KEY[key]
    photo = "есть" if templates.photo(key) else "нет"
    fields = ", ".join(f"{{{name}}}" for name in definition.placeholders) or "нет"

    return block(
        title("✉️", esc(definition.title)),
        f"Ключ: <code>{definition.key}</code>\nФото: {photo}",
        f"Подстановки: {esc(fields)}",
        note("ℹ️", "«Изменить» — и пришлите новое сообщение. Можно с фото, "
                   "премиум-эмодзи и форматированием."),
    )


async def _render_list(call: CallbackQuery, category: str, page: int) -> None:
    items = category_items(category)
    await show(
        call,
        block(title("✉️", CATEGORIES.get(category, category)),
              "Выберите сообщение, чтобы посмотреть или изменить."),
        akb.template_list(category, items, page),
    )


@router.callback_query(TemplateCB.filter(F.action == "categories"))
async def categories(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(
        call,
        block(title("✉️", "Сообщения бота"),
              "Любой экран можно переписать целиком — текст, картинка, "
              "премиум-эмодзи, форматирование."),
        akb.template_categories(),
    )


@router.callback_query(TemplateCB.filter(F.action == "list"))
async def show_list(call: CallbackQuery, callback_data: TemplateCB, state: FSMContext) -> None:
    await state.clear()
    await _render_list(call, callback_data.category or "main", callback_data.page)


@router.callback_query(TemplateCB.filter(F.action == "open"))
async def open_template(call: CallbackQuery, callback_data: TemplateCB, state: FSMContext) -> None:
    key = callback_data.key or ""
    if key not in DEFS_BY_KEY:
        await call.answer("Сообщение не найдено", show_alert=True)
        return

    await state.clear()
    await show(call, card_text(key), akb.template_card(key, callback_data.category or "main", callback_data.page))


@router.callback_query(TemplateCB.filter(F.action == "preview"))
async def preview(call: CallbackQuery, callback_data: TemplateCB) -> None:
    """Показать экран так, как его увидит пользователь."""
    key = callback_data.key or ""
    if key not in DEFS_BY_KEY:
        await call.answer("Сообщение не найдено", show_alert=True)
        return

    text, photo = templates.render(key)  # подстановки оставляем видимыми
    await call.answer()
    try:
        if photo:
            await call.message.answer_photo(photo, caption=text)
        else:
            await call.message.answer(text)
    except Exception as exc:  # noqa: BLE001 - показываем админу, что не так
        log.warning("Предпросмотр %s не отправился: %s", key, exc)
        await call.message.answer(f"⚠️ Сообщение не отправляется: {esc(exc)}")


@router.callback_query(TemplateCB.filter(F.action == "edit"))
async def ask_new(call: CallbackQuery, callback_data: TemplateCB, state: FSMContext) -> None:
    key = callback_data.key or ""
    definition = DEFS_BY_KEY.get(key)
    if definition is None:
        await call.answer("Сообщение не найдено", show_alert=True)
        return

    await state.set_state(AdminSG.template)
    await state.update_data(key=key, category=callback_data.category or definition.category,
                            page=callback_data.page)

    fields = "\n".join(f"• <code>{{{name}}}</code>" for name in definition.placeholders)
    await show(
        call,
        block(
            title("✏️", f"Новое сообщение: {esc(definition.title)}"),
            "Пришлите его одним сообщением — текстом или фото с подписью.\n"
            "Форматирование и премиум-эмодзи сохранятся как есть.",
            f"Доступные подстановки:\n{fields}" if fields else "Подстановок у этого экрана нет.",
            note("⚠️", "Что пришлёте, то и будет. Текст без фото — картинка снимется."),
        ),
        akb.template_card(key, callback_data.category or definition.category, callback_data.page),
    )


@router.message(AdminSG.template, F.text | F.caption)
async def save_new(message: Message, session: AsyncSession, user: User, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("key", "")
    definition = DEFS_BY_KEY.get(key)
    if definition is None:
        await state.clear()
        await message.answer("❌ Сообщение не найдено.", reply_markup=akb.back_to_admin())
        return

    photo_id = message.photo[-1].file_id if message.photo else None
    text = message.html_text

    if not text.strip() and not photo_id:
        await message.answer("❌ Пустое сообщение. Пришлите текст или фото с подписью.")
        return

    # Подпись к фото у Telegram короче обычного сообщения — лучше сказать
    # сразу, чем потом ловить отказ на каждом показе экрана.
    limit = CAPTION_LIMIT if photo_id else TEXT_LIMIT
    if len(text) > limit:
        await message.answer(
            f"❌ Слишком длинно: {len(text)} символов при лимите {limit}.\n"
            + ("Подпись к фото короче обычного сообщения — сократите текст или уберите фото."
               if photo_id else "Сократите текст.")
        )
        return

    await templates.set(session, key, text, photo_id, admin_id=user.tg_id)
    await state.clear()
    log.info("Админ %s изменил сообщение %s (фото: %s)", user.tg_id, key, bool(photo_id))

    await message.answer(
        block(title("✅", f"Сообщение «{esc(definition.title)}» обновлено"),
              "Показать, как оно выглядит — кнопка «Показать как есть»."),
        reply_markup=akb.template_card(key, data.get("category", definition.category), data.get("page", 0)),
    )


@router.message(AdminSG.template)
async def reject_unsupported(message: Message) -> None:
    await message.answer("❌ Нужен текст или фото с подписью. Другие вложения не поддерживаются.")


@router.callback_query(TemplateCB.filter(F.action == "reset"))
async def reset(call: CallbackQuery, callback_data: TemplateCB, session: AsyncSession, user: User) -> None:
    key = callback_data.key or ""
    if key not in DEFS_BY_KEY:
        await call.answer("Сообщение не найдено", show_alert=True)
        return

    await templates.reset(session, key, admin_id=user.tg_id)
    await show(call, card_text(key),
               akb.template_card(key, callback_data.category or "main", callback_data.page),
               toast="Сброшено")

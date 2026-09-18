"""Постоянная клавиатура внизу экрана.

Раскладка: широкая кнопка основного действия, две пары, широкая. Верхний
уровень навигации живёт здесь, действия внутри разделов — на инлайн-кнопках.

На каждую кнопку в админке три параметра: подпись, иконка и цвет.
Иконка может быть премиум-эмодзи — тогда она уходит отдельным полем
`icon_custom_emoji_id` (Bot API 9.4), а подпись остаётся чистым текстом.
Обычное эмодзи просто встаёт в начало подписи. Пустая подпись прячет кнопку.
"""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from bot.services.settings import settings
from bot.utils.style import custom_emoji_id, plain_emoji

DEAL = "btn_deal"
DEPOSIT = "btn_deposit"
CHECK = "btn_check"
PROFILE = "btn_profile"
INFO = "btn_info"
PROJECTS = "btn_projects"
ADMIN = "btn_admin"

ALL_KEYS = (DEAL, DEPOSIT, CHECK, PROFILE, INFO, PROJECTS, ADMIN)

# Кнопка → настройка, которая может её выключить целиком.
GUARDS = {
    DEAL: "deal_enabled",
    DEPOSIT: "deposit_enabled",
    CHECK: "check_enabled",
}


def icon_id(key: str) -> str | None:
    """id премиум-эмодзи для иконки кнопки, если оно задано."""
    return custom_emoji_id(settings.get(f"{key}_icon"))


def button_text(key: str) -> str:
    """Подпись кнопки ровно в том виде, в каком её увидит Telegram.

    Премиум-иконка едет отдельным полем, поэтому в текст не попадает.
    Обычное эмодзи, наоборот, приходится ставить в начало подписи.
    """
    text = settings.get(key).strip()
    if not text:
        return ""
    if icon_id(key):
        return text
    emoji = plain_emoji(settings.get(f"{key}_icon"))
    return f"{emoji} {text}".strip() if emoji else text


def button_style(key: str) -> str | None:
    value = settings.get(f"{key}_style").strip()
    return value or None


def enabled(key: str) -> bool:
    if not settings.get(key).strip():
        return False
    guard = GUARDS.get(key)
    return settings.get_bool(guard) if guard else True


def _row(*keys: str) -> list[KeyboardButton]:
    return [
        KeyboardButton(
            text=button_text(key),
            icon_custom_emoji_id=icon_id(key),
            style=button_style(key),
        )
        for key in keys
        if enabled(key)
    ]


def main_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [row for row in (_row(DEAL), _row(DEPOSIT, CHECK), _row(PROFILE, INFO), _row(PROJECTS)) if row]

    if is_admin and enabled(ADMIN):
        rows.append(_row(ADMIN))

    if not rows:  # всё спрятали в админке — оставляем хотя бы профиль
        rows = [[KeyboardButton(text="Профиль")]]

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите раздел или пришлите @юзернейм",
    )


def remove() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()

"""Постоянная клавиатура внизу экрана.

Раскладка: широкая кнопка основного действия, затем две пары, затем
широкая. Верхний уровень навигации живёт здесь, действия внутри разделов —
на инлайн-кнопках под сообщением.

Подписи берутся из настроек (раздел «Кнопки меню»), поэтому переименовать
или спрятать кнопку можно из админки. Пустая подпись = кнопка скрыта.

Подпись кнопки Telegram передаёт обычной строкой, без entities, поэтому
премиум-эмодзи ставятся в текст сообщения, а не на саму кнопку.
"""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from bot.services.settings import settings

# Ключ настройки → нужен ли раздел вообще (проверяется на лету).
DEAL = "btn_deal"
DEPOSIT = "btn_deposit"
CHECK = "btn_check"
PROFILE = "btn_profile"
INFO = "btn_info"
PROJECTS = "btn_projects"
ADMIN = "btn_admin"


def label(key: str) -> str:
    return settings.get(key).strip()


def _enabled(key: str) -> bool:
    """Кнопка показывается, если у неё есть подпись и раздел не выключен."""
    if not label(key):
        return False
    if key == DEPOSIT:
        return settings.get_bool("deposit_enabled")
    if key == CHECK:
        return settings.get_bool("check_enabled")
    if key == DEAL:
        return settings.get_bool("deal_enabled")
    return True


def _row(*keys: str) -> list[KeyboardButton]:
    return [KeyboardButton(text=label(key)) for key in keys if _enabled(key)]


def main_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [row for row in (_row(DEAL), _row(DEPOSIT, CHECK), _row(PROFILE, INFO), _row(PROJECTS)) if row]

    if is_admin and _enabled(ADMIN):
        rows.append(_row(ADMIN))

    if not rows:  # всё спрятали в админке — оставляем хотя бы профиль
        rows = [[KeyboardButton(text="👤 Профиль")]]

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите раздел или пришлите @юзернейм",
    )


def remove() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()

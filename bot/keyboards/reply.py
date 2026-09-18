"""Постоянная клавиатура внизу экрана.

Верхний уровень навигации живёт здесь, действия внутри разделов — на
инлайн-кнопках. Реплай-клавиатура и инлайн-клавиатура не уживаются в одном
сообщении, поэтому схема такая: клавиатура ставится один раз на /start и
висит всегда, а каждый экран приходит со своими инлайн-кнопками.

⚠️ Подписи — черновик до макетов. Менять здесь, в одном месте.
Премиум-эмодзи на кнопках Telegram не поддерживает (подпись кнопки —
обычная строка без entities), поэтому тут только юникод.
"""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from bot.services.settings import settings

BUTTONS: dict[str, str] = {
    "deals": "🤝 Сделки",
    "balance": "💰 Баланс",
    "deposit": "🛡 Депозит",
    "check": "🔍 Проверить",
    "rules": "📜 Правила",
    "support": "💬 Поддержка",
    "admin": "⚙️ Админка",
}


def main_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Разделы верхнего уровня. Выключенные в админке не показываем."""
    rows: list[list[KeyboardButton]] = [
        [KeyboardButton(text=BUTTONS["deals"]), KeyboardButton(text=BUTTONS["balance"])]
    ]

    second = []
    if settings.get_bool("deposit_enabled"):
        second.append(KeyboardButton(text=BUTTONS["deposit"]))
    if settings.get_bool("check_enabled"):
        second.append(KeyboardButton(text=BUTTONS["check"]))
    if second:
        rows.append(second)

    third = [KeyboardButton(text=BUTTONS["rules"])]
    if settings.get("support_username").strip():
        third.append(KeyboardButton(text=BUTTONS["support"]))
    rows.append(third)

    if is_admin:
        rows.append([KeyboardButton(text=BUTTONS["admin"])])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите раздел или введите @юзернейм",
    )


def remove() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()

"""Оформление сообщений.

Единый стиль: иконка и жирный заголовок, под ним параметры деревом
(`├` для строк, `╰` для последней), значения — моноширинные.

    🔍 <b>Поиск пользователя</b>
    ├ <b>ID:</b> <code>8561401973</code>
    ╰ <b>Юзернейм:</b> <code>@user</code>

Иконку можно заменить премиум-эмодзи прямо в админке — тексты хранятся
как HTML, а premium-эмодзи приезжает в них тегом <tg-emoji>.
"""

from __future__ import annotations

from collections.abc import Sequence

BRANCH = "├"
LAST = "╰"


def mono(value: object) -> str:
    """Моноширинное значение — его удобно выделять и копировать."""
    return f"<code>{value}</code>"


def title(icon: str, text: str, tail: str = "") -> str:
    """Заголовок экрана: иконка, жирный текст и необязательное продолжение."""
    head = f"{icon} <b>{text}</b>" if icon else f"<b>{text}</b>"
    return f"{head} {tail}".rstrip()


def tree(rows: Sequence[tuple[str, object]]) -> str:
    """Параметры деревом. Пустые значения пропускаем, чтобы не было дыр."""
    items = [(label, value) for label, value in rows if value not in (None, "")]
    if not items:
        return ""

    lines = []
    for index, (label, value) in enumerate(items):
        branch = LAST if index == len(items) - 1 else BRANCH
        lines.append(f"{branch} <b>{label}:</b> {value}")
    return "\n".join(lines)


def block(*parts: str) -> str:
    """Склеить куски экрана, выбросив пустые и не наплодив пустых строк."""
    return "\n\n".join(part.strip("\n") for part in parts if part and part.strip())


def note(icon: str, text: str) -> str:
    """Сноска внизу сообщения."""
    return f"{icon} {text}" if icon else text


def bold(value: object) -> str:
    return f"<b>{value}</b>"


def bullets(rows: Sequence[tuple[str, object]]) -> str:
    """Строки списком: `• Подпись: значение`. Пустые значения пропускаем."""
    items = [(label, value) for label, value in rows if value not in (None, "")]
    return "\n".join(f"• {label}: {value}" for label, value in items)


def section(icon: str, heading: str, rows: Sequence[tuple[str, object]]) -> str:
    """Раздел профиля: иконка, жирный заголовок, под ним список."""
    body = bullets(rows)
    head = title(icon, heading)
    return f"{head}\n{body}" if body else head

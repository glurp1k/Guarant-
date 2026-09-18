"""Хелперы для текстов."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

USERNAME_RE = re.compile(r"^@?([A-Za-z0-9_]{4,32})$")


def esc(value: object) -> str:
    """Экранировать пользовательский текст перед вставкой в HTML-сообщение."""
    return html.escape(str(value if value is not None else ""), quote=False)


def normalize_username(text: str) -> str | None:
    """`@User`, `user`, `t.me/user` → `user`. None — если не похоже на юзернейм."""
    value = (text or "").strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "tg://resolve?domain="):
        if value.lower().startswith(prefix):
            value = value[len(prefix):]
            break
    value = value.split("?")[0].strip()
    match = USERNAME_RE.match(value)
    return match.group(1).lower() if match else None


def fmt_date(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.strftime("%d.%m.%Y %H:%M")


def plural(number: int, one: str, few: str, many: str) -> str:
    """`1 сделка`, `2 сделки`, `5 сделок`."""
    n = abs(number) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many

"""Значения для подстановок в шаблонах сообщений.

Шаблоны правит админ, поэтому набор доступных полей должен быть стабильным
и безопасным: всё, что приходит от пользователя, экранируется здесь, а не
надеется на разметку в шаблоне.
"""

from __future__ import annotations

from decimal import Decimal

from bot.db.models import User
from bot.services import deposits
from bot.services.deals import DealStats
from bot.utils.money import fmt
from bot.utils.texts import esc, fmt_date

ZERO = Decimal("0")


def user_values(user: User, stats: DealStats | None = None) -> dict[str, object]:
    """Поля пользователя: профиль, карточка проверки, депозит."""
    total = stats.total if stats else user.deals_done
    volume = stats.volume if stats else user.deals_volume

    return {
        "name": esc(user.full_name) or "—",
        "username": f"@{esc(user.username)}" if user.username else "—",
        "id": user.tg_id,
        "balance": fmt(user.balance),
        "deposit": fmt(user.deposit),
        "status": deposits.trust_label(user),
        "registered": fmt_date(user.created_at, with_time=False),
        "reviews": user.reputation,
        "reviews_plus": user.reviews_plus,
        "reviews_minus": user.reviews_minus,
        "deals_total": total,
        "deals_volume": fmt(volume),
        "deals_as_buyer": stats.as_buyer if stats else 0,
        "buyer_volume": fmt(stats.buyer_volume if stats else ZERO),
        "deals_as_seller": stats.as_seller if stats else 0,
        "seller_volume": fmt(stats.seller_volume if stats else ZERO),
    }

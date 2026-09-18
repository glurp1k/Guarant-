"""Сообщения бота, редактируемые из админки.

Каждый экран — шаблон: текст и необязательное фото. Админ присылает боту
новое сообщение, и оно становится экраном как есть — с форматированием,
премиум-эмодзи и картинкой. Внутри текста работают подстановки вида
{balance}: их список свой у каждого шаблона и виден в админке.

Хранится готовый HTML (message.html_text), потому что только так переживают
и разметка, и премиум-эмодзи: Telegram отдаёт их entity, а не символом.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Template

log = logging.getLogger(__name__)

PLACEHOLDER = re.compile(r"\{(\w+)\}")
CAPTION_LIMIT = 1024   # столько влезает в подпись к фото
TEXT_LIMIT = 4096      # столько — в обычное сообщение


@dataclass(frozen=True, slots=True)
class TemplateDef:
    key: str
    title: str
    category: str
    default: str
    placeholders: tuple[str, ...] = field(default=())
    hint: str = ""


CATEGORIES: dict[str, str] = {
    "main": "🏠 Главное",
    "profile": "👤 Профиль",
    "check": "🔍 Проверка",
    "money": "💰 Деньги",
    "deals": "🤝 Сделки",
}

# Подстановки, общие для карточек пользователя.
USER_FIELDS = (
    "name", "username", "id", "balance", "deposit", "status", "registered",
    "reviews", "reviews_plus", "reviews_minus",
    "deals_total", "deals_volume",
    "deals_as_buyer", "buyer_volume", "deals_as_seller", "seller_volume",
)


DEFINITIONS: tuple[TemplateDef, ...] = (
    # ------------------------------- Главное ------------------------------- #
    TemplateDef(
        "start", "Приветствие", "main",
        "👋 <b>Привет, {name}!</b>\n\n"
        "Это гарант-сервис для безопасных сделок.\n"
        "Деньги покупателя хранятся у гаранта и уходят продавцу только после подтверждения.\n\n"
        "🛡 Страховой депозит повышает доверие к вам — его видно в проверке по юзернейму.",
        ("name", "username", "id"),
    ),
    TemplateDef(
        "info", "Информация", "main",
        "ℹ️ <b>Информация</b>\n\n"
        "Гарант удерживает деньги покупателя до подтверждения сделки.\n"
        "Страховой депозит показывает, насколько человеку можно доверять — "
        "его видно в проверке по юзернейму.",
        ("name", "username", "id"),
    ),
    TemplateDef(
        "rules", "Правила", "main",
        "📜 <b>Правила сервиса</b>\n\n"
        "1. Все споры решает администрация.\n"
        "2. Средства удерживаются до подтверждения покупателем.\n"
        "3. Комиссия сервиса не возвращается при отмене после оплаты.",
    ),
    TemplateDef(
        "projects", "Наши проекты", "main",
        "✨ <b>Наши проекты</b>\n\n"
        "Здесь можно рассказать о своих каналах и сервисах — "
        "текст, картинка и ссылки меняются в админке.",
    ),
    TemplateDef(
        "maintenance", "Технические работы", "main",
        "🛠 Ведутся технические работы, зайдите позже.",
    ),

    # ------------------------------- Профиль ------------------------------- #
    TemplateDef(
        "profile", "Профиль", "profile",
        "❓ <b>Информация</b>\n"
        "• Никнейм: <b>{username}</b>\n"
        "• ID: <code>{id}</code>\n"
        "• Кол-во сделок: <b>{deals_total}</b>\n\n"
        "⭐ <b>Репутация</b>\n"
        "• Депозит: <b>{deposit}</b>\n"
        "• Отзывы (+ / −): <b>{reviews}</b>\n"
        "• Статус: <b>{status}</b>\n"
        "• Дата регистрации: <b>{registered}</b>\n\n"
        "📊 <b>Статистика сделок</b>\n"
        "• Сделки ({deals_total}): <b>{deals_volume}</b>\n"
        "• Покупатель ({deals_as_buyer}): <b>{buyer_volume}</b>\n"
        "• Продавец ({deals_as_seller}): <b>{seller_volume}</b>\n\n"
        "💰 <b>Финансы</b>\n"
        "• Баланс: <b>{balance}</b>",
        USER_FIELDS,
    ),
    TemplateDef(
        "deposit", "Страховой депозит", "profile",
        "🛡 <b>Страховой депозит</b>\n\n"
        "Это замороженная сумма, которая подтверждает вашу надёжность.\n"
        "Любой пользователь может проверить её по вашему юзернейму.\n\n"
        "• Ваш депозит: <b>{deposit}</b>\n"
        "• Статус: <b>{status}</b>\n"
        "• Минимум: <b>{min}</b>",
        USER_FIELDS + ("min", "max", "locked_until"),
    ),

    # ------------------------------- Проверка ------------------------------ #
    TemplateDef(
        "check_prompt", "Проверка: приглашение", "check",
        "🔍 <b>Проверка пользователя.</b> Найти человека можно по любому из параметров:\n"
        "├ <b>ID:</b> <code>8561401973</code>\n"
        "├ <b>Юзернейм:</b> <code>GreedyHatesAI</code>\n"
        "├ <b>Юзернейм (полный):</b> <code>@GreedyHatesAI</code>\n"
        "╰ <b>Юзернейм (ссылка):</b> <code>https://t.me/GreedyHatesAI</code>\n\n"
        "📖 <b>Регистр не важен.</b> При поиске нет разницы между "
        "<code>UserName</code> и <code>username</code>.",
    ),
    TemplateDef(
        "check_found", "Проверка: карточка", "check",
        "🔍 <b>Проверка пользователя</b>\n"
        "├ <b>Имя:</b> {name}\n"
        "├ <b>Юзернейм:</b> <code>{username}</code>\n"
        "├ <b>ID:</b> <code>{id}</code>\n"
        "╰ <b>В сервисе с:</b> {registered}\n\n"
        "{status_line}\n\n"
        "🛡 <b>Гарантии</b>\n"
        "├ <b>Страховой депозит:</b> <code>{deposit}</code>\n"
        "├ <b>Отзывы (+ / −):</b> <code>{reviews}</code>\n"
        "├ <b>Закрытых сделок:</b> <code>{deals_total}</code>\n"
        "╰ <b>Оборот:</b> <code>{deals_volume}</code>",
        USER_FIELDS + ("status_line",),
    ),
    TemplateDef(
        "check_not_found", "Проверка: не найден", "check",
        "❌ <b>Пользователь не найден</b>\n\n"
        "По запросу <code>{query}</code> в сервисе никого нет — значит, "
        "ни страхового депозита, ни истории сделок у него тоже нет.\n\n"
        "⚠️ Будьте осторожны и работайте только через гаранта.",
        ("query",),
    ),

    # -------------------------------- Деньги ------------------------------- #
    TemplateDef(
        "topup_choose", "Пополнение: выбор способа", "money",
        "💳 <b>Пополнение</b>\n\n"
        "• Минимум: <b>{min}</b>\n\n"
        "Выберите способ оплаты:",
        ("min", "max", "balance", "deposit"),
    ),
    TemplateDef(
        "invoice_crypto", "Счёт: криптоперевод", "money",
        "🧾 <b>Счёт #{id}</b>\n\n"
        "Сеть: <b>{network}</b>\n"
        "Адрес:\n<code>{address}</code>\n\n"
        "Сумма к отправке (ровно):\n<code>{amount}</code>\n\n"
        "⚠️ Отправьте <b>точную</b> сумму — по ней бот опознает ваш платёж.\n"
        "Счёт действует до {expires} UTC.",
        ("id", "network", "address", "amount", "expires", "target"),
    ),
    TemplateDef(
        "invoice_cryptobot", "Счёт: CryptoBot", "money",
        "🧾 <b>Счёт #{id}</b>\n\n"
        "Сумма: <b>{amount}</b>\n"
        "Способ: CryptoBot\n"
        "Действует до: {expires} UTC\n\n"
        "Нажмите «Оплатить», средства зачислятся автоматически.",
        ("id", "amount", "expires", "target"),
    ),
    TemplateDef(
        "withdraw_menu", "Вывод: выбор способа", "money",
        "📤 <b>Вывод {source}</b>\n\n"
        "Доступно: <b>{available}</b>\n"
        "Лимиты: {limits}\n\n"
        "<b>Комиссии:</b>\n{fees}\n\n"
        "Выберите способ вывода:",
        ("source", "available", "limits", "fees"),
    ),

    # -------------------------------- Сделки ------------------------------- #
    TemplateDef(
        "deal_card", "Карточка сделки", "deals",
        "🤝 <b>Сделка {code}</b>\n\n"
        "Статус: {status}\n"
        "Сумма: <b>{amount}</b>\n"
        "Комиссия: <b>{commission}</b> (платит {payer})\n\n"
        "🛍 Продавец: {seller}\n"
        "💵 Покупатель: {buyer}\n\n"
        "📝 Предмет сделки:\n{description}",
        ("code", "status", "amount", "commission", "payer", "seller", "buyer",
         "description", "link", "payout", "charge"),
    ),
    TemplateDef(
        "review_ask", "Просьба оставить отзыв", "deals",
        "⭐ <b>Сделка {code} закрыта</b>\n\n"
        "Оцените вторую сторону — отзыв увидят все, кто будет её проверять.",
        ("code", "partner"),
    ),
)

DEFS_BY_KEY: dict[str, TemplateDef] = {item.key: item for item in DEFINITIONS}


def fill(text: str, values: dict[str, object]) -> str:
    """Подставить значения. Незнакомое {что_то} остаётся как есть.

    Регуляркой, а не format_map: в тексте от админа могут встретиться
    фигурные скобки, и падать из-за них сообщение не должно.
    """
    return PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), m.group(0))), text)


class TemplateService:
    """Кэшированный доступ к шаблонам."""

    def __init__(self) -> None:
        self._text: dict[str, str] = {d.key: d.default for d in DEFINITIONS}
        self._photo: dict[str, str | None] = {d.key: None for d in DEFINITIONS}

    async def load(self, session: AsyncSession) -> None:
        rows = (await session.execute(select(Template))).scalars().all()
        stored = {row.key: row for row in rows}

        created = 0
        for definition in DEFINITIONS:
            row = stored.get(definition.key)
            if row is None:
                session.add(Template(key=definition.key, text=definition.default))
                created += 1
                continue
            self._text[definition.key] = row.text or definition.default
            self._photo[definition.key] = row.photo_id

        if created:
            await session.commit()
        log.info("Шаблоны загружены (%s новых)", created)

    def text(self, key: str) -> str:
        return self._text.get(key, DEFS_BY_KEY[key].default if key in DEFS_BY_KEY else "")

    def photo(self, key: str) -> str | None:
        return self._photo.get(key)

    def render(self, key: str, **values: object) -> tuple[str, str | None]:
        """Готовый текст и фото для отправки."""
        return fill(self.text(key), values), self.photo(key)

    async def set(
        self,
        session: AsyncSession,
        key: str,
        text: str,
        photo_id: str | None,
        admin_id: int | None = None,
    ) -> None:
        row = await session.get(Template, key)
        if row is None:
            row = Template(key=key)
            session.add(row)
        row.text = text
        row.photo_id = photo_id
        row.updated_by = admin_id

        self._text[key] = text
        self._photo[key] = photo_id
        await session.commit()

    async def reset(self, session: AsyncSession, key: str, admin_id: int | None = None) -> None:
        definition = DEFS_BY_KEY[key]
        await self.set(session, key, definition.default, None, admin_id)

    def preview(self, key: str) -> str:
        """Короткая строка для списка в админке."""
        flat = " ".join(self.text(key).split())
        mark = "🖼 " if self.photo(key) else ""
        return mark + (flat[:38] + "…" if len(flat) > 38 else flat or "— пусто")


templates = TemplateService()

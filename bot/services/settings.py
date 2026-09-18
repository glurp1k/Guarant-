"""Динамические настройки бота.

Каждый параметр объявлен в реестре ниже: ключ, тип, значение по умолчанию,
раздел админки и подпись. Значения хранятся в таблице `settings` строками,
в памяти держится кэш, чтобы хендлеры не ходили в БД за каждой цифрой.

Чтобы добавить новый настраиваемый параметр, достаточно дописать сюда
одну строку — в админке он появится сам.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Setting

log = logging.getLogger(__name__)

TRUE_VALUES = {"1", "true", "yes", "on", "да", "вкл"}


@dataclass(frozen=True, slots=True)
class SettingDef:
    key: str
    default: str
    type: str          # str | int | decimal | bool | text
    title: str
    category: str
    hint: str = ""
    choices: tuple[str, ...] = field(default=())


# --------------------------------------------------------------------------- #
# Разделы админки
# --------------------------------------------------------------------------- #

CATEGORIES: dict[str, str] = {
    "general": "⚙️ Основное",
    "deals": "🤝 Сделки и комиссия",
    "deposit": "🛡 Страховой депозит",
    "topup": "💳 Пополнение",
    "withdraw": "📤 Вывод",
    "requisites": "🔑 Реквизиты и API",
    "check": "🔍 Проверка по юзернейму",
    "reviews": "⭐ Отзывы",
    "menu": "⌨️ Кнопки меню",
}


DEFINITIONS: tuple[SettingDef, ...] = (
    # ----------------------------- Основное -------------------------------- #
    SettingDef("bot_name", "Guarant", "str", "Название сервиса", "general"),
    SettingDef("support_username", "", "str", "Юзернейм поддержки", "general", "Без @, например: support"),
    SettingDef("channel_url", "", "str", "Ссылка на канал", "general"),
    SettingDef("chat_url", "", "str", "Ссылка на чат", "general"),
    SettingDef("reviews_url", "", "str", "Ссылка на отзывы", "general"),
    SettingDef("currency_symbol", "$", "str", "Символ валюты", "general", "Например: $ или USDT"),
    SettingDef("currency_position", "before", "str", "Символ до или после суммы", "general",
               "before — «$ 10,00», after — «10,00 USDT»", ("before", "after")),
    SettingDef("currency_comma", "1", "bool", "Запятая как разделитель дробной части", "general"),
    SettingDef("maintenance", "0", "bool", "Технические работы", "general", "Бот отвечает только админам"),

    # ------------------------------ Сделки --------------------------------- #
    SettingDef("deal_enabled", "1", "bool", "Сделки включены", "deals"),
    SettingDef("deal_commission_percent", "3", "decimal", "Комиссия гаранта, %", "deals"),
    SettingDef("deal_commission_min", "0.5", "decimal", "Минимальная комиссия, USDT", "deals"),
    SettingDef("deal_commission_max", "0", "decimal", "Максимальная комиссия, USDT", "deals", "0 — без ограничения"),
    SettingDef("deal_min_amount", "1", "decimal", "Минимальная сумма сделки", "deals"),
    SettingDef("deal_max_amount", "0", "decimal", "Максимальная сумма сделки", "deals", "0 — без ограничения"),
    SettingDef("deal_commission_payer", "seller", "str", "Кто платит комиссию по умолчанию", "deals",
               "seller / buyer / split", ("seller", "buyer", "split")),
    SettingDef("deal_auto_cancel_hours", "24", "int", "Автоотмена неоплаченной сделки, ч", "deals", "0 — не отменять"),

    # ------------------------- Страховой депозит --------------------------- #
    SettingDef("deposit_enabled", "1", "bool", "Страховой депозит включён", "deposit"),
    SettingDef("deposit_min", "10", "decimal", "Минимальное пополнение депозита", "deposit"),
    SettingDef("deposit_max", "0", "decimal", "Максимальный размер депозита", "deposit", "0 — без ограничения"),
    SettingDef("deposit_from_balance", "1", "bool", "Можно пополнять депозит с баланса", "deposit"),
    SettingDef("deposit_withdraw_enabled", "1", "bool", "Разрешён вывод депозита", "deposit"),
    SettingDef("deposit_lock_days", "0", "int", "Заморозка депозита после пополнения, дней", "deposit",
               "0 — снимать можно сразу"),
    SettingDef("deposit_withdraw_fee_percent", "0", "decimal", "Комиссия за снятие депозита, %", "deposit"),
    SettingDef("deposit_required_for_deal", "0", "decimal", "Мин. депозит для создания сделки", "deposit",
               "0 — депозит не обязателен"),

    # ----------------------------- Пополнение ------------------------------ #
    SettingDef("topup_enabled", "1", "bool", "Пополнение включено", "topup"),
    SettingDef("topup_min", "1", "decimal", "Минимальная сумма пополнения", "topup"),
    SettingDef("topup_max", "0", "decimal", "Максимальная сумма пополнения", "topup", "0 — без ограничения"),
    SettingDef("topup_cryptobot_enabled", "1", "bool", "CryptoBot", "topup"),
    SettingDef("topup_trc20_enabled", "1", "bool", "USDT TRC20", "topup"),
    SettingDef("topup_bep20_enabled", "1", "bool", "USDT BEP20", "topup"),
    SettingDef("invoice_lifetime_min", "60", "int", "Время жизни счёта, мин", "topup"),
    SettingDef("topup_min_confirmations", "1", "int", "Подтверждений сети для зачисления", "topup"),

    # -------------------------------- Вывод -------------------------------- #
    SettingDef("withdraw_enabled", "1", "bool", "Вывод включён", "withdraw"),
    SettingDef("withdraw_min", "5", "decimal", "Минимальная сумма вывода", "withdraw"),
    SettingDef("withdraw_max", "0", "decimal", "Максимальная сумма вывода за раз", "withdraw", "0 — без ограничения"),
    SettingDef("withdraw_cryptobot_enabled", "1", "bool", "Вывод в CryptoBot", "withdraw"),
    SettingDef("withdraw_trc20_enabled", "1", "bool", "Вывод на USDT TRC20", "withdraw"),
    SettingDef("withdraw_bep20_enabled", "1", "bool", "Вывод на USDT BEP20", "withdraw"),
    SettingDef("withdraw_fee_cryptobot_percent", "0", "decimal", "Комиссия вывода CryptoBot, %", "withdraw"),
    SettingDef("withdraw_fee_trc20_percent", "2", "decimal", "Комиссия вывода TRC20, %", "withdraw"),
    SettingDef("withdraw_fee_bep20_percent", "2", "decimal", "Комиссия вывода BEP20, %", "withdraw"),
    SettingDef("withdraw_fee_cryptobot_fixed", "0", "decimal", "Фикс. комиссия CryptoBot, USDT", "withdraw"),
    SettingDef("withdraw_fee_trc20_fixed", "1", "decimal", "Фикс. комиссия TRC20, USDT", "withdraw", "Сеть"),
    SettingDef("withdraw_fee_bep20_fixed", "0.5", "decimal", "Фикс. комиссия BEP20, USDT", "withdraw", "Сеть"),
    SettingDef("withdraw_auto_cryptobot", "0", "bool", "Автовыплата чеком CryptoBot", "withdraw",
               "Без ручного подтверждения админом"),

    # ------------------------------ Реквизиты ------------------------------ #
    SettingDef("cryptobot_token", "", "str", "Токен Crypto Pay (CryptoBot)", "requisites",
               "@CryptoBot → Crypto Pay → My Apps"),
    SettingDef("usdt_trc20_address", "", "str", "Адрес USDT TRC20", "requisites"),
    SettingDef("usdt_bep20_address", "", "str", "Адрес USDT BEP20", "requisites"),
    SettingDef("trongrid_api_key", "", "str", "API-ключ TronGrid", "requisites", "Необязательно, но снимает лимиты"),
    SettingDef("bscscan_api_key", "", "str", "API-ключ BscScan / Etherscan V2", "requisites",
               "Нужен для отслеживания BEP20"),
    SettingDef("payment_check_interval", "30", "int", "Интервал проверки платежей, сек", "requisites"),

    # --------------------------- Проверка по ЮЗ ---------------------------- #
    SettingDef("check_enabled", "1", "bool", "Проверка пользователя по юзернейму", "check"),
    SettingDef("check_show_deposit", "1", "bool", "Показывать размер депозита", "check"),
    SettingDef("check_show_deals", "1", "bool", "Показывать количество сделок", "check"),
    SettingDef("check_show_registered", "1", "bool", "Показывать дату регистрации", "check"),
    SettingDef("check_trusted_from", "100", "decimal", "Депозит для статуса «Надёжный», USDT", "check"),

    # -------------------------------- Отзывы ------------------------------- #
    SettingDef("reviews_enabled", "1", "bool", "Отзывы включены", "reviews"),
    SettingDef("reviews_comment_required", "0", "bool", "Требовать комментарий", "reviews"),
    SettingDef("reviews_show_in_profile", "1", "bool", "Показывать в профиле", "reviews"),
    SettingDef("reviews_show_in_check", "1", "bool", "Показывать при проверке", "reviews"),
    SettingDef("reviews_ask_after_deal", "1", "bool", "Предлагать отзыв после сделки", "reviews"),

    # ---------------------------- Кнопки меню ------------------------------ #
    # На каждую кнопку три параметра: подпись, иконка и цвет.
    # Иконка — обычное эмодзи или премиум: пришлите премиум-эмодзи сообщением,
    # бот возьмёт из него id и повесит на кнопку через icon_custom_emoji_id
    # (Bot API 9.4). Обычное эмодзи просто встаёт в начало подписи.
    # Пустая подпись прячет кнопку.

    SettingDef("btn_deal", "Начать сделку", "str", "Кнопка: сделка", "menu"),
    SettingDef("btn_deal_icon", "🤝", "emoji", "Иконка: сделка", "menu",
               "Можно прислать премиум-эмодзи"),
    SettingDef("btn_deal_style", "", "str", "Цвет: сделка", "menu",
               "Пусто — обычная", ("", "primary", "success", "danger", "link")),
    SettingDef("btn_deposit", "Депозит", "str", "Кнопка: депозит", "menu"),
    SettingDef("btn_deposit_icon", "🛡", "emoji", "Иконка: депозит", "menu",
               "Можно прислать премиум-эмодзи"),
    SettingDef("btn_deposit_style", "", "str", "Цвет: депозит", "menu",
               "Пусто — обычная", ("", "primary", "success", "danger", "link")),
    SettingDef("btn_check", "Проверить", "str", "Кнопка: проверка", "menu"),
    SettingDef("btn_check_icon", "🔍", "emoji", "Иконка: проверка", "menu",
               "Можно прислать премиум-эмодзи"),
    SettingDef("btn_check_style", "", "str", "Цвет: проверка", "menu",
               "Пусто — обычная", ("", "primary", "success", "danger", "link")),
    SettingDef("btn_profile", "Профиль", "str", "Кнопка: профиль", "menu"),
    SettingDef("btn_profile_icon", "👤", "emoji", "Иконка: профиль", "menu",
               "Можно прислать премиум-эмодзи"),
    SettingDef("btn_profile_style", "", "str", "Цвет: профиль", "menu",
               "Пусто — обычная", ("", "primary", "success", "danger", "link")),
    SettingDef("btn_info", "Информация", "str", "Кнопка: информация", "menu"),
    SettingDef("btn_info_icon", "ℹ️", "emoji", "Иконка: информация", "menu",
               "Можно прислать премиум-эмодзи"),
    SettingDef("btn_info_style", "", "str", "Цвет: информация", "menu",
               "Пусто — обычная", ("", "primary", "success", "danger", "link")),
    SettingDef("btn_projects", "Наши проекты", "str", "Кнопка: наши проекты", "menu", "Пусто — кнопка скрыта"),
    SettingDef("btn_projects_icon", "✨", "emoji", "Иконка: наши проекты", "menu",
               "Можно прислать премиум-эмодзи"),
    SettingDef("btn_projects_style", "", "str", "Цвет: наши проекты", "menu",
               "Пусто — обычная", ("", "primary", "success", "danger", "link")),
    SettingDef("btn_admin", "Админка", "str", "Кнопка: админка", "menu", "Видна только администраторам"),
    SettingDef("btn_admin_icon", "⚙️", "emoji", "Иконка: админка", "menu",
               "Можно прислать премиум-эмодзи"),
    SettingDef("btn_admin_style", "", "str", "Цвет: админка", "menu",
               "Пусто — обычная", ("", "primary", "success", "danger", "link"))
)

DEFS_BY_KEY: dict[str, SettingDef] = {item.key: item for item in DEFINITIONS}


class SettingsService:
    """Кэшированный доступ к настройкам."""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {d.key: d.default for d in DEFINITIONS}

    async def load(self, session: AsyncSession) -> None:
        """Прочитать значения из БД и досоздать отсутствующие ключи."""
        rows = (await session.execute(select(Setting))).scalars().all()
        stored = {row.key: row.value for row in rows}

        created = 0
        for definition in DEFINITIONS:
            if definition.key in stored:
                self._cache[definition.key] = stored[definition.key]
            else:
                session.add(Setting(key=definition.key, value=definition.default))
                self._cache[definition.key] = definition.default
                created += 1

        if created:
            await session.commit()
        log.info("Настройки загружены (%s новых ключей)", created)

    # ------------------------------ Чтение ------------------------------- #

    def get(self, key: str) -> str:
        return self._cache.get(key, DEFS_BY_KEY[key].default if key in DEFS_BY_KEY else "")

    def get_bool(self, key: str) -> bool:
        return self.get(key).strip().lower() in TRUE_VALUES

    def get_int(self, key: str) -> int:
        try:
            return int(Decimal(self.get(key) or "0"))
        except (InvalidOperation, ValueError):
            return 0

    def get_decimal(self, key: str) -> Decimal:
        try:
            return Decimal(self.get(key).replace(",", ".") or "0")
        except (InvalidOperation, ValueError):
            return Decimal("0")

    # ------------------------------ Запись ------------------------------- #

    async def set(self, session: AsyncSession, key: str, value: str, admin_id: int | None = None) -> None:
        row = await session.get(Setting, key)
        if row is None:
            row = Setting(key=key)
            session.add(row)
        row.value = value
        row.updated_by = admin_id
        self._cache[key] = value
        await session.commit()

    def describe(self, key: str) -> str:
        """Значение в человекочитаемом виде — для списков в админке."""
        definition = DEFS_BY_KEY.get(key)
        raw = self.get(key)
        if definition is None:
            return raw
        if definition.type == "bool":
            return "✅ вкл" if self.get_bool(key) else "❌ выкл"
        if not raw:
            return "— не задано"
        if definition.type == "emoji":
            return raw
        if definition.type == "text":
            flat = " ".join(raw.split())
            return flat[:40] + "…" if len(flat) > 40 else flat
        if key.endswith("_token") or key.endswith("_api_key"):
            return f"{raw[:6]}…{raw[-4:]}" if len(raw) > 12 else "задан"
        return raw


settings = SettingsService()

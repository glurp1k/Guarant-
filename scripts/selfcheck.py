#!/usr/bin/env python3
"""Самопроверка сборки: python scripts/selfcheck.py

Гоняет основные денежные сценарии на временной базе, не обращаясь
ни к Telegram, ни к блокчейну. Полезно после правок и перед деплоем.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP_DB = Path(tempfile.gettempdir()) / "guarant_selfcheck.db"
TMP_DB.unlink(missing_ok=True)

os.environ.setdefault("BOT_TOKEN", "123456789:SELFCHECK")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TMP_DB}"

from bot.db import close_db, init_db, session_scope  # noqa: E402
from bot.db.models import Account, CommissionPayer, DealRole, PayMethod, TxKind  # noqa: E402
from bot.handlers import build_router  # noqa: E402
from bot.keyboards import callbacks as cb  # noqa: E402
from bot.services import deals, deposits, ledger, payments, reviews, users, withdrawals  # noqa: E402
from bot.services.settings import DEFINITIONS, settings  # noqa: E402
from bot.utils.money import parse_amount, q2  # noqa: E402
from bot.utils.texts import normalize_username  # noqa: E402

checks: list[str] = []


def ok(title: str) -> None:
    checks.append(title)
    print(f"  ✓ {title}")


def check_callbacks() -> None:
    """Каждая кнопка должна пережить pack/unpack.

    Пустые сегменты aiogram превращает в None — необязательные строковые
    поля обязаны это допускать, иначе кнопка молча перестанет работать.
    """
    samples = [
        cb.MenuCB(action="main"),
        cb.DepositCB(action="menu"),
        cb.TopupCB(action="choose", purpose="deposit"),
        cb.TopupCB(action="method", method="trc20", purpose="balance"),
        cb.TopupCB(action="check", invoice_id=7),
        cb.WithdrawCB(action="menu", source="deposit"),
        cb.WithdrawCB(action="confirm", method="cryptobot", source="balance"),
        cb.DealCB(action="list"),
        cb.DealCB(action="role", value="seller"),
        cb.DealCB(action="view", deal_id=12),
        cb.AdminCB(action="stats"),
        cb.AdminItemCB(action="wd_paid", item_id=3),
        cb.SettingCB(action="categories"),
        cb.SettingCB(action="list", category="withdraw"),
        cb.SettingCB(action="set", category="deals", key="deal_commission_payer", value="split"),
    ]
    for sample in samples:
        restored = type(sample).unpack(sample.pack())
        assert restored.action == sample.action, sample.pack()
    ok(f"callback-данные ({len(samples)} шт.) распаковываются")


def check_utils() -> None:
    assert parse_amount("10,5") == Decimal("10.50")
    assert parse_amount("-3") is None
    assert parse_amount("абв") is None
    assert normalize_username("https://t.me/Some_User") == "some_user"
    assert normalize_username("@ok_user") == "ok_user"
    assert normalize_username("@ab") is None
    ok("разбор сумм и юзернеймов")


async def check_money() -> None:
    async with session_scope() as s:
        await settings.load(s)
        await settings.set(s, "usdt_trc20_address", "TXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX")
        await settings.set(s, "topup_cryptobot_enabled", "0")

        seller = await users.get_or_create(s, 9001, "sc_seller", "Продавец")
        buyer = await users.get_or_create(s, 9002, "sc_buyer", "Покупатель")

        invoice = await payments.create_invoice(s, buyer, Decimal("100"), PayMethod.TRC20, Account.BALANCE)
        assert Decimal("100") < invoice.pay_amount < Decimal("101")
        credited = await payments.mark_paid(s, invoice, invoice.pay_amount, tx_hash="tx-1")
        assert buyer.balance == credited
        assert await payments.mark_paid(s, invoice, invoice.pay_amount, "tx-1") == Decimal("0")
        ok("пополнение зачисляется один раз")

        deal = await deals.create(s, seller, Decimal("50"), "Тест", DealRole.SELLER, CommissionPayer.SELLER)
        assert q2(deal.commission) == Decimal("1.50")
        await deals.join(s, deal, buyer)
        before = buyer.balance
        await deals.fund(s, deal, buyer)
        assert buyer.balance == before - Decimal("50")
        await deals.complete(s, deal)
        assert seller.balance == Decimal("48.50")
        assert seller.deals_done == 1 and buyer.deals_done == 1
        ok("сделка: удержание, комиссия, выплата")

        deal2 = await deals.create(s, seller, Decimal("20"), "Возврат", DealRole.SELLER, CommissionPayer.BUYER)
        await deals.join(s, deal2, buyer)
        balance_before = buyer.balance
        await deals.fund(s, deal2, buyer)
        await deals.refund(s, deal2)
        assert buyer.balance == balance_before, (buyer.balance, balance_before)
        ok("возврат по спору возвращает всю сумму покупателю")

        await deposits.top_up_from_balance(s, seller, Decimal("40"))
        assert seller.deposit == Decimal("40")
        back = await deposits.move_to_balance(s, seller, Decimal("10"))
        assert seller.deposit == Decimal("30") and back == Decimal("10")
        card = deposits.build_card(seller)
        assert "30,00" in card, card
        ok("депозит: пополнение с баланса, снятие, карточка проверки")

        fee = withdrawals.calc_fee(Decimal("20"), PayMethod.TRC20, Account.BALANCE)
        assert fee == Decimal("1.40"), fee
        await ledger.credit(s, seller, Decimal("100"), TxKind.TOPUP)
        await s.commit()
        held = seller.balance
        request = await withdrawals.create_request(
            s, seller, Decimal("20"), PayMethod.TRC20, "T" + "x" * 33, Account.BALANCE
        )
        assert seller.balance == held - Decimal("20")
        await withdrawals.reject(s, request, admin_id=1, comment="проверка")
        assert seller.balance == held
        ok("вывод: списание при заявке и возврат при отказе")

        try:
            await ledger.debit(s, buyer, buyer.balance + Decimal("1"), TxKind.WITHDRAW)
            raise AssertionError("списание в минус должно падать")
        except ledger.InsufficientFunds:
            await s.rollback()
        ok("баланс нельзя увести в минус")

        # --- отзывы ---
        closed = await deals.by_code(s, deal.code)
        await reviews.leave(s, closed, buyer, rating=1, comment="всё чётко")
        assert (seller.reviews_plus, seller.reviews_minus) == (1, 0), seller.reputation

        try:
            await reviews.leave(s, closed, buyer, rating=-1)
            raise AssertionError("второй отзыв по той же сделке не должен пройти")
        except reviews.ReviewError:
            pass
        assert seller.reputation == "1 / 0", seller.reputation

        open_deal = await deals.create(s, seller, Decimal("10"), "не закрыта",
                                       DealRole.SELLER, CommissionPayer.SELLER)
        await deals.join(s, open_deal, buyer)
        try:
            await reviews.leave(s, open_deal, buyer, rating=1)
            raise AssertionError("отзыв по незакрытой сделке не должен пройти")
        except reviews.ReviewError:
            pass
        ok("отзыв: один на сделку, только после закрытия, счётчики сходятся")

        # Поиск для проверки депозита: ID, юзернейм, @юзернейм, ссылка,
        # любой регистр — всё должно вести к одному человеку.
        for query in ("9001", "sc_seller", "SC_Seller", "@SC_SELLER",
                      "https://t.me/Sc_Seller", "t.me/sc_seller"):
            found = await users.find_any(s, query)
            assert found is not None and found.tg_id == 9001, query
        assert await users.find_any(s, "нет_такого") is None
        assert await users.find_any(s, "123456789") is None
        ok("поиск по ID, юзернейму и ссылке, регистр не важен")

        card = deposits.build_card(seller)
        assert "├" in card and "╰" in card and "<code>" in card
        ok("карточка свёрстана деревом с моноширинными значениями")


async def check_presentation() -> None:
    """Оформление: валюта из настроек и премиум-эмодзи в заголовках."""
    from bot.handlers.admin.settings_panel import _validate
    from bot.handlers.common import profile_text
    from bot.services.settings import DEFS_BY_KEY
    from bot.utils.money import fmt

    async with session_scope() as s:
        await settings.set(s, "currency_symbol", "$")
        await settings.set(s, "currency_position", "before")
        await settings.set(s, "currency_comma", "1")
        assert fmt(Decimal("1234.5")) == "$ 1234,50", fmt(Decimal("1234.5"))

        await settings.set(s, "currency_symbol", "USDT")
        await settings.set(s, "currency_position", "after")
        await settings.set(s, "currency_comma", "0")
        assert fmt(Decimal("1234.5")) == "1234.50 USDT", fmt(Decimal("1234.5"))
        ok("сумма форматируется по настройкам валюты")

        # Премиум-эмодзи приезжает от админа готовым HTML — оно должно
        # пережить проверку значения и попасть в заголовок раздела.
        premium = '<tg-emoji emoji-id="5215347090674192358">❓</tg-emoji>'
        assert _validate(DEFS_BY_KEY["icon_info"], premium) == premium
        assert _validate(DEFS_BY_KEY["icon_info"], "две\nстроки") is None
        await settings.set(s, "icon_info", premium)

        user = await users.find_any(s, "9001")
        stats = await deals.stats_for(s, user.tg_id)
        rendered = profile_text(user, stats)
        assert rendered.startswith(premium), rendered[:80]
        assert "• Никнейм:" in rendered and "• ID:" in rendered
        ok("премиум-эмодзи из админки рендерится в заголовке профиля")

        # 9001 продал одну сделку, 9002 её купил; отменённая в счёт не идёт.
        assert (stats.as_seller, stats.as_buyer) == (1, 0), stats
        assert stats.seller_volume == Decimal("50.000000"), stats

        buyer_stats = await deals.stats_for(s, 9002)
        assert (buyer_stats.as_seller, buyer_stats.as_buyer) == (0, 1), buyer_stats
        assert buyer_stats.total == 1 and buyer_stats.volume == Decimal("50.000000"), buyer_stats
        ok("статистика сделок считается отдельно по ролям")

        # Если у бота нет права слать премиум-эмодзи, Telegram отклонит всё
        # сообщение — должен остаться откат на обычное эмодзи из тега.
        from bot.utils.render import strip_custom_emoji

        assert strip_custom_emoji(rendered).startswith("❓ <b>Информация</b>")
        ok("при отказе Telegram премиум-эмодзи заменяется обычным")

        await settings.set(s, "icon_info", DEFS_BY_KEY["icon_info"].default)
        await settings.set(s, "currency_symbol", "$")
        await settings.set(s, "currency_position", "before")
        await settings.set(s, "currency_comma", "1")


async def check_button_icons(ReplyButton, DEAL, button_text, main_keyboard, strip_markup_icons) -> None:
    """Иконка кнопки: обычное эмодзи уходит в подпись, премиум — отдельным полем.

    Премиум-эмодзи на кнопках появилось в Bot API 9.4 (icon_custom_emoji_id),
    поэтому проект требует aiogram не ниже 3.31.
    """
    from datetime import datetime, timezone

    from aiogram.types import Chat, Message

    premium = '<tg-emoji emoji-id="5215347090674192358">🤝</tg-emoji>'

    async def pressed(text: str) -> bool:
        message = Message(message_id=1, date=datetime.now(timezone.utc),
                          chat=Chat(id=1, type="private"), text=text)
        return await ReplyButton(DEAL)(message)

    async with session_scope() as s:
        assert button_text(DEAL) == "🤝 Начать сделку", button_text(DEAL)
        assert await pressed("🤝 Начать сделку")

        await settings.set(s, "btn_deal_icon", premium)
        await settings.set(s, "btn_deal_style", "success")

        button = main_keyboard().keyboard[0][0]
        assert button.text == "Начать сделку", button.text
        assert button.icon_custom_emoji_id == "5215347090674192358", button.icon_custom_emoji_id
        assert button.style == "success", button.style
        # Подпись меняется вместе с иконкой — фильтр обязан это учитывать.
        assert await pressed("Начать сделку")
        ok("премиум-эмодзи становится иконкой кнопки, подпись остаётся чистой")

        stripped = strip_markup_icons(main_keyboard()).keyboard[0][0]
        assert stripped.icon_custom_emoji_id is None and stripped.text == "Начать сделку"
        ok("при отказе Telegram иконка снимается, кнопка остаётся рабочей")

        await settings.set(s, "btn_deal_icon", "🤝")
        await settings.set(s, "btn_deal_style", "")


async def main() -> int:
    await init_db()

    print("Самопроверка гарант-бота\n")
    check_callbacks()
    check_utils()
    await check_money()

    router = build_router()
    assert router.sub_routers, "роутеры не собрались"
    ok(f"роутеры собираются ({len(router.sub_routers)} шт.)")

    assert len({d.key for d in DEFINITIONS}) == len(DEFINITIONS), "дублирующиеся ключи настроек"
    ok(f"реестр настроек без дублей ({len(DEFINITIONS)} параметров)")

    await check_presentation()

    from bot.filters_reply import ReplyButton
    from bot.keyboards.reply import DEAL, button_text, main_keyboard
    from bot.utils.render import strip_markup_icons

    rows = [[b.text for b in row] for row in main_keyboard(is_admin=True).keyboard]
    assert [len(row) for row in rows] == [1, 2, 2, 1, 1], rows
    assert all(text.strip() for row in rows for text in row), rows
    ok(f"нижняя клавиатура: раскладка {[len(r) for r in rows]}")

    await check_button_icons(ReplyButton, DEAL, button_text, main_keyboard, strip_markup_icons)

    await close_db()
    TMP_DB.unlink(missing_ok=True)

    print(f"\n✅ Всё в порядке: {len(checks)} проверок пройдено")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

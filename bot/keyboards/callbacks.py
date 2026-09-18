"""Фабрики callback-данных.

Каждая кнопка несёт короткий машинно-читаемый префикс, чтобы хендлеры
фильтровались по типу, а не по разбору строк.

Важно: при распаковке aiogram превращает пустые сегменты в None, поэтому
все необязательные строковые поля объявлены как `str | None`. Иначе кнопка
вроде «tp:choose::deposit:0» не пройдёт валидацию и молча не сработает.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class MenuCB(CallbackData, prefix="m"):
    action: str


class TopupCB(CallbackData, prefix="tp"):
    action: str                     # choose | method | check | cancel
    method: str | None = None       # cryptobot | trc20 | bep20
    purpose: str = "balance"        # balance | deposit
    invoice_id: int = 0


class DepositCB(CallbackData, prefix="dp"):
    action: str                     # menu | from_balance | to_balance


class WithdrawCB(CallbackData, prefix="wd"):
    action: str                     # menu | method | confirm | cancel
    method: str | None = None
    source: str = "balance"         # balance | deposit
    request_id: int = 0


class DealCB(CallbackData, prefix="dl"):
    action: str                     # list | new | role | payer | my | view | join | pay | done | dispute | cancel
    deal_id: int = 0
    value: str | None = None


class AdminCB(CallbackData, prefix="ad"):
    action: str                     # menu | stats | withdrawals | disputes | users | invoices | broadcast
    arg: str | None = None
    page: int = 0


class AdminItemCB(CallbackData, prefix="ai"):
    action: str                     # wd_open | wd_paid | deal_release | inv_confirm | user_ban | ...
    item_id: int = 0
    value: str | None = None


class SettingCB(CallbackData, prefix="st"):
    action: str                     # categories | list | edit | toggle | set | reset
    category: str | None = None
    key: str | None = None
    value: str | None = None
    page: int = 0

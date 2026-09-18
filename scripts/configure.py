#!/usr/bin/env python3
"""Первичная настройка реквизитов без захода в админку.

Удобно при первом деплое: реквизиты попадают сразу в базу и не оседают
в истории git. Дальше всё то же самое правится в админке бота.

Пример:
    python scripts/configure.py \
        --cryptobot-token 123456:AA... \
        --trc20 TF9Vf... \
        --bep20 0xc92f... \
        --bscscan-key ABCDEF

Показать текущие значения:
    python scripts/configure.py --show
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.db import close_db, init_db, session_scope  # noqa: E402
from bot.services.settings import CATEGORIES, DEFINITIONS, settings  # noqa: E402

ARG_TO_KEY = {
    "cryptobot_token": "cryptobot_token",
    "trc20": "usdt_trc20_address",
    "bep20": "usdt_bep20_address",
    "trongrid_key": "trongrid_api_key",
    "bscscan_key": "bscscan_api_key",
    "support": "support_username",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Первичная настройка гарант-бота")
    parser.add_argument("--cryptobot-token", help="Токен Crypto Pay из @CryptoBot → Crypto Pay → My Apps")
    parser.add_argument("--trc20", help="Адрес приёма USDT TRC20")
    parser.add_argument("--bep20", help="Адрес приёма USDT BEP20")
    parser.add_argument("--trongrid-key", help="API-ключ TronGrid (необязательно)")
    parser.add_argument("--bscscan-key", help="API-ключ BscScan / Etherscan V2 (нужен для BEP20)")
    parser.add_argument("--support", help="Юзернейм поддержки без @")
    parser.add_argument("--set", nargs=2, action="append", metavar=("КЛЮЧ", "ЗНАЧЕНИЕ"),
                        help="Произвольный параметр, можно повторять")
    parser.add_argument("--show", action="store_true", help="Показать текущие настройки и выйти")
    return parser


def print_settings() -> None:
    for category, title in CATEGORIES.items():
        items = [d for d in DEFINITIONS if d.category == category]
        if not items:
            continue
        print(f"\n{title}")
        for definition in items:
            print(f"  {definition.key:<32} {settings.describe(definition.key)}")


async def main() -> int:
    args = build_parser().parse_args()

    await init_db()
    async with session_scope() as session:
        await settings.load(session)

        if args.show:
            print_settings()
            await close_db()
            return 0

        updates: dict[str, str] = {}
        for arg_name, key in ARG_TO_KEY.items():
            value = getattr(args, arg_name, None)
            if value:
                updates[key] = value.strip()

        known = {d.key for d in DEFINITIONS}
        for key, value in args.set or []:
            if key not in known:
                print(f"❌ Неизвестный параметр: {key}")
                await close_db()
                return 1
            updates[key] = value

        if not updates:
            print("Нечего менять. Посмотрите --help или запустите с --show.")
            await close_db()
            return 0

        for key, value in updates.items():
            await settings.set(session, key, value)
            print(f"✅ {key} → {settings.describe(key)}")

    await close_db()
    print("\nГотово. Остальное настраивается в боте: /admin → Настройки.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

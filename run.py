#!/usr/bin/env python3
"""Запуск бота: python run.py"""

import asyncio

from bot.main import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass

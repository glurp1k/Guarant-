"""Состояния диалогов (FSM)."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class TopupSG(StatesGroup):
    amount = State()


class WithdrawSG(StatesGroup):
    amount = State()
    destination = State()


class DepositSG(StatesGroup):
    to_deposit = State()
    to_balance = State()


class DealSG(StatesGroup):
    amount = State()
    description = State()
    dispute_reason = State()


class ReviewSG(StatesGroup):
    comment = State()


class CheckSG(StatesGroup):
    username = State()


class AdminSG(StatesGroup):
    setting_value = State()
    template = State()
    user_query = State()
    amount_delta = State()
    ban_reason = State()
    broadcast = State()
    withdraw_hash = State()
    withdraw_reject = State()

"""Проверка пользователя.

Ввёл ID, юзернейм или ссылку — увидел страховой депозит, историю сделок
и статус доверия. Состав карточки настраивается в админке («Проверка»).
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import user as kb
from bot.keyboards.callbacks import MenuCB
from bot.services import deposits, users as users_service
from bot.services.settings import settings
from bot.states import CheckSG
from bot.utils.render import Event, deny, show
from bot.utils.style import block, mono, note, title, tree
from bot.utils.texts import esc

router = Router(name="check")

PROMPT = block(
    title("🔍", "Проверка пользователя.", "Найти человека можно по любому из параметров:")
    + "\n"
    + tree([
        ("ID", mono("8561401973")),
        ("Юзернейм", mono("GreedyHatesAI")),
        ("Юзернейм (полный)", mono("@GreedyHatesAI")),
        ("Юзернейм (ссылка)", mono("https://t.me/GreedyHatesAI")),
    ]),
    note("📖", "<b>Регистр не важен.</b> При поиске нет разницы между "
               "<code>UserName</code> и <code>username</code>."),
)


def not_found(query: str) -> str:
    return block(
        title("❌", "Пользователь не найден"),
        f"По запросу {mono(esc(query))} в сервисе никого нет — значит, "
        f"ни страхового депозита, ни истории сделок у него тоже нет.",
        note("⚠️", "Будьте осторожны и работайте только через гаранта."),
    )


async def render_check_prompt(event: Event, state: FSMContext) -> None:
    if not settings.get_bool("check_enabled"):
        await deny(event, "Проверка сейчас отключена")
        return

    await state.set_state(CheckSG.username)
    await show(event, PROMPT, kb.cancel())


async def _reply_card(message: Message, session: AsyncSession, raw: str) -> None:
    query = (raw or "").strip()
    target = await users_service.find_any(session, query)

    if target is None:
        await message.answer(not_found(query[:64] or "—"), reply_markup=kb.cancel())
        return

    await message.answer(deposits.build_card(target), reply_markup=kb.back_only())


@router.callback_query(MenuCB.filter(F.action == "check"))
async def ask_username(call: CallbackQuery, state: FSMContext) -> None:
    await render_check_prompt(call, state)


@router.message(CheckSG.username)
async def check_from_state(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not settings.get_bool("check_enabled"):
        await state.clear()
        await message.answer("Проверка сейчас отключена.")
        return
    await _reply_card(message, session, message.text or "")


@router.message(Command("check"))
async def check_command(message: Message, command: CommandObject, session: AsyncSession, state: FSMContext) -> None:
    """Быстрая проверка: /check @username, /check 8561401973"""
    if not settings.get_bool("check_enabled"):
        await message.answer("Проверка сейчас отключена.")
        return

    if not command.args:
        await render_check_prompt(message, state)
        return

    await _reply_card(message, session, command.args)


@router.message(StateFilter(None), F.text.regexp(r"^@[A-Za-z0-9_]{4,32}$"))
async def check_by_bare_username(message: Message, session: AsyncSession) -> None:
    """Просто прислали @username вне диалога — тоже показываем карточку."""
    if not settings.get_bool("check_enabled"):
        return
    await _reply_card(message, session, message.text or "")

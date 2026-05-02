from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config import ROLE_ADMIN
from database import add_user, get_role

router = Router()


@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    add_user(
        message.from_user.id,
        message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )

    lines = [
        "Команды бота:",
        "",
        "/start — открыть главное меню",
        "/help — показать список команд",
        "/pay — выбрать и оплатить тариф",
        "/gift — активировать промокод",
        "/ref — получить реферальную ссылку",
        "/stats — узнать дату окончания подписки",
    ]

    if get_role(message.from_user.id) >= ROLE_ADMIN:
        lines.extend(
            [
                "",
                "Команды администратора:",
                "/resert <user_id> — полностью сбросить подписку и доступ пользователя",
                "/reset <user_id> — то же самое, альтернативная команда",
            ]
        )

    lines.extend(
        [
            "",
            "Основные действия также доступны через кнопки: покупка, профиль, промокод, поддержка и админка.",
        ]
    )

    await message.answer("\n".join(lines))

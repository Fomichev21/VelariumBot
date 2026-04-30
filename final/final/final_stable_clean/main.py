from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError

from config import settings
from database import init_db
from handlers import admin, help, user
from payments import process_expiry_reminders


def build_bot() -> Bot:
    if settings.telegram_proxy:
        session = AiohttpSession(proxy=settings.telegram_proxy)
        return Bot(token=settings.bot_token, session=session)
    return Bot(token=settings.bot_token)


async def main() -> None:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not set. Configure it in environment variables before starting the bot.")

    init_db()

    bot = build_bot()
    dispatcher = Dispatcher()
    dispatcher.include_router(admin.router)
    dispatcher.include_router(user.router)
    dispatcher.include_router(help.router)
    reminder_task = asyncio.create_task(reminder_loop(bot))

    try:
        await dispatcher.start_polling(bot)
    except TelegramNetworkError as exc:
        proxy_hint = (
            "\nSet TELEGRAM_PROXY, for example: socks5://127.0.0.1:1080"
            if not settings.telegram_proxy
            else f"\nCurrent TELEGRAM_PROXY: {settings.telegram_proxy}"
        )
        raise RuntimeError(
            "Bot cannot connect to api.telegram.org. "
            "This is a network problem on the machine, not a handler error."
            f"{proxy_hint}"
        ) from exc
    finally:
        reminder_task.cancel()
        await asyncio.gather(reminder_task, return_exceptions=True)
        await bot.session.close()


async def reminder_loop(bot: Bot) -> None:
    while True:
        try:
            await process_expiry_reminders(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())

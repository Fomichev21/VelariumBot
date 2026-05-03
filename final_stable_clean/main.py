from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError

from config import settings
from database import init_db
from handlers.admin import router as admin_router
from handlers.user import router as user_router
from handlers.help import router as help_router
from payments import process_expiry_reminders
from remnawave import get_missing_remnawave_settings


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)


def build_bot() -> Bot:
    session = AiohttpSession(
        proxy=settings.telegram_proxy if settings.telegram_proxy else None,
        timeout=30
    )

    return Bot(
        token=settings.bot_token,
        session=session
    )


async def reminder_loop(bot: Bot) -> None:
    while True:
        try:
            await process_expiry_reminders(bot)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.error(f"Reminder error: {e}")

        await asyncio.sleep(3600)


async def run_bot() -> None:
    bot = build_bot()

    dispatcher = Dispatcher()
    dispatcher.include_router(admin.router)
    dispatcher.include_router(user.router)
    dispatcher.include_router(help.router)

    reminder_task = asyncio.create_task(reminder_loop(bot))

    try:
        await dispatcher.start_polling(
            bot,
            polling_timeout=30
        )
    finally:
        reminder_task.cancel()
        await asyncio.gather(reminder_task, return_exceptions=True)
        await bot.session.close()


async def main() -> None:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not set")

    missing_remnawave = get_missing_remnawave_settings()
    if missing_remnawave:
        raise RuntimeError(
            f"Missing env vars: {', '.join(missing_remnawave)}"
        )

    init_db()

    while True:
        try:
            logging.info("Starting bot...")
            await run_bot()

        except TelegramNetworkError as e:
            logging.warning(f"Telegram connection lost: {e}")
            logging.info("Retrying in 10 seconds...")

        except Exception as e:
            logging.error(f"Unexpected error: {e}")
            logging.info("Restarting in 15 seconds...")

        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())

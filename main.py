import asyncio

from aiogram import Bot, Dispatcher

from config import BOT_TOKEN
from db.database import init_db
from handlers.verification import router
from utils.logger import logger


async def main() -> None:
    await init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Bot starting")
    try:
        await dp.start_polling(
            bot,
            allowed_updates=["chat_member", "message", "callback_query"],
        )
    finally:
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

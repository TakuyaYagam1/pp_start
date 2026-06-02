import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import BotCommand
from config import BOT_TOKEN
from db.database import init_db
from handlers.verification import router
from utils.logger import logger

PROXY_URL = "socks5://127.0.0.1:12334"


async def main() -> None:
    await init_db()
    session = AiohttpSession(proxy=PROXY_URL)
    bot = Bot(token=BOT_TOKEN, session=session)
    dp = Dispatcher()
    dp.include_router(router)
    await bot.set_my_commands([BotCommand(command="verify", description="Подтвердить, что я человек")])
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

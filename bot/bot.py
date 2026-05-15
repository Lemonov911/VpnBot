import asyncio
import logging

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN, API_PORT
from handlers import admin, start, vpn
from services.database import init_db
from services.scheduler import run_scheduler
from services.webapp_api import create_api_app


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(admin.router)
    dp.include_router(start.router)
    dp.include_router(vpn.router)

    await init_db()

    # Mini App API
    runner = web.AppRunner(create_api_app(bot))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", API_PORT).start()
    logging.info("Mini App API listening on :%d", API_PORT)

    asyncio.create_task(run_scheduler(bot))

    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())

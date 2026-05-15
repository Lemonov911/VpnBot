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

    # Cleanup: слоты застрявшие в 'activating' после непредвиденного рестарта.
    # Они блокируют юзера (нельзя ни добавить, ни отозвать). 5min cutoff =
    # любая реальная активация заканчивается быстрее.
    try:
        from services.database import cleanup_stuck_activating_slots
        stuck = await cleanup_stuck_activating_slots()
        if stuck:
            logging.info("cleanup: освободили %d застрявших activating-слотов", stuck)
    except Exception as e:
        logging.warning("cleanup activating-slots failed: %s", e)

    # Mini App API
    runner = web.AppRunner(create_api_app(bot))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", API_PORT).start()
    logging.info("Mini App API listening on :%d", API_PORT)

    asyncio.create_task(run_scheduler(bot), name="scheduler")

    # Прогреваем кеш eSIM пакетов в фоне (чтобы первый юзер не ждал 30с)
    from services.esim_api import warm_cache
    asyncio.create_task(warm_cache(), name="esim_warm_cache")

    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())

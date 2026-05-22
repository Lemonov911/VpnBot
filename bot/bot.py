import asyncio
import logging
import os
import subprocess
import sys

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import ADMIN_ID, BOT_TOKEN, API_PORT
from handlers import admin, start, vpn
from services.database import init_db
from services.scheduler import run_scheduler
from services.webapp_api import create_api_app


def _resolve_version() -> str:
    """Версия для логов + /api/health. Источники в порядке приоритета:
       1. BOT_VERSION env (CI устанавливает при deploy)
       2. git rev-parse HEAD (если репо доступен на проде)
       3. 'dev' fallback
    """
    v = os.getenv("BOT_VERSION", "").strip()
    if v:
        return v
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stderr=subprocess.DEVNULL, timeout=2,
        ).decode().strip()
    except Exception:
        return "dev"


BOT_VERSION = _resolve_version()


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.info("Bot starting: version=%s pid=%d", BOT_VERSION, os.getpid())

    # S9: Fail-fast on missing BOT_TOKEN; warn (don't crash) on missing ADMIN_ID.
    if not BOT_TOKEN:
        logging.error("FATAL: BOT_TOKEN env var missing or empty")
        sys.exit(2)
    if not ADMIN_ID:
        logging.warning("ADMIN_ID is 0 — admin alerts will be silently suppressed")

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(admin.router)
    dp.include_router(start.router)
    dp.include_router(vpn.router)

    await init_db()

    # S1: Validate bot can talk to Telegram BEFORE binding HTTP socket.
    # Otherwise nginx may proxy webapp requests that lazy-init the aiogram
    # session while delete_webhook hasn't cleared a stale webhook yet
    # (409 conflict on first getUpdates).
    try:
        me = await bot.get_me()
        logging.info("bot authenticated: @%s id=%d", me.username, me.id)
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logging.error("Telegram API unreachable on startup: %s", e)
        raise

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
    app = create_api_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", API_PORT)
    await site.start()
    logging.info("Mini App API listening on :%d", API_PORT)

    # Fire-and-forget tasks с done-callback: иначе исключение в таске
    # тихо съедается, и (для scheduler'а) retention/grace перестанут
    # работать без единой записи в логе.
    def _log_task_death(t: asyncio.Task):
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logging.error("background task '%s' died: %s",
                          t.get_name(), exc, exc_info=exc)

    sched_task = asyncio.create_task(run_scheduler(bot), name="scheduler")
    sched_task.add_done_callback(_log_task_death)

    # Прогреваем кеш eSIM пакетов в фоне (чтобы первый юзер не ждал 30с)
    from services.esim_api import warm_cache
    warm_task = asyncio.create_task(warm_cache(), name="esim_warm_cache")
    warm_task.add_done_callback(_log_task_death)

    try:
        await dp.start_polling(bot)
    finally:
        # Graceful shutdown: отменяем фоновые таски и закрываем aiohttp-сессию.
        # Без этого asyncio логирует "Unclosed connector" и планировщик не
        # даёт event-loop'у завершиться чисто при systemctl stop/restart.
        for task in (sched_task, warm_task):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        from services.vpnctl_client import close_shared_session
        await close_shared_session()

        # S6: close eSIM aiohttp session (avoid "Unclosed connector" noise).
        try:
            from services.esim_api import close_session as _esim_close
            await _esim_close()
        except Exception as e:
            logging.warning("esim session close: %s", e)

        # S5: aiohttp graceful drain — stop accepting new connections, run
        # on_shutdown signals, brief grace for in-flight handlers.
        try:
            await site.stop()
            await app.shutdown()
            await asyncio.sleep(1.0)
        except Exception as e:
            logging.warning("aiohttp graceful shutdown error: %s", e)

        # S3: checkpoint WAL → main DB so a kill -9 right after restart
        # won't leave fresh writes only in the .db-wal file.
        try:
            import aiosqlite
            from services.database import DB_PATH
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                await db.commit()
            logging.info("WAL checkpoint completed")
        except Exception as e:
            logging.warning("WAL checkpoint on shutdown failed: %s", e)

        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())

"""
Shared pytest fixtures for bot test suite.

IMPORTANT: this file MUST import-time-set env vars BEFORE bot modules are loaded,
because services/webapp_api.py grabs BOT_TOKEN / CRYPTOBOT_TOKEN from config.py
at import time and bakes them into module globals.
"""
import os
import sys
from pathlib import Path

# 1) Make `bot/` importable (so `import services.auth`, `import config` work).
_BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BOT_DIR))

# 2) Stub env vars before any bot module import.  Real bot/.env is not required.
os.environ.setdefault("BOT_TOKEN", "111111:TEST_TOKEN_FOR_PYTEST")
os.environ.setdefault("ADMIN_ID", "0")
os.environ.setdefault("WEBAPP_URL", "http://localhost:5173")
os.environ.setdefault("API_PORT", "8080")
os.environ.setdefault("DEBUG", "false")
os.environ.setdefault("CRYPTOBOT_TOKEN", "TEST_CRYPTOBOT_TOKEN")

import pytest
import pytest_asyncio


@pytest.fixture
def test_bot_token() -> str:
    return os.environ["BOT_TOKEN"]


@pytest.fixture
def test_cryptobot_token() -> str:
    return os.environ["CRYPTOBOT_TOKEN"]


@pytest_asyncio.fixture
async def fresh_db(tmp_path, monkeypatch):
    """
    Per-test fresh sqlite DB.  Monkeypatches services.database.DB_PATH and
    runs init_db() so the schema is in place.

    Yields the Path to the DB file.
    """
    # Import here so the monkeypatch happens AFTER the env stubs above.
    import services.database as db_mod

    db_file = tmp_path / "bot.db"
    monkeypatch.setattr(db_mod, "DB_PATH", db_file)

    # webapp_api.handle_user_stats imports DB_PATH separately — patch the bound
    # symbol there too if needed at call sites (we don't hit that path in tests).
    await db_mod.init_db()
    return db_file

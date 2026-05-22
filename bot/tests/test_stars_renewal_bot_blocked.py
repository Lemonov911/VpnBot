"""
Regression: Stars recurring renewal на bot-blocked юзера.

Сценарий: юзер с active recurring Stars-подпиской (auto_renew=1) заблокировал
бота → Telegram продолжает списывать ★ каждые 30 дней. Раньше бот делал
refund + admin alert, но НЕ снимал `auto_renew=1` → следующий месяц снова
charge → снова refund → loop.

Фикс A2 (commit 596e155) добавил `disable_auto_renew(sub_id)` после refund:
    - auto_renew=0 в БД
    - reminder/scheduler перестают за неё цепляться
    - если юзер разблокирует бота — мы не пойдём в этот branch повторно

Тесты проверяют что цикл закрывается даже когда refund сам fail'ит.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

import services.database as _db_mod
from services.database import (
    create_subscription,
    mark_user_bot_blocked,
    upsert_user,
)


USER_ID = 8001
PLAN_KEY = "vpn_base"


def _fake_plan() -> dict:
    """Минимальный VPN_PLANS-словарь для plan-аргумента _handle_stars_renewal."""
    return {
        "name": "База",
        "duration_days": 30,
        "stars": 250,
        "rub": "200",
        "awg_slots": 2, "vless_slots": 1, "wg_slots": 0,
    }


def _fake_message(user_id: int) -> MagicMock:
    """Mock aiogram Message с from_user.id — это всё что нужно _handle_stars_renewal."""
    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.bot = MagicMock()
    msg.bot.send_message = AsyncMock()
    msg.answer = AsyncMock()
    return msg


def _fake_payment(charge_id: str, amount: int = 250) -> MagicMock:
    """Mock SuccessfulPayment object — нужны telegram_payment_charge_id + total_amount."""
    p = MagicMock()
    p.telegram_payment_charge_id = charge_id
    p.total_amount = amount
    return p


async def _setup_user_with_recurring_sub() -> int:
    """Создаём user + active recurring sub (auto_renew=True), mark bot-blocked."""
    await upsert_user(USER_ID, "blocked_user", "Blocked User")
    sub_id = await create_subscription(
        user_id=USER_ID, plan=PLAN_KEY,
        payment_id=f"first_stars_charge_{USER_ID}",
        stars_paid=250,
        expires_at=datetime.utcnow() + timedelta(days=15),
        auto_renew=True,
        payment_provider="stars",
    )
    assert sub_id is not None
    await mark_user_bot_blocked(USER_ID)
    return sub_id


async def _get_auto_renew(sub_id: int) -> int:
    # NB: DB_PATH через _db_mod attribute — `from services.database import DB_PATH`
    # биндится при загрузке модуля, fresh_db monkeypatch не обновит локальное имя.
    async with aiosqlite.connect(_db_mod.DB_PATH) as db:
        async with db.execute(
            "SELECT auto_renew FROM subscriptions WHERE id=?", (sub_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else -1


# ── Кейс 1: happy path — refund + auto_renew=0 + admin alert ────────────────

@pytest.mark.asyncio
async def test_bot_blocked_user_disables_auto_renew_after_refund(
    fresh_db, monkeypatch,
):
    """bot-blocked юзер получает renewal-charge → бот делает refund,
    устанавливает auto_renew=0, шлёт admin alert."""
    sub_id = await _setup_user_with_recurring_sub()
    # Verify pre-state
    assert await _get_auto_renew(sub_id) == 1, "fixture: sub должна быть recurring"

    bot = MagicMock()
    bot.refund_star_payment = AsyncMock()
    bot.send_message = AsyncMock()

    # ADMIN_ID должен быть выставлен — иначе admin alert не уйдёт
    import config as cfg
    monkeypatch.setattr(cfg, "ADMIN_ID", 99999, raising=False)

    msg = _fake_message(USER_ID)
    payment = _fake_payment("renewal_charge_abc123", amount=250)

    from handlers.vpn import _handle_stars_renewal
    await _handle_stars_renewal(msg, bot, payment, _fake_plan(), PLAN_KEY)

    # Refund call с правильными args
    bot.refund_star_payment.assert_awaited_once_with(
        user_id=USER_ID, telegram_payment_charge_id="renewal_charge_abc123",
    )
    # auto_renew теперь 0 — loop не повторится
    assert await _get_auto_renew(sub_id) == 0, (
        "auto_renew должен быть 0 после refund на bot-blocked — иначе следующий "
        "месяц снова charge+refund loop"
    )
    # Admin alert ушёл (текст содержит маркер "SKIPPED")
    bot.send_message.assert_awaited_once()
    args = bot.send_message.await_args
    assert "SKIPPED" in args[0][1] and "blocked bot" in args[0][1]


# ── Кейс 2: refund fail — auto_renew всё равно дисэйблится ──────────────────

@pytest.mark.asyncio
async def test_disable_auto_renew_runs_even_if_refund_throws(
    fresh_db, monkeypatch,
):
    """Если Telegram отказывает в refund (network/CHARGE_ID_INVALID/etc.) —
    auto_renew ВСЁ РАВНО должен дисэйблиться. Иначе loop сохраняется."""
    sub_id = await _setup_user_with_recurring_sub()

    bot = MagicMock()
    bot.refund_star_payment = AsyncMock(side_effect=RuntimeError("TG API down"))
    bot.send_message = AsyncMock()

    import config as cfg
    monkeypatch.setattr(cfg, "ADMIN_ID", 99999, raising=False)

    msg = _fake_message(USER_ID)
    payment = _fake_payment("renewal_charge_xyz999", amount=250)

    from handlers.vpn import _handle_stars_renewal
    # Не должно бросить — exception от refund_star_payment ловится внутри
    await _handle_stars_renewal(msg, bot, payment, _fake_plan(), PLAN_KEY)

    # Refund попытался
    bot.refund_star_payment.assert_awaited_once()
    # Главная проверка: auto_renew всё равно 0 (защита от loop'а)
    assert await _get_auto_renew(sub_id) == 0, (
        "auto_renew должен быть 0 ДАЖЕ если refund упал — иначе бесконечный "
        "цикл charge+failed-refund+admin-alert каждый месяц"
    )

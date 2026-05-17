"""
Grace renewal helper — общий между Stars/CryptoBot/Cryptomus/Lava.

Ключевые гарантии:
- Если grace-sub того же плана найдена — продлеваем (status→active),
  возвращаем True (caller skip create).
- Не grace-sub / другой план / нет sub → возвращаем False (обычный create).
- Race: если scheduler уже перевёл sub в expired между check и renew —
  graceful fallback (False, caller создаст новую).
- Состояние sub после успешного renew: status='active', grace_until=NULL,
  expires_at = now + duration_days, reminded_*=0.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.database import (
    create_subscription,
    mark_subscription_grace,
    mark_subscription_expired,
    get_active_subscription,
    get_subscription_by_id,
)


USER_ID = 8001


async def _make_sub(plan: str = "vpn_base", days_until_expiry: float = 30,
                    status: str = "active") -> int:
    """Создаёт sub с заданными expires_at и status."""
    expires_at = datetime.utcnow() + timedelta(days=days_until_expiry)
    sub_id = await create_subscription(
        user_id=USER_ID, plan=plan,
        payment_id=f"chg_{plan}_{datetime.utcnow().timestamp()}",
        stars_paid=145, expires_at=expires_at,
    )
    if status == "grace":
        grace_until = (datetime.utcnow() + timedelta(days=10)).isoformat()
        await mark_subscription_grace(sub_id, grace_until)
    elif status == "expired":
        await mark_subscription_expired(sub_id)
    return sub_id


def _fake_plan(plan_key: str = "vpn_base", duration_days: int = 30) -> dict:
    """Минимальный VPN_PLANS-словарь для helper'а."""
    return {
        "name": "База",
        "duration_days": duration_days,
        "awg_slots": 2, "vless_slots": 1, "wg_slots": 0,
    }


def _fake_bot() -> MagicMock:
    """Bot mock с send_message AsyncMock'ом."""
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


# ── happy path ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_renews_when_user_in_grace_same_plan(fresh_db):
    from services.grace import try_renew_from_grace

    sub_id = await _make_sub(plan="vpn_base", status="grace")
    plan = _fake_plan("vpn_base", 30)
    bot = _fake_bot()

    # vpnctl_client.client_for_server мокаем — без агента в тесте.
    with patch("services.grace.client_for_server"):
        result = await try_renew_from_grace(
            bot, USER_ID, "vpn_base", plan, "stars_42",
            method="stars", stars=145,
        )

    assert result is True
    # Sub теперь active, не grace
    updated = await get_subscription_by_id(sub_id)
    assert updated["status"] == "active"
    assert updated["grace_until"] is None
    # User получил confirmation
    bot.send_message.assert_called_once()
    args, kwargs = bot.send_message.call_args
    assert args[0] == USER_ID
    assert "продлена" in args[1].lower()


@pytest.mark.asyncio
async def test_renew_extends_expires_at(fresh_db):
    from services.grace import try_renew_from_grace

    sub_id = await _make_sub(plan="vpn_base", status="grace")
    plan = _fake_plan("vpn_base", 30)

    with patch("services.grace.client_for_server"):
        await try_renew_from_grace(
            _fake_bot(), USER_ID, "vpn_base", plan, "p1", method="crypto",
        )

    updated = await get_subscription_by_id(sub_id)
    # Новый expires_at — примерно через 30 дней (с погрешностью ±1)
    exp = datetime.fromisoformat(updated["expires_at"])
    expected = datetime.utcnow() + timedelta(days=30)
    delta = abs((exp - expected).total_seconds())
    assert delta < 5  # допуск 5 сек


# ── negative cases ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_skips_when_no_existing_sub(fresh_db):
    """У юзера нет sub → helper возвращает False, caller создаёт обычную."""
    from services.grace import try_renew_from_grace

    result = await try_renew_from_grace(
        _fake_bot(), USER_ID, "vpn_base", _fake_plan(), "p1", method="stars",
    )
    assert result is False


@pytest.mark.asyncio
async def test_skips_when_sub_is_active_not_grace(fresh_db):
    """Active sub (не grace) — это upgrade/parallel-buy, не grace renewal."""
    from services.grace import try_renew_from_grace

    await _make_sub(plan="vpn_base", status="active")
    result = await try_renew_from_grace(
        _fake_bot(), USER_ID, "vpn_base", _fake_plan(), "p1", method="stars",
    )
    assert result is False


@pytest.mark.asyncio
async def test_skips_when_sub_is_expired(fresh_db):
    """Expired sub — слоты уже отозваны, не renewable. Создаём новую."""
    from services.grace import try_renew_from_grace

    await _make_sub(plan="vpn_base", status="expired")
    result = await try_renew_from_grace(
        _fake_bot(), USER_ID, "vpn_base", _fake_plan(), "p1", method="stars",
    )
    assert result is False


@pytest.mark.asyncio
async def test_skips_when_grace_but_different_plan(fresh_db):
    """Юзер в grace на vpn_base, платит за vpn_max — это upgrade,
    grace-renewal не применим (другой плагин, другие слоты).
    Caller создаст новую sub vpn_max, старая grace отдельно истечёт."""
    from services.grace import try_renew_from_grace

    await _make_sub(plan="vpn_base", status="grace")
    result = await try_renew_from_grace(
        _fake_bot(), USER_ID, "vpn_max", _fake_plan("vpn_max"), "p1",
        method="stars",
    )
    assert result is False


# ── race / robustness ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_crash_when_unthrottle_fails(fresh_db):
    """Agent down → unthrottle падает, но renew всё равно успешен.
    Юзер получил «продлено», scheduler следующим tick'ом досинхронит."""
    from services.grace import try_renew_from_grace

    sub_id = await _make_sub(plan="vpn_base", status="grace")

    # client_for_server бросает — имитируем agent down
    def boom(*a, **kw):
        raise RuntimeError("agent unreachable")

    with patch("services.grace.client_for_server", side_effect=boom):
        result = await try_renew_from_grace(
            _fake_bot(), USER_ID, "vpn_base", _fake_plan(), "p1", method="stars",
        )

    assert result is True  # renew всё равно успешен
    updated = await get_subscription_by_id(sub_id)
    assert updated["status"] == "active"


@pytest.mark.asyncio
async def test_no_crash_when_send_message_fails(fresh_db):
    """Telegram заблокировал бота юзером → send_message бросает.
    DB-state корректный, helper возвращает True."""
    from services.grace import try_renew_from_grace

    await _make_sub(plan="vpn_base", status="grace")
    bot = _fake_bot()
    bot.send_message.side_effect = RuntimeError("blocked by user")

    with patch("services.grace.client_for_server"):
        result = await try_renew_from_grace(
            bot, USER_ID, "vpn_base", _fake_plan(), "p1", method="stars",
        )

    assert result is True


# ── method propagated to record_payment ───────────────────────────────────────

@pytest.mark.asyncio
async def test_atomic_renew_loses_race_to_scheduler(fresh_db):
    """Race: scheduler перевёл sub в expired между check и renew →
    UPDATE WHERE status='grace' не trogger'ит rowcount, helper returns False,
    caller fallback'нется на create."""
    from services.database import renew_subscription_from_grace

    sub_id = await _make_sub(plan="vpn_base", status="grace")
    # Имитируем race: scheduler перевёл sub в expired
    await mark_subscription_expired(sub_id)

    result = await renew_subscription_from_grace(sub_id, days=30)
    assert result is None
    # Sub осталась expired (scheduler сценарий выиграл)
    updated = await get_subscription_by_id(sub_id)
    assert updated["status"] == "expired"


@pytest.mark.asyncio
async def test_records_payment_with_method(fresh_db):
    """Метод (stars/crypto/cryptomus/lavatop) пишется в payments-log
    для admin /payments и LTV-аналитики."""
    from services.grace import try_renew_from_grace
    from services.database import DB_PATH
    import aiosqlite

    await _make_sub(plan="vpn_base", status="grace")
    with patch("services.grace.client_for_server"):
        await try_renew_from_grace(
            _fake_bot(), USER_ID, "vpn_base", _fake_plan(),
            "lavatop_xyz", method="lavatop", amount_rub=200,
        )

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT method, tx_id FROM payments WHERE user_id=?", (USER_ID,)
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] == "lavatop"
    assert row[1] == "lavatop_xyz"

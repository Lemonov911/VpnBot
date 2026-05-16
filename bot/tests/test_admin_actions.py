"""
Admin write-ops DB helpers — extend_subscription, set_user_banned.

Pure DB-layer tests with the fresh_db fixture.
"""
from datetime import datetime, timedelta

import pytest

from services.database import (
    upsert_user,
    create_subscription,
    extend_subscription,
    mark_subscription_grace,
    set_user_banned,
    is_user_banned,
    get_subscription_by_id,
)


USER_ID = 9001


async def _make_sub(*, expires_in_days: int = 30, plan: str = "vpn_base") -> int:
    expires = datetime.utcnow() + timedelta(days=expires_in_days)
    return await create_subscription(
        user_id=USER_ID, plan=plan,
        payment_id=f"chg_{plan}_{datetime.utcnow().timestamp()}",
        stars_paid=145, expires_at=expires,
    )


# ── extend_subscription ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_extend_active_sub_adds_days(fresh_db):
    """Активная подписка + extend(7) → expires_at сдвигается на +7 дней."""
    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(expires_in_days=10)

    before = await get_subscription_by_id(sub_id)
    expires_before = datetime.fromisoformat(before["expires_at"].replace(" ", "T"))

    result = await extend_subscription(sub_id, days=7)
    assert result is not None
    assert result["id"] == sub_id

    after = await get_subscription_by_id(sub_id)
    expires_after = datetime.fromisoformat(after["expires_at"].replace(" ", "T"))
    delta = (expires_after - expires_before).total_seconds() / 86400
    assert 6.9 < delta < 7.1


@pytest.mark.asyncio
async def test_extend_expired_sub_starts_from_now(fresh_db):
    """Если expires_at в прошлом — extend считает от now, иначе подарок
    «продление на 30д» уже-истёкшей подписки даст expires_at тоже в прошлом."""
    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(expires_in_days=-5)  # истекла 5 дней назад

    await extend_subscription(sub_id, days=30)

    after = await get_subscription_by_id(sub_id)
    expires = datetime.fromisoformat(after["expires_at"].replace(" ", "T"))
    days_ahead = (expires - datetime.utcnow()).total_seconds() / 86400
    assert 29 < days_ahead < 31, f"expected ~30 days ahead, got {days_ahead}"


@pytest.mark.asyncio
async def test_extend_grace_sub_brings_back_to_active(fresh_db):
    """Sub в grace + extend → status='active', grace_until=NULL."""
    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(expires_in_days=-1)
    await mark_subscription_grace(sub_id, (datetime.utcnow() + timedelta(days=10)).isoformat())

    await extend_subscription(sub_id, days=14)

    after = await get_subscription_by_id(sub_id)
    assert after["status"] == "active"
    assert after["grace_until"] is None


@pytest.mark.asyncio
async def test_extend_missing_sub_returns_none(fresh_db):
    """extend несуществующей подписки → None, без exception."""
    result = await extend_subscription(99999, days=7)
    assert result is None


# ── set_user_banned / is_user_banned ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_user_banned_flips_flag(fresh_db):
    await upsert_user(USER_ID, "u", "U")
    assert await is_user_banned(USER_ID) is False

    ok = await set_user_banned(USER_ID, banned=True, reason="abuse")
    assert ok is True
    assert await is_user_banned(USER_ID) is True


@pytest.mark.asyncio
async def test_unban_clears_flag_and_reason(fresh_db):
    """unban → is_banned=0, banned_reason очищается."""
    import aiosqlite
    await upsert_user(USER_ID, "u", "U")
    await set_user_banned(USER_ID, banned=True, reason="abuse")
    await set_user_banned(USER_ID, banned=False)

    assert await is_user_banned(USER_ID) is False
    async with aiosqlite.connect(fresh_db) as db:
        async with db.execute(
            "SELECT banned_reason FROM users WHERE id=?", (USER_ID,),
        ) as cur:
            row = await cur.fetchone()
    assert row[0] is None, "banned_reason должен быть NULL после unban"


@pytest.mark.asyncio
async def test_ban_unknown_user_returns_false(fresh_db):
    """Ban несуществующего юзера → False (rowcount=0)."""
    result = await set_user_banned(99999, banned=True)
    assert result is False


@pytest.mark.asyncio
async def test_is_user_banned_returns_false_for_unknown_user(fresh_db):
    """Гейт is_user_banned не должен бросать на неизвестных юзерах — это
    safe-default «не забанен», иначе любая ошибка с user_id ломает /start."""
    assert await is_user_banned(99999) is False

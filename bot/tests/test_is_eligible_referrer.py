"""
Regression: is_eligible_referrer (services/database.py).

Bot-blocked юзеры не должны квалифицироваться как реферрер — иначе бонус
накапливается в ref_bonus_days, но TG-уведомление о +7 днях упирается в
TelegramForbiddenError → юзер не узнаёт что у него есть бонус, bank растёт
мёртвым весом.

4 проверки:
  1. paid user, no block → eligible
  2. paid user, bot_blocked_at SET → NOT eligible (моё A5 fix)
  3. trial-only user → NOT eligible (плановое поведение, не моё)
  4. banned user (is_banned=1) → NOT eligible (плановое поведение)
"""
from datetime import datetime, timedelta

import pytest

import services.database as _db_mod
from services.database import (
    create_subscription,
    is_eligible_referrer,
    mark_user_bot_blocked,
    upsert_user,
)


async def _paid_sub(user_id: int, plan: str = "vpn_max"):
    sub_id = await create_subscription(
        user_id=user_id, plan=plan,
        payment_id=f"chg_{user_id}_{plan}_{datetime.utcnow().timestamp()}",
        stars_paid=450,
        expires_at=datetime.utcnow() + timedelta(days=15),
    )
    assert sub_id is not None
    return sub_id


@pytest.mark.asyncio
async def test_paid_user_no_block_is_eligible(fresh_db):
    await upsert_user(701, "good_ref", "Good Ref")
    await _paid_sub(701)
    assert await is_eligible_referrer(701) is True


@pytest.mark.asyncio
async def test_paid_user_with_bot_blocked_is_NOT_eligible(fresh_db):
    """Моя A5 проверка — bot_blocked_at filter."""
    await upsert_user(702, "dead_ref", "Dead Ref")
    await _paid_sub(702)
    assert await is_eligible_referrer(702) is True  # baseline OK
    await mark_user_bot_blocked(702)
    assert await is_eligible_referrer(702) is False, (
        "bot_blocked юзер НЕ должен быть eligible — иначе bank растёт без "
        "возможности уведомить юзера"
    )


@pytest.mark.asyncio
async def test_trial_only_user_NOT_eligible(fresh_db):
    """Trial — не paid sub, не квалифицируется."""
    await upsert_user(703, "trial_ref", "Trial Ref")
    await create_subscription(
        user_id=703, plan="vpn_trial",
        payment_id="trial_703",
        stars_paid=0,
        expires_at=datetime.utcnow() + timedelta(days=2),
    )
    assert await is_eligible_referrer(703) is False


@pytest.mark.asyncio
async def test_banned_user_NOT_eligible(fresh_db):
    await upsert_user(704, "banned_ref", "Banned Ref")
    await _paid_sub(704)
    # Ban через прямой UPDATE — admin-ban endpoint имеет много side-effects.
    import aiosqlite
    async with aiosqlite.connect(_db_mod.DB_PATH) as db:
        await db.execute("UPDATE users SET is_banned=1 WHERE id=?", (704,))
        await db.commit()
    assert await is_eligible_referrer(704) is False


@pytest.mark.asyncio
async def test_nonexistent_user_NOT_eligible(fresh_db):
    """Защита от orphan ref_999999999 в /start handler."""
    assert await is_eligible_referrer(99999999) is False

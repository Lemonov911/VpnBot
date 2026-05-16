"""
Referral bonus state machine — pure DB-layer tests.

Covers:
  set_referred_by()           — link user → referrer, self-referral guard
  try_award_referral_bonus()  — gates: paid plan only, first paid sub only,
                                no double-award
  rollback_referral_bonus()   — refund undoes bonus atomically
  get_referral_stats()        — invited/converted counts
"""
from datetime import datetime, timedelta

import pytest

from services.database import (
    upsert_user,
    set_referred_by,
    try_award_referral_bonus,
    rollback_referral_bonus,
    get_referral_stats,
    create_subscription,
    mark_subscription_refunded,
    get_subscription_by_id,
)


# ── helpers ───────────────────────────────────────────────────────────────────

REFERRER_ID = 1000
NEWBIE_ID   = 2000

FUTURE = datetime.utcnow() + timedelta(days=30)


async def _add_user(uid: int):
    await upsert_user(uid, username=f"u{uid}", first_name=f"User{uid}")


async def _make_paid_sub(user_id: int, plan: str = "vpn_base",
                          payment_id: str | None = None) -> int:
    return await create_subscription(
        user_id=user_id, plan=plan,
        payment_id=payment_id or f"chg_{user_id}_{plan}_{datetime.utcnow().timestamp()}",
        stars_paid=200, expires_at=FUTURE,
    )


async def _make_trial(user_id: int) -> int:
    return await create_subscription(
        user_id=user_id, plan="vpn_trial",
        payment_id=f"trial_{user_id}",
        stars_paid=0, expires_at=datetime.utcnow() + timedelta(days=3),
    )


# ── set_referred_by: linking ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_referrer_link_stored(fresh_db):
    """Базовый кейс: /start ref_<id> → запись в users.referred_by."""
    await _add_user(REFERRER_ID)
    await _add_user(NEWBIE_ID)
    await set_referred_by(NEWBIE_ID, REFERRER_ID)

    stats = await get_referral_stats(REFERRER_ID)
    assert stats["invited"] == 1
    assert stats["converted"] == 0  # ещё не купил


@pytest.mark.asyncio
async def test_self_referral_silently_rejected(fresh_db):
    """Юзер кидает свою же реф-ссылку — игнорируется (no-op, no exception)."""
    await _add_user(REFERRER_ID)
    await set_referred_by(REFERRER_ID, REFERRER_ID)

    stats = await get_referral_stats(REFERRER_ID)
    assert stats["invited"] == 0


@pytest.mark.asyncio
async def test_referrer_link_immutable_once_set(fresh_db):
    """Если referred_by уже выставлен, повторный set_referred_by не перезапишет
    (защита от angry-userом attribution-стилинга)."""
    other_referrer = 3000
    await _add_user(REFERRER_ID)
    await _add_user(other_referrer)
    await _add_user(NEWBIE_ID)

    await set_referred_by(NEWBIE_ID, REFERRER_ID)
    await set_referred_by(NEWBIE_ID, other_referrer)  # попытка перезаписать

    # invited у первого — 1, у второго — 0
    assert (await get_referral_stats(REFERRER_ID))["invited"] == 1
    assert (await get_referral_stats(other_referrer))["invited"] == 0


# ── try_award_referral_bonus: gating ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_bonus_awarded_on_first_paid_subscription(fresh_db):
    """Базовый flow: реферал → первая платная подписка → реферер получает +7 дней.
    У реферера тоже есть активная подписка → её expires_at сдвигается на +7 дней."""
    await _add_user(REFERRER_ID)
    await _add_user(NEWBIE_ID)
    await set_referred_by(NEWBIE_ID, REFERRER_ID)

    # У реферера есть активная подписка (иначе сдвигать нечего)
    ref_sub_id = await _make_paid_sub(REFERRER_ID, payment_id="ref_own_sub")
    ref_sub_before = await get_subscription_by_id(ref_sub_id)
    expires_before = datetime.fromisoformat(ref_sub_before["expires_at"])

    # Newbie покупает первую платную
    newbie_sub_id = await _make_paid_sub(NEWBIE_ID)
    result = await try_award_referral_bonus(NEWBIE_ID, days=7, paid_sub_id=newbie_sub_id)

    assert result == REFERRER_ID

    # Реферер получил +7 дней к ref_bonus_days
    stats = await get_referral_stats(REFERRER_ID)
    assert stats["bonus_days"] == 7
    assert stats["converted"] == 1

    # И его активная подписка сдвинута на +7 дней. SQLite datetime(..., '+7 days')
    # обрезает миллисекунды, поэтому сравниваем по total_seconds с допуском.
    ref_sub_after = await get_subscription_by_id(ref_sub_id)
    expires_after = datetime.fromisoformat(ref_sub_after["expires_at"])
    delta_days = (expires_after - expires_before).total_seconds() / 86400
    assert 6.9 < delta_days < 7.1, f"expected ~7 days, got {delta_days}"

    # Tracking-поля проставлены на платной подписке newbie (для rollback).
    # get_subscription_by_id не возвращает их — читаем напрямую.
    import aiosqlite
    async with aiosqlite.connect(fresh_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT ref_bonus_awarded_to, ref_bonus_days_awarded "
            "FROM subscriptions WHERE id=?", (newbie_sub_id,),
        ) as cur:
            tracking = dict(await cur.fetchone())
    assert tracking["ref_bonus_awarded_to"] == REFERRER_ID
    assert tracking["ref_bonus_days_awarded"] == 7


@pytest.mark.asyncio
async def test_no_bonus_for_trial_purchase(fresh_db):
    """Trial (vpn_trial, 0 ⭐) НЕ считается «первой покупкой» — бонус не идёт.
    Иначе любой реферал-фермер триггерит бонус нулевыми триалами."""
    await _add_user(REFERRER_ID)
    await _add_user(NEWBIE_ID)
    await set_referred_by(NEWBIE_ID, REFERRER_ID)

    trial_id = await _make_trial(NEWBIE_ID)
    result = await try_award_referral_bonus(NEWBIE_ID, days=7, paid_sub_id=trial_id)
    assert result is None
    assert (await get_referral_stats(REFERRER_ID))["bonus_days"] == 0


@pytest.mark.asyncio
async def test_no_bonus_when_no_referrer(fresh_db):
    """Юзер без referred_by → try_award_referral_bonus = no-op."""
    await _add_user(NEWBIE_ID)
    sub_id = await _make_paid_sub(NEWBIE_ID)
    result = await try_award_referral_bonus(NEWBIE_ID, days=7, paid_sub_id=sub_id)
    assert result is None


@pytest.mark.asyncio
async def test_no_double_award_on_second_paid_purchase(fresh_db):
    """Второй раз бонус НЕ начисляется — иначе юзер мог бы продлять реферу годами."""
    await _add_user(REFERRER_ID)
    await _add_user(NEWBIE_ID)
    await set_referred_by(NEWBIE_ID, REFERRER_ID)

    sub1 = await _make_paid_sub(NEWBIE_ID, payment_id="chg_1")
    r1 = await try_award_referral_bonus(NEWBIE_ID, days=7, paid_sub_id=sub1)
    assert r1 == REFERRER_ID

    sub2 = await _make_paid_sub(NEWBIE_ID, payment_id="chg_2")
    r2 = await try_award_referral_bonus(NEWBIE_ID, days=7, paid_sub_id=sub2)
    assert r2 is None  # уже получал — не вторично

    # Реферер всё ещё имеет только 7 дней, не 14
    assert (await get_referral_stats(REFERRER_ID))["bonus_days"] == 7


# ── rollback_referral_bonus: refund flow ─────────────────────────────────────

@pytest.mark.asyncio
async def test_refund_rolls_back_referral_bonus(fresh_db):
    """User купил → бонус начислен → user сделал refund → бонус откатан."""
    await _add_user(REFERRER_ID)
    await _add_user(NEWBIE_ID)
    await set_referred_by(NEWBIE_ID, REFERRER_ID)

    ref_sub_id = await _make_paid_sub(REFERRER_ID, payment_id="ref_own")
    newbie_sub_id = await _make_paid_sub(NEWBIE_ID, payment_id="newbie_pay")
    await try_award_referral_bonus(NEWBIE_ID, days=7, paid_sub_id=newbie_sub_id)

    bonus_before = (await get_referral_stats(REFERRER_ID))["bonus_days"]
    assert bonus_before == 7

    # Refund подписки newbie + откат бонуса
    await mark_subscription_refunded(newbie_sub_id)
    result = await rollback_referral_bonus(newbie_sub_id)
    assert result is not None
    assert result == (REFERRER_ID, 7)

    bonus_after = (await get_referral_stats(REFERRER_ID))["bonus_days"]
    assert bonus_after == 0


@pytest.mark.asyncio
async def test_rollback_is_idempotent_under_race(fresh_db):
    """Параллельный второй вызов rollback_referral_bonus → возвращает None,
    бонус НЕ списывается дважды (claim-first pattern)."""
    await _add_user(REFERRER_ID)
    await _add_user(NEWBIE_ID)
    await set_referred_by(NEWBIE_ID, REFERRER_ID)

    await _make_paid_sub(REFERRER_ID, payment_id="ref_own")
    newbie_sub_id = await _make_paid_sub(NEWBIE_ID, payment_id="newbie_pay")
    await try_award_referral_bonus(NEWBIE_ID, days=7, paid_sub_id=newbie_sub_id)
    await mark_subscription_refunded(newbie_sub_id)

    r1 = await rollback_referral_bonus(newbie_sub_id)
    r2 = await rollback_referral_bonus(newbie_sub_id)

    assert r1 == (REFERRER_ID, 7)
    assert r2 is None  # уже откатан, второй вызов — no-op
    assert (await get_referral_stats(REFERRER_ID))["bonus_days"] == 0


@pytest.mark.asyncio
async def test_rollback_no_op_if_bonus_never_awarded(fresh_db):
    """Sub без ref_bonus_awarded_to (например купил без реферера) → rollback вернёт None."""
    await _add_user(NEWBIE_ID)
    sub_id = await _make_paid_sub(NEWBIE_ID)
    result = await rollback_referral_bonus(sub_id)
    assert result is None


# ── refund-then-rebuy: bonus re-awarded on legitimate second purchase ─────────

@pytest.mark.asyncio
async def test_bonus_reawarded_after_refund_and_rebuy(fresh_db):
    """User купил → refund → купил снова. Бонус должен начислиться повторно,
    т.к. первая покупка не считается (refunded_at IS NOT NULL).
    Это важно: без этого юзер не может «вернуться» к рефереру после refund."""
    await _add_user(REFERRER_ID)
    await _add_user(NEWBIE_ID)
    await set_referred_by(NEWBIE_ID, REFERRER_ID)
    await _make_paid_sub(REFERRER_ID, payment_id="ref_own")

    # Первая покупка → бонус → refund → откат
    sub1 = await _make_paid_sub(NEWBIE_ID, payment_id="chg_1")
    await try_award_referral_bonus(NEWBIE_ID, days=7, paid_sub_id=sub1)
    await mark_subscription_refunded(sub1)
    await rollback_referral_bonus(sub1)
    assert (await get_referral_stats(REFERRER_ID))["bonus_days"] == 0

    # Вторая покупка → бонус снова начисляется
    sub2 = await _make_paid_sub(NEWBIE_ID, payment_id="chg_2")
    result = await try_award_referral_bonus(NEWBIE_ID, days=7, paid_sub_id=sub2)
    assert result == REFERRER_ID
    assert (await get_referral_stats(REFERRER_ID))["bonus_days"] == 7

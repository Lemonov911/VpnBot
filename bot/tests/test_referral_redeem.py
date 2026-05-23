"""
Tests for referral redeem reactivate flow.

Covers three new pieces landed in commits 899f8b0 / bf441d6 / 0dd7219:

  1. services.database.redeem_referral_bonus(user_id) — 9 branches:
       - bank=0 → None
       - extend active paid sub
       - extend grace sub (status flip + grace_until cleared)
       - reactivate latest expired paid sub (status flip + cleared flags)
       - skip refunded-only history (no_eligible_sub)
       - skip trial-only history (no_eligible_sub)
       - skip when no paid sub in history (only trial)
       - extend preferred over reactivate when active exists
       - reactivate picks LATEST expired (ORDER BY id DESC)

  2. services.database.delete_empty_configs_for_sub(sub_id) — 3 cases:
       - mixed empty/active → only empty deleted
       - returns rowcount
       - no match returns 0

  3. handlers.vpn.send_purchase_success_message — 2 cases:
       - default title (no title_key) → "VPN ... активирован"
       - custom title_key='bot_referral_reactivate_title' → "Бонусные дни активированы"

Notes on test setup:
  - users.ref_bonus_days has no public setter (set internally by
    try_award_referral_bonus). To seed it directly we UPDATE the users row.
  - subscriptions.status='expired' is set via mark_subscription_expired().
  - subscriptions.status='grace' via mark_subscription_grace(); it requires
    expires_at to be already in the past, so we pre-set that.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from services.database import (
    upsert_user,
    create_subscription,
    mark_subscription_expired,
    mark_subscription_grace,
    mark_subscription_refunded,
    create_config_record,
    delete_empty_configs_for_sub,
    redeem_referral_bonus,
    get_subscription_by_id,
)


USER_ID = 5000

FUTURE = datetime.utcnow() + timedelta(days=30)
PAST = datetime.utcnow() - timedelta(days=5)


# ── helpers ───────────────────────────────────────────────────────────────────

async def _add_user(uid: int = USER_ID):
    await upsert_user(uid, username=f"u{uid}", first_name=f"User{uid}")


async def _set_bonus_bank(db_path, uid: int, days: int):
    """Direct UPDATE — no public helper for arbitrary bank seeding.
    award_referral_bonus_days requires a paid_sub_id + referrer linkage; for
    isolated redeem tests it's cleaner to write the column directly."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE users SET ref_bonus_days=? WHERE id=?", (days, uid),
        )
        await db.commit()


async def _get_bonus_bank(db_path, uid: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT ref_bonus_days FROM users WHERE id=?", (uid,)
        ) as cur:
            row = await cur.fetchone()
            return (row[0] if row else 0) or 0


async def _make_paid_sub(uid: int, plan: str = "vpn_base",
                          expires_at=None, payment_id=None) -> int:
    return await create_subscription(
        user_id=uid, plan=plan,
        payment_id=payment_id or f"chg_{uid}_{plan}_{datetime.utcnow().timestamp()}",
        stars_paid=200, expires_at=expires_at or FUTURE,
    )


async def _make_trial(uid: int, expires_at=None) -> int:
    return await create_subscription(
        user_id=uid, plan="vpn_trial",
        payment_id=f"trial_{uid}_{datetime.utcnow().timestamp()}",
        stars_paid=0, expires_at=expires_at or (datetime.utcnow() + timedelta(days=3)),
    )


async def _force_expires_at(db_path, sub_id: int, expires_at_iso: str):
    """Override expires_at directly so we can place a sub 'in the past' for
    grace transition tests."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE subscriptions SET expires_at=? WHERE id=?",
            (expires_at_iso, sub_id),
        )
        await db.commit()


# ── redeem_referral_bonus ────────────────────────────────────────────────────

async def test_redeem_bonus_zero_bank_returns_none(fresh_db):
    """bank=0 → return None, no DB mutation."""
    await _add_user()
    await _make_paid_sub(USER_ID)

    result = await redeem_referral_bonus(USER_ID)

    assert result is None
    assert await _get_bonus_bank(fresh_db, USER_ID) == 0


async def test_redeem_bonus_extends_active_paid_sub(fresh_db):
    """Active vpn_max sub + bank=7 → expires_at += 7d, bank=0."""
    await _add_user()
    sub_id = await _make_paid_sub(USER_ID, plan="vpn_max")
    sub_before = await get_subscription_by_id(sub_id)
    expires_before = datetime.fromisoformat(sub_before["expires_at"])

    await _set_bonus_bank(fresh_db, USER_ID, 7)

    result = await redeem_referral_bonus(USER_ID)

    assert result is not None
    assert result["action"] == "extended"
    assert result["days"] == 7
    assert result["sub_id"] == sub_id
    assert result["plan"] == "vpn_max"

    sub_after = await get_subscription_by_id(sub_id)
    expires_after = datetime.fromisoformat(sub_after["expires_at"])
    delta_days = (expires_after - expires_before).total_seconds() / 86400
    assert abs(delta_days - 7) < 0.5, f"expected +7d, got {delta_days}"

    assert await _get_bonus_bank(fresh_db, USER_ID) == 0


async def test_redeem_bonus_extends_grace_sub(fresh_db):
    """Grace vpn_base sub + bank=5 → status='active', grace_until=NULL,
    expires_at += 5d."""
    await _add_user()
    sub_id = await _make_paid_sub(USER_ID, plan="vpn_base")
    # Put expires_at in the past so mark_subscription_grace's guard passes.
    await _force_expires_at(fresh_db, sub_id, PAST.isoformat())

    grace_until_iso = (datetime.utcnow() + timedelta(days=14)).isoformat()
    flipped = await mark_subscription_grace(sub_id, grace_until_iso)
    assert flipped, "precondition: grace flip must succeed"

    await _set_bonus_bank(fresh_db, USER_ID, 5)

    result = await redeem_referral_bonus(USER_ID)
    assert result is not None
    assert result["action"] == "extended"
    assert result["days"] == 5

    # Status flipped grace→active, grace_until cleared.
    async with aiosqlite.connect(fresh_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT status, grace_until, expires_at FROM subscriptions WHERE id=?",
            (sub_id,),
        ) as cur:
            row = dict(await cur.fetchone())
    assert row["status"] == "active"
    assert row["grace_until"] is None
    # expires_at advanced from PAST by +5 days → still close to PAST+5d.
    new_exp = datetime.fromisoformat(row["expires_at"].replace(" ", "T"))
    # Since old expires_at was in the past, redeem uses datetime('now', +5 days).
    # So new_exp should be approximately now+5d.
    expected = datetime.utcnow() + timedelta(days=5)
    assert abs((new_exp - expected).total_seconds()) < 120

    assert await _get_bonus_bank(fresh_db, USER_ID) == 0


async def test_redeem_bonus_reactivates_expired_paid_sub(fresh_db):
    """No active/grace, only expired vpn_max → reactivate:
    status='active', expires_at=now+14d, bank=0, pending_plan=NULL,
    grace_until=NULL, all reminded_* flags = 0."""
    await _add_user()
    sub_id = await _make_paid_sub(USER_ID, plan="vpn_max")
    await mark_subscription_expired(sub_id)

    await _set_bonus_bank(fresh_db, USER_ID, 14)

    result = await redeem_referral_bonus(USER_ID)
    assert result is not None
    assert result["action"] == "reactivated"
    assert result["days"] == 14
    assert result["sub_id"] == sub_id
    assert result["plan"] == "vpn_max"

    async with aiosqlite.connect(fresh_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT status, expires_at, grace_until, pending_plan,
                      reminded_3d, reminded_1d, reminded_renewal_3d,
                      reminded_grace_3d, reminded_quota_throttled
               FROM subscriptions WHERE id=?""",
            (sub_id,),
        ) as cur:
            row = dict(await cur.fetchone())
    assert row["status"] == "active"
    assert row["grace_until"] is None
    assert row["pending_plan"] is None
    assert row["reminded_3d"] == 0
    assert row["reminded_1d"] == 0
    assert row["reminded_renewal_3d"] == 0
    assert row["reminded_grace_3d"] == 0
    assert row["reminded_quota_throttled"] == 0

    new_exp = datetime.fromisoformat(row["expires_at"].replace(" ", "T"))
    expected = datetime.utcnow() + timedelta(days=14)
    assert abs((new_exp - expected).total_seconds()) < 120

    assert await _get_bonus_bank(fresh_db, USER_ID) == 0


async def test_redeem_bonus_skips_refunded_for_reactivate(fresh_db):
    """bank>0, only refunded paid sub in history → no_eligible_sub,
    bank stays put (CAS does not fire)."""
    await _add_user()
    sub_id = await _make_paid_sub(USER_ID, plan="vpn_max")
    await mark_subscription_expired(sub_id)
    await mark_subscription_refunded(sub_id)

    await _set_bonus_bank(fresh_db, USER_ID, 7)

    result = await redeem_referral_bonus(USER_ID)
    assert result == {"action": "no_eligible_sub"}
    # bank NOT zeroed: CAS only fires after we have a target sub.
    assert await _get_bonus_bank(fresh_db, USER_ID) == 7


async def test_redeem_bonus_skips_trial_for_reactivate(fresh_db):
    """bank>0, only expired trial in history → no_eligible_sub, bank stays."""
    await _add_user()
    trial_id = await _make_trial(USER_ID)
    await mark_subscription_expired(trial_id)

    await _set_bonus_bank(fresh_db, USER_ID, 7)

    result = await redeem_referral_bonus(USER_ID)
    assert result == {"action": "no_eligible_sub"}
    assert await _get_bonus_bank(fresh_db, USER_ID) == 7


async def test_redeem_bonus_only_trial_no_paid_history(fresh_db):
    """bank>0, no paid sub at all (active trial only) → no_eligible_sub,
    bank stays. This is the 'user copies bonus before ever paying' branch."""
    await _add_user()
    await _make_trial(USER_ID)  # active trial → excluded by plan!='vpn_trial'

    await _set_bonus_bank(fresh_db, USER_ID, 7)

    result = await redeem_referral_bonus(USER_ID)
    assert result == {"action": "no_eligible_sub"}
    assert await _get_bonus_bank(fresh_db, USER_ID) == 7


async def test_redeem_bonus_extend_preferred_over_reactivate(fresh_db):
    """If there's BOTH an active sub and an expired sub, redeem must pick
    the active one (extend), not reactivate the expired."""
    await _add_user()
    # Old expired vpn_max
    expired_sub = await _make_paid_sub(
        USER_ID, plan="vpn_max", payment_id="exp_max",
    )
    await mark_subscription_expired(expired_sub)
    # New active vpn_base
    active_sub = await _make_paid_sub(
        USER_ID, plan="vpn_base", payment_id="act_base",
    )
    active_before = await get_subscription_by_id(active_sub)
    expired_before = await get_subscription_by_id(expired_sub)

    await _set_bonus_bank(fresh_db, USER_ID, 10)

    result = await redeem_referral_bonus(USER_ID)
    assert result is not None
    assert result["action"] == "extended"
    assert result["sub_id"] == active_sub
    assert result["plan"] == "vpn_base"

    # Active sub got extended
    active_after = await get_subscription_by_id(active_sub)
    active_delta = (
        datetime.fromisoformat(active_after["expires_at"])
        - datetime.fromisoformat(active_before["expires_at"])
    ).total_seconds() / 86400
    assert abs(active_delta - 10) < 0.5

    # Expired sub untouched
    expired_after = await get_subscription_by_id(expired_sub)
    assert expired_after["status"] == "expired"
    assert expired_after["expires_at"] == expired_before["expires_at"]


async def test_redeem_bonus_picks_latest_expired_for_reactivate(fresh_db):
    """Two expired paid subs (vpn_base older, vpn_max newer). Reactivate
    must pick LATEST = vpn_max (ORDER BY id DESC)."""
    await _add_user()
    older = await _make_paid_sub(USER_ID, plan="vpn_base", payment_id="old_base")
    await mark_subscription_expired(older)
    newer = await _make_paid_sub(USER_ID, plan="vpn_max", payment_id="new_max")
    await mark_subscription_expired(newer)

    await _set_bonus_bank(fresh_db, USER_ID, 12)

    result = await redeem_referral_bonus(USER_ID)
    assert result is not None
    assert result["action"] == "reactivated"
    assert result["sub_id"] == newer
    assert result["plan"] == "vpn_max"

    older_after = await get_subscription_by_id(older)
    assert older_after["status"] == "expired", \
        "older sub must remain expired (reactivate touched only newer)"


# ── delete_empty_configs_for_sub ─────────────────────────────────────────────

async def test_delete_empty_only(fresh_db):
    """Sub with 3 empty + 1 active config → delete_empty_configs_for_sub
    removes only the 3 empty rows, active stays."""
    await _add_user()
    sub_id = await _make_paid_sub(USER_ID)
    # 3 empty slots
    for _ in range(3):
        await create_config_record(sub_id, USER_ID, protocol="awg")
    # 1 active slot — promote one via direct UPDATE (no public helper that
    # bypasses the activating→active CAS).
    active_id = await create_config_record(sub_id, USER_ID, protocol="vless")
    async with aiosqlite.connect(fresh_db) as db:
        await db.execute(
            "UPDATE configs SET status='active', peer_name='peer1', "
            "config_data='vless://x' WHERE id=?",
            (active_id,),
        )
        await db.commit()

    deleted = await delete_empty_configs_for_sub(sub_id)
    assert deleted == 3

    async with aiosqlite.connect(fresh_db) as db:
        async with db.execute(
            "SELECT COUNT(*), MIN(status) FROM configs WHERE subscription_id=?",
            (sub_id,),
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == 1
    assert row[1] == "active"


async def test_delete_returns_count(fresh_db):
    """rowcount returned matches number of empty rows present."""
    await _add_user()
    sub_id = await _make_paid_sub(USER_ID)
    for _ in range(5):
        await create_config_record(sub_id, USER_ID, protocol="awg")

    deleted = await delete_empty_configs_for_sub(sub_id)
    assert deleted == 5


async def test_delete_no_match(fresh_db):
    """All configs are active → returns 0, none removed."""
    await _add_user()
    sub_id = await _make_paid_sub(USER_ID)
    ids = [
        await create_config_record(sub_id, USER_ID, protocol="awg"),
        await create_config_record(sub_id, USER_ID, protocol="vless"),
    ]
    async with aiosqlite.connect(fresh_db) as db:
        for cid in ids:
            await db.execute(
                "UPDATE configs SET status='active', peer_name='p', "
                "config_data='d' WHERE id=?",
                (cid,),
            )
        await db.commit()

    deleted = await delete_empty_configs_for_sub(sub_id)
    assert deleted == 0

    async with aiosqlite.connect(fresh_db) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM configs WHERE subscription_id=?", (sub_id,)
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == 2  # both still present


# ── send_purchase_success_message — title_key ────────────────────────────────

async def test_send_purchase_success_default_title(fresh_db, monkeypatch):
    """No title_key → uses bot_purchase_success_title.
    For lang=ru this renders 'VPN <name> активирован!'."""
    from handlers import vpn as vpn_mod
    from services import database as db_mod
    from services.plans import VPN_PLANS

    await _add_user()
    sub_id = await _make_paid_sub(USER_ID, plan="vpn_base")

    # Mock heavy bits: configs and sub_token. We want a plain "no AWG, no
    # VLESS" path so only one bot.send_message call is made and we can
    # assert on its body.
    monkeypatch.setattr(
        db_mod, "get_configs_for_subscription", AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        db_mod, "get_or_create_sub_token", AsyncMock(return_value=""),
    )

    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock(return_value=None)
    fake_bot.send_document = AsyncMock(return_value=None)

    await vpn_mod.send_purchase_success_message(
        bot=fake_bot,
        user_id=USER_ID,
        sub_id=sub_id,
        plan=VPN_PLANS["vpn_base"],
        plan_key="vpn_base",
        expires_at=datetime.utcnow() + timedelta(days=30),
        delivered=1,
        total=1,
    )

    fake_bot.send_message.assert_awaited_once()
    text = fake_bot.send_message.call_args.args[1]
    assert "активирован" in text
    # И НЕ должно быть «Бонусные дни активированы» — это другой title_key.
    assert "Бонусные дни" not in text


async def test_send_purchase_success_custom_title_key(fresh_db, monkeypatch):
    """title_key='bot_referral_reactivate_title' → renders the referral
    reactivate wording, not the default 'spasibo za pokupku'."""
    from handlers import vpn as vpn_mod
    from services import database as db_mod
    from services.plans import VPN_PLANS

    await _add_user()
    sub_id = await _make_paid_sub(USER_ID, plan="vpn_max")

    monkeypatch.setattr(
        db_mod, "get_configs_for_subscription", AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        db_mod, "get_or_create_sub_token", AsyncMock(return_value=""),
    )

    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock(return_value=None)
    fake_bot.send_document = AsyncMock(return_value=None)

    await vpn_mod.send_purchase_success_message(
        bot=fake_bot,
        user_id=USER_ID,
        sub_id=sub_id,
        plan=VPN_PLANS["vpn_max"],
        plan_key="vpn_max",
        expires_at=datetime.utcnow() + timedelta(days=14),
        delivered=1,
        total=1,
        title_key="bot_referral_reactivate_title",
    )

    fake_bot.send_message.assert_awaited_once()
    text = fake_bot.send_message.call_args.args[1]
    assert "Бонусные дни активированы" in text

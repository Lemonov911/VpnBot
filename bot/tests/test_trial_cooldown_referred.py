"""
Regression: trial cooldown — разный для regular vs referred (мой C16 fix).

Сценарий: referred-юзер получает 7-day trial (vs 3 у regular). Без длиннее
cooldown'а можно фармить trials с alt-аккаунтов раз в 30 дней — 12 циклов
за год = 84 free дня на каждый аккаунт.

Fix: TRIAL_COOLDOWN_DAYS_REFERRED = 90, regular остался 30.
"""
from datetime import datetime, timedelta

import aiosqlite
import pytest

import services.database as _db_mod
import services.trial as _trial_mod
from services.database import (
    create_subscription,
    set_referred_by,
    upsert_user,
)
from services.trial import (
    TRIAL_COOLDOWN_DAYS,
    TRIAL_COOLDOWN_DAYS_REFERRED,
    can_claim_trial,
    trial_cooldown_days_for,
)


@pytest.fixture(autouse=True)
def _patch_trial_db_path(fresh_db, monkeypatch):
    """trial.py делает `from services.database import DB_PATH` на module top —
    биндит локальное имя при импорте, fresh_db monkeypatch на db_mod.DB_PATH
    не обновляет services.trial.DB_PATH. Патчим обе ссылки."""
    monkeypatch.setattr(_trial_mod, "DB_PATH", fresh_db)


# ── trial_cooldown_days_for: hook returns right value ───────────────────────

@pytest.mark.asyncio
async def test_regular_user_gets_60d_cooldown(fresh_db):
    # Cooldown поднят 30→60 при апгрейде триала 3→7 дней (бизнес-аудит 25.05):
    # cooldown считается от created_at, поэтому при 7д триале и 30д cooldown
    # abuse был бы 84 free дня/год. 60д держит его в 42.
    await upsert_user(801, "regular", "Regular User")
    assert await trial_cooldown_days_for(801) == TRIAL_COOLDOWN_DAYS == 60


@pytest.mark.asyncio
async def test_referred_user_gets_90d_cooldown(fresh_db):
    """Referred-юзер (referred_by != NULL) получает удлинённый cooldown."""
    await upsert_user(802, "referrer", "Referrer")
    await upsert_user(803, "referred", "Referred")
    await set_referred_by(803, 802)
    assert await trial_cooldown_days_for(803) == TRIAL_COOLDOWN_DAYS_REFERRED == 90


# ── can_claim_trial respects cooldown delta ──────────────────────────────────

async def _seed_expired_trial(user_id: int, days_ago: int):
    """Создаёт trial-sub созданный N дней назад + помеченный expired.
    `created_at` важен — can_claim_trial фильтрует по нему."""
    sub_id = await create_subscription(
        user_id=user_id, plan="vpn_trial",
        payment_id=f"trial_{user_id}_{days_ago}d_ago",
        stars_paid=0,
        expires_at=datetime.utcnow() - timedelta(days=days_ago - 3),
    )
    # Прямой UPDATE created_at в прошлое + status=expired
    async with aiosqlite.connect(_db_mod.DB_PATH) as db:
        past_ts = (datetime.utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
        await db.execute(
            "UPDATE subscriptions SET created_at=?, status='expired' WHERE id=?",
            (past_ts, sub_id),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_regular_user_can_reclaim_after_61_days(fresh_db):
    """Regular: trial был 61 день назад → cooldown 60d прошёл → can_claim=True."""
    await upsert_user(811, "regular", "Regular")
    await _seed_expired_trial(811, days_ago=61)
    assert await can_claim_trial(811) is True


@pytest.mark.asyncio
async def test_regular_user_blocked_at_59_days(fresh_db):
    """Regular: trial был 59 дней назад → cooldown 60d ещё не прошёл → False."""
    await upsert_user(812, "regular2", "Regular2")
    await _seed_expired_trial(812, days_ago=59)
    assert await can_claim_trial(812) is False


@pytest.mark.asyncio
async def test_referred_user_blocked_at_45_days(fresh_db):
    """Referred с 90d cooldown: trial был 45 дней назад → ещё не прошло → False.
    Под старым 30d cooldown'ом юзер бы прошёл — это и есть закрываемая дыра."""
    await upsert_user(821, "ref_src", "Source")
    await upsert_user(822, "ref_dst", "Dest")
    await set_referred_by(822, 821)
    await _seed_expired_trial(822, days_ago=45)
    assert await can_claim_trial(822) is False, (
        "referred юзер должен ждать 90 дней, не 30 — anti-abuse"
    )


@pytest.mark.asyncio
async def test_referred_user_can_reclaim_after_91_days(fresh_db):
    await upsert_user(831, "ref_src", "Source")
    await upsert_user(832, "ref_dst", "Dest")
    await set_referred_by(832, 831)
    await _seed_expired_trial(832, days_ago=91)
    assert await can_claim_trial(832) is True

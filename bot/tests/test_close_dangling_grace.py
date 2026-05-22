"""
Regression: close_dangling_grace_subs_after_upgrade — cross-plan grace cleanup.

Scenario это чинит: юзер на vpn_base в grace (14d throttle), покупает vpn_max.
Раньше (EU-F7) старая grace-sub оставалась до natural grace_until expire —
14 дней Happ балансировал между throttled и full-speed peers, peer_count drift.
Теперь: после успешного provisioning нового sub helper закрывает дряхлые grace.

Тесты не дёргают агента — `_close_dangling_grace` мокается, проверяем только
что вызван с правильными аргументами / не вызван когда не должен.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.database import (
    create_subscription,
    mark_subscription_grace,
    upsert_user,
)


USER_ID = 9001


async def _ensure_user():
    """FK на users.id — без записи в users create_subscription упадёт IntegrityError."""
    await upsert_user(USER_ID, username="test_user", first_name="Test")


async def _make_sub(plan: str, status: str = "active") -> int:
    """Создаёт sub в нужном статусе. Возвращает sub_id.

    Grace-sub создаётся с expires_at В ПРОШЛОМ: mark_subscription_grace
    атомарно проверяет `expires_at <= now()` (защита от race с admin extend),
    иначе остаётся active и тест не находит «дряхлую grace».
    """
    if status == "grace":
        # Past expires_at — иначе mark_subscription_grace откажет (CAS-guard).
        expires_at = datetime.utcnow() - timedelta(days=1)
    else:
        expires_at = datetime.utcnow() + timedelta(days=30)
    sub_id = await create_subscription(
        user_id=USER_ID, plan=plan,
        payment_id=f"chg_{plan}_{datetime.utcnow().timestamp()}",
        stars_paid=145, expires_at=expires_at,
    )
    if status == "grace":
        grace_until = (datetime.utcnow() + timedelta(days=10)).isoformat()
        ok = await mark_subscription_grace(sub_id, grace_until)
        assert ok, "fixture: mark_subscription_grace вернул False — проверь expires_at"
    return sub_id


@pytest.mark.asyncio
async def test_closes_old_grace_when_user_upgrades_to_different_plan(fresh_db):
    """User: grace на vpn_base + новая active vpn_max → старая grace закрывается."""
    from services.grace import close_dangling_grace_subs_after_upgrade

    await _ensure_user()
    old_grace_sub = await _make_sub("vpn_base", status="grace")
    new_active_sub = await _make_sub("vpn_max", status="active")

    bot = MagicMock()
    with patch("services.grace._close_dangling_grace", new=AsyncMock()) as mock_close:
        closed = await close_dangling_grace_subs_after_upgrade(
            bot, USER_ID, new_sub_id=new_active_sub,
        )

    assert closed == 1
    mock_close.assert_awaited_once()
    args = mock_close.call_args
    # _close_dangling_grace(bot, sub_id, plan_key)
    assert args[0][1] == old_grace_sub
    assert args[0][2] == "vpn_base"


@pytest.mark.asyncio
async def test_does_nothing_when_only_active_sub_exists(fresh_db):
    """No grace subs anywhere → helper returns 0, _close не вызывался."""
    from services.grace import close_dangling_grace_subs_after_upgrade

    await _ensure_user()
    new_active_sub = await _make_sub("vpn_max", status="active")

    bot = MagicMock()
    with patch("services.grace._close_dangling_grace", new=AsyncMock()) as mock_close:
        closed = await close_dangling_grace_subs_after_upgrade(
            bot, USER_ID, new_sub_id=new_active_sub,
        )

    assert closed == 0
    mock_close.assert_not_awaited()


@pytest.mark.asyncio
async def test_does_not_close_same_sub_passed_as_exclude(fresh_db):
    """Edge case: грейс-sub в БД, но её id == exclude (т.е. renew_from_grace
    превратил её в active с тем же id) → exclude filter работает, 0 closed."""
    from services.grace import close_dangling_grace_subs_after_upgrade

    # Помечаем sub как grace И передаём её же как exclude — имитирует ситуацию
    # «sub статус ещё grace из-за read-after-write race, но caller знает
    # что это и есть его новая sub».
    await _ensure_user()
    sub_id = await _make_sub("vpn_base", status="grace")

    bot = MagicMock()
    with patch("services.grace._close_dangling_grace", new=AsyncMock()) as mock_close:
        closed = await close_dangling_grace_subs_after_upgrade(
            bot, USER_ID, new_sub_id=sub_id,
        )

    assert closed == 0
    mock_close.assert_not_awaited()


@pytest.mark.asyncio
async def test_swallows_close_errors_and_returns_zero(fresh_db):
    """_close_dangling_grace raises → helper не падает, returns 0, не крашит caller.

    Критично: caller — webhook handler, любое исключение тут вернёт 500 платёжке
    (CryptoBot/OxaPay/Lava), та начнёт ретраить, юзер не увидит провижининг.
    """
    from services.grace import close_dangling_grace_subs_after_upgrade

    await _ensure_user()
    await _make_sub("vpn_base", status="grace")
    new_sub = await _make_sub("vpn_max", status="active")

    bot = MagicMock()
    failing_close = AsyncMock(side_effect=RuntimeError("agent down"))
    with patch("services.grace._close_dangling_grace", new=failing_close):
        # Не должно бросить
        closed = await close_dangling_grace_subs_after_upgrade(
            bot, USER_ID, new_sub_id=new_sub,
        )

    assert closed == 0
    failing_close.assert_awaited_once()


@pytest.mark.asyncio
async def test_closes_multiple_dangling_grace_subs(fresh_db):
    """Data drift: у юзера 2 grace-sub (например, scheduler-bug в прошлом
    оставил два) → обе закрываются."""
    from services.grace import close_dangling_grace_subs_after_upgrade

    await _ensure_user()
    grace_sub_a = await _make_sub("vpn_base", status="grace")
    grace_sub_b = await _make_sub("vpn_pro",  status="grace")
    new_sub     = await _make_sub("vpn_max",  status="active")

    bot = MagicMock()
    with patch("services.grace._close_dangling_grace", new=AsyncMock()) as mock_close:
        closed = await close_dangling_grace_subs_after_upgrade(
            bot, USER_ID, new_sub_id=new_sub,
        )

    assert closed == 2
    assert mock_close.await_count == 2
    closed_sub_ids = {call.args[1] for call in mock_close.await_args_list}
    assert closed_sub_ids == {grace_sub_a, grace_sub_b}

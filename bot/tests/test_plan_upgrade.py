"""
Plan upgrade flow tests.

Two layers:

1. DB-layer (change_subscription_plan / schedule_plan_change) — deterministic
   state-machine tests, no network.

2. Handler-layer (_apply_plan_upgrade) — payload parsing, security check
   (sub_id must belong to paying user), grace → active transition.
   The agent (VpnctlClient) is mocked since the upgrade-from-grace branch
   makes real HTTP calls otherwise.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import aiosqlite

from services.database import (
    upsert_user,
    create_subscription,
    change_subscription_plan,
    schedule_plan_change,
    mark_subscription_grace,
    get_subscription_by_id,
    get_configs_for_subscription,
)
from services.plans import VPN_PLANS


# ── helpers ───────────────────────────────────────────────────────────────────

USER_ID  = 5001
OTHER_ID = 5002  # attacker user

FUTURE = datetime.utcnow() + timedelta(days=30)


async def _make_sub(user_id: int = USER_ID, plan: str = "vpn_base") -> int:
    return await create_subscription(
        user_id=user_id, plan=plan,
        payment_id=f"chg_{user_id}_{plan}_{datetime.utcnow().timestamp()}",
        stars_paid=145, expires_at=FUTURE,
    )


async def _count_configs(db_path, sub_id: int, protocol: str | None = None) -> int:
    q = "SELECT COUNT(*) FROM configs WHERE subscription_id=?"
    args: tuple = (sub_id,)
    if protocol is not None:
        q += " AND protocol=?"
        args = (sub_id, protocol)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(q, args) as cur:
            return (await cur.fetchone())[0]


def _make_payment(payload: str, user_id: int, total: int = 215,
                   charge_id: str = "chg_upgrade") -> MagicMock:
    p = MagicMock()
    p.invoice_payload = payload
    p.total_amount = total
    p.telegram_payment_charge_id = charge_id
    return p


def _make_message(user_id: int, payment) -> MagicMock:
    msg = MagicMock()
    msg.from_user = MagicMock(id=user_id)
    msg.successful_payment = payment
    msg.answer = AsyncMock()
    msg.bot = MagicMock()
    return msg


# ── DB-layer: change_subscription_plan ────────────────────────────────────────

@pytest.mark.asyncio
async def test_upgrade_changes_plan_and_clears_pending(fresh_db):
    """Базовый апгрейд: plan меняется, pending_plan сбрасывается."""
    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(plan="vpn_base")
    await schedule_plan_change(sub_id, "vpn_base")  # был запланирован даунгрейд

    await change_subscription_plan(sub_id, "vpn_max", USER_ID,
                                    awg_delta=1, vless_delta=4, wg_delta=0)

    sub = await get_subscription_by_id(sub_id)
    assert sub["plan"] == "vpn_max"
    assert sub["pending_plan"] is None
    assert sub["status"] == "active"


@pytest.mark.asyncio
async def test_upgrade_adds_correct_slot_deltas(fresh_db):
    """awg_delta / vless_delta / wg_delta создают новые пустые слоты."""
    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(plan="vpn_base")
    # vpn_base — slots создаются вручную в флоу _deliver_vpn. Тут проверяем
    # только дельту: было 0 слотов на sub_id, добавляем 1+4+0.
    await change_subscription_plan(sub_id, "vpn_max", USER_ID,
                                    awg_delta=1, vless_delta=4, wg_delta=0)

    assert await _count_configs(fresh_db, sub_id, "awg") == 1
    assert await _count_configs(fresh_db, sub_id, "vless") == 4
    assert await _count_configs(fresh_db, sub_id, "wg") == 0


@pytest.mark.asyncio
async def test_upgrade_zero_deltas_creates_no_slots(fresh_db):
    """delta=0 → ни одного слота не создаётся (например, downgrade-форма
    которая использует change_plan но без расширения)."""
    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(plan="vpn_max")
    await change_subscription_plan(sub_id, "vpn_base", USER_ID,
                                    awg_delta=0, vless_delta=0, wg_delta=0)

    assert await _count_configs(fresh_db, sub_id) == 0


@pytest.mark.asyncio
async def test_upgrade_negative_deltas_do_not_remove_slots(fresh_db):
    """Отрицательная дельта (теоретически даунгрейд) НЕ должна удалять слоты —
    `max(0, delta)` в коде. Иначе у юзера пропадают активные конфиги при
    смене плана."""
    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(plan="vpn_max")
    # Pre-seed: 3 awg слота уже есть
    async with aiosqlite.connect(fresh_db) as db:
        for _ in range(3):
            await db.execute(
                "INSERT INTO configs (subscription_id, user_id, protocol, status) "
                "VALUES (?,?,?,?)",
                (sub_id, USER_ID, "awg", "active"),
            )
        await db.commit()

    await change_subscription_plan(sub_id, "vpn_base", USER_ID,
                                    awg_delta=-2, vless_delta=-1, wg_delta=0)

    # Слотов всё ещё 3 — даунгрейд не удалил
    assert await _count_configs(fresh_db, sub_id, "awg") == 3


@pytest.mark.asyncio
async def test_upgrade_from_grace_restores_active_and_extends_expiry(fresh_db):
    """Апгрейд из grace → status='active', grace_until=NULL,
    expires_at сдвигается на now+30 дней (иначе юзер заплатил, а expires
    остался в прошлом)."""
    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(plan="vpn_base")
    # Истекла + переведена в grace
    await mark_subscription_grace(sub_id, (datetime.utcnow() + timedelta(days=5)).isoformat())

    sub_before = await get_subscription_by_id(sub_id)
    assert sub_before["status"] == "grace"
    assert sub_before["grace_until"] is not None

    await change_subscription_plan(sub_id, "vpn_max", USER_ID,
                                    awg_delta=1, vless_delta=4, wg_delta=0)

    sub_after = await get_subscription_by_id(sub_id)
    assert sub_after["status"] == "active"
    assert sub_after["grace_until"] is None
    # expires_at должен быть в будущем (~30 дней от now)
    expires = datetime.fromisoformat(sub_after["expires_at"].replace(" ", "T"))
    days_ahead = (expires - datetime.utcnow()).total_seconds() / 86400
    assert 29 < days_ahead < 31, f"expected ~30 days ahead, got {days_ahead}"


@pytest.mark.asyncio
async def test_upgrade_from_active_does_not_touch_expires_at(fresh_db):
    """Апгрейд из active → expires_at НЕ сдвигается (юзер платит за апгрейд
    функционала, не за продление; продление — отдельный flow)."""
    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(plan="vpn_base")

    sub_before = await get_subscription_by_id(sub_id)
    expires_before = sub_before["expires_at"]

    await change_subscription_plan(sub_id, "vpn_max", USER_ID,
                                    awg_delta=1, vless_delta=4, wg_delta=0)

    sub_after = await get_subscription_by_id(sub_id)
    assert sub_after["expires_at"] == expires_before


# ── DB-layer: schedule_plan_change (pending downgrade) ────────────────────────

@pytest.mark.asyncio
async def test_schedule_pending_plan_change(fresh_db):
    """Юзер ставит даунгрейд на следующий месяц → pending_plan записан."""
    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(plan="vpn_max")
    await schedule_plan_change(sub_id, "vpn_base")

    sub = await get_subscription_by_id(sub_id)
    assert sub["pending_plan"] == "vpn_base"


@pytest.mark.asyncio
async def test_cancel_pending_plan_change(fresh_db):
    """schedule_plan_change(None) — отмена."""
    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(plan="vpn_max")
    await schedule_plan_change(sub_id, "vpn_base")
    await schedule_plan_change(sub_id, None)

    sub = await get_subscription_by_id(sub_id)
    assert sub["pending_plan"] is None


# ── Handler-layer: _apply_plan_upgrade ────────────────────────────────────────

@pytest_asyncio.fixture
async def user_with_sub(fresh_db):
    """User + active vpn_base subscription. Returns (user_id, sub_id)."""
    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(plan="vpn_base")
    return USER_ID, sub_id


@pytest.mark.asyncio
async def test_handler_valid_payload_applies_upgrade(user_with_sub):
    """Happy path: payload корректный, sub принадлежит юзеру → апгрейд."""
    user_id, sub_id = user_with_sub
    payload = f"plan_upgrade:{sub_id}:vpn_max:1:4:0"
    payment = _make_payment(payload, user_id)
    msg = _make_message(user_id, payment)

    from handlers.vpn import _apply_plan_upgrade
    await _apply_plan_upgrade(msg, payment)

    sub = await get_subscription_by_id(sub_id)
    assert sub["plan"] == "vpn_max"


@pytest.mark.asyncio
async def test_handler_rejects_upgrade_for_another_users_sub(fresh_db):
    """SECURITY: атакующий получил invoice-URL чужого юзера и оплатил его.
    Sub принадлежит USER_ID, оплатил OTHER_ID → reject, никакого апгрейда."""
    await upsert_user(USER_ID, "u", "U")
    await upsert_user(OTHER_ID, "o", "O")
    sub_id = await _make_sub(user_id=USER_ID, plan="vpn_base")

    payload = f"plan_upgrade:{sub_id}:vpn_max:1:4:0"
    payment = _make_payment(payload, OTHER_ID)
    msg = _make_message(OTHER_ID, payment)

    from handlers.vpn import _apply_plan_upgrade
    await _apply_plan_upgrade(msg, payment)

    sub = await get_subscription_by_id(sub_id)
    assert sub["plan"] == "vpn_base", "Plan must NOT change on cross-user upgrade attempt"
    msg.answer.assert_awaited()  # error message shown to attacker


@pytest.mark.asyncio
async def test_handler_rejects_missing_sub(fresh_db):
    """sub_id не существует (удалили / опечатка) → graceful error, без exception."""
    await upsert_user(USER_ID, "u", "U")

    payload = "plan_upgrade:99999:vpn_max:1:4:0"
    payment = _make_payment(payload, USER_ID)
    msg = _make_message(USER_ID, payment)

    from handlers.vpn import _apply_plan_upgrade
    await _apply_plan_upgrade(msg, payment)  # must not raise

    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_handler_rejects_unknown_plan_key(user_with_sub):
    """plan_key не в VPN_PLANS → error, sub не меняется."""
    user_id, sub_id = user_with_sub
    payload = f"plan_upgrade:{sub_id}:vpn_phantom:1:4:0"
    payment = _make_payment(payload, user_id)
    msg = _make_message(user_id, payment)

    from handlers.vpn import _apply_plan_upgrade
    await _apply_plan_upgrade(msg, payment)

    sub = await get_subscription_by_id(sub_id)
    assert sub["plan"] == "vpn_base"


@pytest.mark.asyncio
async def test_handler_rejects_malformed_payload(user_with_sub):
    """Payload с неправильным количеством частей → error без crash."""
    user_id, sub_id = user_with_sub
    payment = _make_payment(f"plan_upgrade:{sub_id}:vpn_max", user_id)  # 3 parts только
    msg = _make_message(user_id, payment)

    from handlers.vpn import _apply_plan_upgrade
    await _apply_plan_upgrade(msg, payment)

    sub = await get_subscription_by_id(sub_id)
    assert sub["plan"] == "vpn_base"


@pytest.mark.asyncio
async def test_handler_rejects_non_integer_deltas(user_with_sub):
    """Дельты не int (manipulated payload) → ValueError → graceful error."""
    user_id, sub_id = user_with_sub
    payment = _make_payment(f"plan_upgrade:{sub_id}:vpn_max:x:y:z", user_id)
    msg = _make_message(user_id, payment)

    from handlers.vpn import _apply_plan_upgrade
    await _apply_plan_upgrade(msg, payment)

    sub = await get_subscription_by_id(sub_id)
    assert sub["plan"] == "vpn_base"


@pytest.mark.asyncio
async def test_handler_upgrade_from_grace_calls_unthrottle(user_with_sub):
    """Sub в grace → апгрейд должен дополнительно вызвать unthrottle/move
    через vpnctl_client (иначе юзер заплатил, plan=Max в UI, но 256kbps реально).
    """
    user_id, sub_id = user_with_sub
    # Pre-seed: sub в grace + один AWG слот с server_id и assigned_ip
    await mark_subscription_grace(sub_id, (datetime.utcnow() + timedelta(days=5)).isoformat())
    async with aiosqlite.connect(fresh_db := await _get_db_path()) as db:
        pass  # placeholder; we use fresh_db indirectly through fixtures

    # We need to seed a config with server_id. Insert directly.
    import services.database as db_mod
    async with aiosqlite.connect(db_mod.DB_PATH) as db:
        await db.execute(
            """INSERT INTO servers (name, host, agent_url, agent_token, is_active)
               VALUES ('S', '1.1.1.1', 'http://a', 't', 1)"""
        )
        await db.commit()
        async with db.execute("SELECT id FROM servers") as cur:
            server_id = (await cur.fetchone())[0]
        await db.execute(
            """INSERT INTO configs (subscription_id, user_id, protocol, status,
                                    server_id, peer_name, assigned_ip)
               VALUES (?, ?, 'awg', 'active', ?, 'peer_x', '10.0.0.99')""",
            (sub_id, user_id, server_id),
        )
        await db.commit()

    payload = f"plan_upgrade:{sub_id}:vpn_max:1:4:0"
    payment = _make_payment(payload, user_id)
    msg = _make_message(user_id, payment)

    mock_client = MagicMock()
    mock_client.unthrottle_peer = AsyncMock()
    mock_client.add_peer = AsyncMock()
    mock_client.remove_peer = AsyncMock()

    with patch("services.vpnctl_client.client_for_server", return_value=mock_client):
        from handlers.vpn import _apply_plan_upgrade
        await _apply_plan_upgrade(msg, payment)

    mock_client.unthrottle_peer.assert_awaited_once()
    # First arg is protocol "awg"
    assert mock_client.unthrottle_peer.await_args.args[0] == "awg"

    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "active"
    assert sub["plan"] == "vpn_max"


# Helper used by the grace test above — pytest doesn't propagate fixtures into
# async helpers, but we need DB_PATH at runtime.
async def _get_db_path():
    import services.database as db_mod
    return db_mod.DB_PATH

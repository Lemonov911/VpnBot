"""
Regression: _close_trial_on_paid_purchase (handlers/vpn.py) — try/finally
гарантирует mark_subscription_expired даже при сбое revoke на агентах или
reset_config_slot.

Без этого: paid sub created + trial sub остаётся active в БД → /sub/{token}
аггрегирует URLs из ОБОИХ subs → Happ балансит между trial-throttled и
paid-normal peer'ами → юзер пишет "оплатил, скорость не та" (#C1 audit 17.05).

Тестируем именно DB-стейт trial-sub'ы: status='expired' должен быть после
выхода из функции в любом случае (даже если revoke упал на агенте или
reset_config_slot бросил DB-ошибку).
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

import services.database as _db_mod
from services.database import (
    create_config_record,
    create_subscription,
    upsert_user,
)
from services.vpnctl_client import VpnctlError


async def _activate_cfg_directly(cfg_id: int, server_id: int, vless_uuid: str):
    """Помечаем cfg как active напрямую в БД (минуя save_peer_to_config — там
    FK на servers, в тестах серверов нет; обходим прямым UPDATE)."""
    async with aiosqlite.connect(_db_mod.DB_PATH) as db:
        # Отключаем FK для этого UPDATE — server_id ссылается на отсутствующую
        # row, но для нашего scope (revoke flow тестируем) сервер не важен,
        # важен только status и vless_uuid.
        await db.execute("PRAGMA foreign_keys=OFF")
        await db.execute(
            "UPDATE configs SET status='active', server_id=?, "
            "config_data=?, vless_uuid=?, peer_name=? WHERE id=?",
            (server_id, f"vless://{vless_uuid}@srv{server_id}",
             vless_uuid, f"peer_{cfg_id}", cfg_id),
        )
        await db.commit()


USER_ID = 9101


async def _get_sub_status(sub_id: int) -> str:
    async with aiosqlite.connect(_db_mod.DB_PATH) as db:
        async with db.execute(
            "SELECT status FROM subscriptions WHERE id=?", (sub_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else "missing"


async def _setup_trial_with_two_active_configs() -> int:
    """User + trial-sub в active со 2 VLESS-конфигами на разных серверах.
    Server FK не enforced (legacy ALTER), используем server_id напрямую."""
    await upsert_user(USER_ID, "trial_user", "Trial User")
    sub_id = await create_subscription(
        user_id=USER_ID, plan="vpn_trial",
        payment_id=f"trial_{USER_ID}_test",
        stars_paid=0,
        expires_at=datetime.utcnow() + timedelta(days=2),
        payment_provider="trial",
    )
    assert sub_id is not None
    # 2 VLESS configs — активируем напрямую (минуя save_peer_to_config FK).
    for i in range(2):
        cfg_id = await create_config_record(sub_id, USER_ID, protocol="vless")
        await _activate_cfg_directly(
            cfg_id, server_id=11 + i,
            vless_uuid="aaaaaaaa-1111-2222-3333-444444444444",
        )
    return sub_id


# ── Кейс 1: happy path — все revoke ОК → trial expired ─────────────────────

@pytest.mark.asyncio
async def test_happy_path_all_revoked_then_expired(fresh_db, monkeypatch):
    """Все agent revoke и DB reset проходят → trial.status=expired."""
    sub_id = await _setup_trial_with_two_active_configs()

    # Мокаем agent — каждый remove_peer успешен.
    fake_client = type("C", (), {})()
    fake_client.remove_peer = AsyncMock(return_value=None)

    fake_server_lookup = AsyncMock(return_value={
        "id": 11, "agent_url": "http://x", "name": "srv",
    })

    # client_for_server / get_server_by_id импортируются ЛОКАЛЬНО внутри
    # _close_trial_on_paid_purchase — патчим на их module of origin.
    import services.vpnctl_client as vpnctl_mod
    monkeypatch.setattr(vpnctl_mod, "client_for_server", lambda srv: fake_client)
    monkeypatch.setattr(_db_mod, "get_server_by_id", fake_server_lookup)
    from handlers import vpn as vpn_mod

    await vpn_mod._close_trial_on_paid_purchase(sub_id, USER_ID)

    assert await _get_sub_status(sub_id) == "expired"
    assert fake_client.remove_peer.await_count == 2, (
        f"должно revoke'нуть 2 пира, got {fake_client.remove_peer.await_count}"
    )


# ── Кейс 2: revoke fails for ALL configs → trial всё равно expired ─────────

@pytest.mark.asyncio
async def test_revoke_failure_still_marks_trial_expired(fresh_db, monkeypatch):
    """Agent падает на ОБОИХ remove_peer (timeout, 500) → внутренний
    try/except в loop ловит per-cfg, mark_subscription_expired В FINALLY
    всё равно срабатывает. Без этого юзер 14 дней живёт с двумя живыми
    sub'ами."""
    sub_id = await _setup_trial_with_two_active_configs()

    fake_client = type("C", (), {})()
    fake_client.remove_peer = AsyncMock(
        side_effect=VpnctlError("agent timeout"),
    )
    fake_server = {"id": 11, "agent_url": "http://x", "name": "srv"}

    import services.vpnctl_client as vpnctl_mod
    monkeypatch.setattr(vpnctl_mod, "client_for_server", lambda srv: fake_client)
    monkeypatch.setattr(_db_mod, "get_server_by_id",
                         AsyncMock(return_value=fake_server))
    from handlers import vpn as vpn_mod

    # Не должно бросить — per-cfg exception caught внутри loop'а
    await vpn_mod._close_trial_on_paid_purchase(sub_id, USER_ID)

    # Главное: trial expired ДАЖЕ при revoke fail
    assert await _get_sub_status(sub_id) == "expired", (
        "mark_subscription_expired не вызвался — try/finally сломан, юзер "
        "получит two active sub overlap до scheduler reap"
    )


# ── Кейс 3: reset_config_slot fails — trial всё равно expired ──────────────

@pytest.mark.asyncio
async def test_reset_slot_failure_still_marks_trial_expired(fresh_db, monkeypatch):
    """Если reset_config_slot бросает DB error (например WAL-corruption,
    disk full) — main loop ловит per-cfg исключение, finally всё равно
    делает mark_expired."""
    sub_id = await _setup_trial_with_two_active_configs()

    fake_client = type("C", (), {})()
    fake_client.remove_peer = AsyncMock(return_value=None)
    fake_server = {"id": 11, "agent_url": "http://x", "name": "srv"}

    import services.vpnctl_client as vpnctl_mod
    monkeypatch.setattr(vpnctl_mod, "client_for_server", lambda srv: fake_client)
    monkeypatch.setattr(_db_mod, "get_server_by_id",
                         AsyncMock(return_value=fake_server))
    from handlers import vpn as vpn_mod

    # Патчим reset_config_slot чтобы бросал. Используем patch.object на db_mod
    # потому что код импортирует функцию локально из services.database.
    failing_reset = AsyncMock(side_effect=RuntimeError("disk error"))
    monkeypatch.setattr(_db_mod, "reset_config_slot", failing_reset)

    await vpn_mod._close_trial_on_paid_purchase(sub_id, USER_ID)

    assert await _get_sub_status(sub_id) == "expired", (
        "mark_expired не вызвался при reset_config_slot failure — "
        "trial остаётся active навсегда"
    )
    # И reset был попытан минимум раз
    assert failing_reset.await_count >= 1

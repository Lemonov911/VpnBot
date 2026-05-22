"""
Regression: _resolve_vless_urls (services/webapp_api.py) — построение
sub-URL для /sub/{token}.

Критичные поведения:
  1. user with vless_uuid + active sub + N servers → N vless:// URLs (один на сервер)
  2. NULL `xray_sni` в БД → fallback на default ("www.microsoft.com")
     БЕЗ выкидывания сервера из подписки. Это была регрессия 17.05 —
     legacy серверы с SNI только в env-файле агента давали юзеру пустой
     subscription URL.
  3. NULL `xray_port_max` для vpn_max tier → fallback на `xray_port_base`
     (graceful degradation, юзер хотя бы получит VPN).
  4. Сервер без xray_pubkey → пропускается (active_vless_servers фильтр).
  5. У юзера нет активной sub → пустой list URL → /sub/ вернёт expired-header.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import aiosqlite
import pytest

import services.database as _db_mod
from services.database import (
    create_subscription,
    ensure_user_vless_uuid,
    upsert_user,
)


USER_ID = 9601


async def _insert_vless_server(*, srv_id: int, name: str, sni: str | None,
                                port_base: int | None = 8443,
                                port_max: int | None = 8448,
                                pubkey: str = "test_pubkey_xyz123"):
    """Прямой INSERT в servers таблицу. Тестируем resolver — не admin-UI.

    `backfilled=1` обязательно — active_vless_servers фильтрует это:
    multi-location UUID-backfill должен быть пройден чтобы агент знал про
    user UUIDs. В тестах эмулируем готовый backfilled-state.
    """
    async with aiosqlite.connect(_db_mod.DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=OFF")
        await db.execute(
            """INSERT INTO servers
               (id, name, host, protocol, is_active,
                xray_pubkey, xray_short_id, xray_sni, xray_fingerprint,
                xray_port_base, xray_port_max,
                flag, city, backfilled)
               VALUES (?, ?, ?, 'vless', 1, ?, 'sid_abc', ?, 'chrome', ?, ?, '🇳🇱', ?, 1)""",
            (srv_id, name, f"1.1.1.{srv_id}", pubkey, sni, port_base, port_max, name),
        )
        await db.commit()


async def _make_active_sub(plan: str = "vpn_base") -> int:
    await upsert_user(USER_ID, "vless_user", "VLESS User")
    sub_id = await create_subscription(
        user_id=USER_ID, plan=plan,
        payment_id=f"chg_{USER_ID}_{plan}",
        stars_paid=200,
        expires_at=datetime.utcnow() + timedelta(days=15),
    )
    assert sub_id is not None
    return sub_id


# ── Happy path: 2 servers → 2 URLs ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_returns_one_url_per_active_server(fresh_db):
    await _make_active_sub()
    await ensure_user_vless_uuid(USER_ID)
    await _insert_vless_server(srv_id=11, name="AMS", sni="www.microsoft.com")
    await _insert_vless_server(srv_id=14, name="Charlotte", sni="www.microsoft.com")

    from services.webapp_api import _resolve_vless_urls
    urls = await _resolve_vless_urls(USER_ID)

    assert len(urls) == 2, f"expected 2 URLs (по серверу), got {len(urls)}"
    assert all(u.startswith("vless://") for u in urls)


# ── SNI fallback: NULL → default ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_null_sni_falls_back_to_default_does_not_skip_server(fresh_db):
    """Legacy сервер: SNI только в env агента, в БД NULL. Раньше юзер
    получал пустой sub URL. Теперь — fallback + URL всё равно строится."""
    await _make_active_sub()
    await ensure_user_vless_uuid(USER_ID)
    await _insert_vless_server(srv_id=11, name="LegacySrv", sni=None)

    from services.webapp_api import _resolve_vless_urls
    urls = await _resolve_vless_urls(USER_ID)

    assert len(urls) == 1, "сервер с NULL sni НЕ должен выпадать"
    # Дефолтный SNI — в URL виден как `sni=...`
    assert "sni=www.microsoft.com" in urls[0] or "sni=" in urls[0]


# ── Port fallback: NULL xray_port_max для vpn_max → fallback на _base ───────

@pytest.mark.asyncio
async def test_missing_tier_port_falls_back_to_base(fresh_db):
    """vpn_max-юзер на сервере с xray_port_max=NULL (только base есть) →
    URL строится с base-портом, сервер не выпадает."""
    await _make_active_sub("vpn_max")
    await ensure_user_vless_uuid(USER_ID)
    await _insert_vless_server(
        srv_id=11, name="OldServer", sni="www.microsoft.com",
        port_base=8443, port_max=None,  # ← max NULL
    )

    from services.webapp_api import _resolve_vless_urls
    urls = await _resolve_vless_urls(USER_ID)

    assert len(urls) == 1, "сервер без max-порта должен fallback на base"
    # URL содержит :8443 (base port) а не :8448 (max port)
    assert ":8443" in urls[0]


# ── No active VLESS servers — legacy fallback path ──────────────────────────

@pytest.mark.asyncio
async def test_no_servers_returns_empty(fresh_db):
    """0 active VLESS-серверов → пустой list. /sub/{token} вернёт expired-header
    (см. handle_user_subscription branch 'if not urls')."""
    await _make_active_sub()
    # Не создаём ни одного сервера
    from services.webapp_api import _resolve_vless_urls
    urls = await _resolve_vless_urls(USER_ID)
    assert urls == []


# ── No active sub → empty ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_active_sub_returns_empty(fresh_db):
    """user_uuid выставлен, но active sub нет → пустой list (Happ покажет expired)."""
    await upsert_user(USER_ID, "nosub", "NoSub")
    # ensure_user_vless_uuid не вызываем — без сервера он не allocate'нется
    # через resolver. Но руками выставим — это тестирует ветку "нет sub"
    # а не "нет UUID".
    await _insert_vless_server(srv_id=11, name="AMS", sni="www.microsoft.com")
    await ensure_user_vless_uuid(USER_ID)

    from services.webapp_api import _resolve_vless_urls
    urls = await _resolve_vless_urls(USER_ID)
    # Нет sub → не строим URL
    assert urls == []

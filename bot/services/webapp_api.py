"""
aiohttp HTTP API — backend для Telegram Mini App.

VPN:
  POST /api/vpn/invoice            { plan_key } → { invoice_url }
  GET  /api/vpn/configs            → [{ id, protocol, peer_name, plan, expires_at, has_config }]
  GET  /api/vpn/config/{id}/download → файл .conf (attachment)
  POST /api/vpn/config/{id}/revoke → { ok: true }

eSIM:
  GET  /api/esim/countries         → [{ code, name, count }]
  GET  /api/esim/packages          ?country=ES → [{ packageCode, ... stars }]
  POST /api/esim/invoice           { package_code, price, stars, name } → { invoice_url }

Авторизация:
  Приоритет — заголовок X-Telegram-Init-Data.
  Fallback  — поле init_data в теле запроса (обратная совместимость).
  В DEBUG-режиме проверка отключается.
"""

import base64
import json
import logging
import os
import re
import time as _time

from aiohttp import web
from aiogram import Bot
from aiogram.types import LabeledPrice

from config import (
    DEBUG, ADMIN_ID, BOT_TOKEN, CRYPTOBOT_TOKEN, WEBAPP_URL,
    ESIM_WEBHOOK_SECRET, ADMIN_API_SECRET, SHOW_ESIM, SUB_URL_BASE,
    CRYPTOMUS_MERCHANT_UUID, CRYPTOMUS_PAYMENT_KEY, CRYPTOMUS_ENABLED,
    LAVATOP_API_KEY, LAVATOP_WEBHOOK_KEY, LAVATOP_ENABLED, LAVATOP_OFFERS,
)
from services.auth import verify_init_data
import services.esim_api as esim
from services.database import (
    get_user_configs, get_user_configs_full, get_config_by_id, activate_config_slot,
    reset_config_slot, claim_config_slot_for_activation,
    get_servers_by_protocol, get_server_by_id,
    get_active_subscription, get_last_expired_subscription, change_subscription_plan, schedule_plan_change,
    has_active_subscription, create_support_ticket, update_ticket_admin_msg,
    get_referral_stats as db_get_referral_stats,
)

logger = logging.getLogger(__name__)

# Тарифы — services.plans (единственный источник истины).
from services.plans import VPN_PLANS, vless_service_for_plan  # noqa: F401

# ── Авторизация ────────────────────────────────────────────────────────────────

def _resolve_user(request: web.Request, body: dict | None = None) -> dict | None:
    """
    Определяет пользователя из запроса.

    Порядок проверки:
      1. Заголовок X-Telegram-Init-Data
      2. Поле init_data в теле запроса (backward compat)
      3. Query-параметр init_data (для GET-запросов)
      4. В DEBUG-режиме — возвращаем admin-заглушку
    """
    # 1. Заголовок (новый способ)
    init_data = request.headers.get("X-Telegram-Init-Data", "").strip()

    # 2. Тело запроса (старый способ — совместимость с legacy POST)
    if not init_data and body:
        init_data = body.get("init_data", "").strip()

    # 3. Query-параметр — ТОЛЬКО для эндпоинтов которым он реально нужен
    #    (WebApp.openLink/downloadFile в Telegram не передаёт headers).
    #    Sec audit H1 (15.05): раньше query-fallback был для ВСЕХ эндпоинтов
    #    → initData попадал в nginx access.log на каждый запрос → 24h
    #    impersonation. Теперь только path /api/vpn/config/{id}/download|qr.
    if not init_data:
        path = request.path
        if "/download" in path or "/qr" in path:
            init_data = request.rel_url.query.get("init_data", "").strip()

    user = verify_init_data(init_data, BOT_TOKEN) if init_data else None

    if user is None and DEBUG:
        logger.warning("DEBUG: пропускаем проверку initData")
        user = {"id": ADMIN_ID or 0}

    return user


def _unauthorized() -> web.Response:
    return web.json_response({"error": "Unauthorized"}, status=401)


def _int_param(request: web.Request, name: str) -> int | None:
    try:
        return int(request.match_info[name])
    except (ValueError, KeyError):
        return None


def _client_ip(request: web.Request) -> str:
    """Real client IP за nginx-прокси.

    `request.remote` за nginx-proxy = `127.0.0.1` → все rate-limit'ы
    ломаются в global gate (1 req / 6с на ВСЁ инстанс).  nginx во всех
    наших location-блоках ставит `X-Real-IP` = реальный peer-IP.
    Fallback на X-Forwarded-For (первый IP в списке), затем на remote.
    """
    return (
        request.headers.get("X-Real-IP")
        or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.remote
        or ""
    )


# ── VPN хендлеры ───────────────────────────────────────────────────────────────

async def handle_vpn_invoice(request: web.Request) -> web.Response:
    # Rate-limit: 6s/IP. Telegram Stars createInvoiceLink имеет soft-лимиты
    # ~30 req/min, спам этого endpoint лочит всю продажу.
    ip = _client_ip(request)
    if not _rate_limit_check_evict(_invoice_rate, ip, _time.monotonic(), window=6.0):
        return web.json_response({"error": "rate_limited"}, status=429)
    body = await request.json()
    user = _resolve_user(request, body)
    if user is None:
        return _unauthorized()

    plan = VPN_PLANS.get(body.get("plan_key", ""))
    if not plan:
        return web.json_response({"error": "Unknown plan"}, status=400)

    # Блокируем покупку если уже есть активная подписка
    existing_sub = await get_active_subscription(user["id"])
    # Триал — не платная подписка, юзер должен иметь возможность купить
    # обычный тариф. Триал-пиры закроются в provision_vpn_slots_async /
    # _deliver_vpn после успешного платежа (см. _close_trial_on_paid_purchase).
    if existing_sub and existing_sub.get("plan") != "vpn_trial":
        return web.json_response(
            {"error": "У тебя уже есть активная подписка. Используй смену тарифа."},
            status=400,
        )

    bot: Bot = request.app["bot"]

    # Auto-renew подписка через Telegram Stars: subscription_period=2592000 (30 дней).
    # Доступно ТОЛЬКО для 1м планов (vpn_base, vpn_max без суффикса) — Telegram
    # не поддерживает другие периоды subscription'ов.
    # Multi-period (3/6/12) — всегда one-time, флаг recurring игнорируем.
    recurring = bool(body.get("recurring")) and not plan.get("multi_period")

    invoice_kwargs: dict = dict(
        title=f"VPN {plan['name']}",
        description=f"Доступ к VPN на {plan['duration_days']} дней. VLESS-Reality.",
        payload=body["plan_key"],
        currency="XTR",
        prices=[LabeledPrice(label=plan["name"], amount=plan["stars"])],
        provider_token="",
    )
    if recurring:
        invoice_kwargs["subscription_period"] = 2592000  # 30 days, единственное поддерживаемое значение

    url = await bot.create_invoice_link(**invoice_kwargs)
    logger.info("VPN invoice: user=%s plan=%s recurring=%s",
                user.get("id"), body["plan_key"], recurring)
    return web.json_response({"invoice_url": url})


async def handle_vpn_configs(request: web.Request) -> web.Response:
    """Возвращает список конфигов пользователя с данными сервера и трафиком."""
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    configs = await get_user_configs_full(user["id"])

    # Форматируем трафик и убираем чувствительные поля
    result = []
    for c in configs:
        result.append({
            "id":           c["id"],
            "protocol":     c["protocol"],
            "label":        c["label"] or c["peer_name"] or f"Устройство #{c['slot_num']}",
            "slot_num":     c["slot_num"],
            "status":       c["status"],
            "has_config":   bool(c["config_data"]),
            "assigned_ip":  c.get("assigned_ip", ""),
            "rx_bytes":     c.get("rx_bytes", 0),
            "tx_bytes":     c.get("tx_bytes", 0),
            "rx_human":     _fmt_bytes(c.get("rx_bytes", 0)),
            "tx_human":     _fmt_bytes(c.get("tx_bytes", 0)),
            "last_seen":    c.get("last_seen"),
            "plan":         c["plan"],
            "expires_at":   c["expires_at"],
            "sub_status":   c["sub_status"],
            "server_name":  c.get("server_name") or "",
            "server_flag":  c.get("flag") or "🌍",
            "server_city":  c.get("city") or "",
            "vless_url":    c.get("config_data") if c["protocol"] == "vless" else None,
        })
    return web.json_response(result)


def _fmt_bytes(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    elif b < 1024 ** 2:
        return f"{b/1024:.1f} KB"
    elif b < 1024 ** 3:
        return f"{b/1024**2:.1f} MB"
    else:
        return f"{b/1024**3:.2f} GB"


async def handle_vpn_config_download(request: web.Request) -> web.Response:
    """Отдаёт .conf файл для скачивания.

    Для AWG/WG default: подменяет `AllowedIPs = 0.0.0.0/0` на bypass-список
    (всё кроме RU CIDR) — Сбер/Кинопоиск/Госуслуги работают через локальный
    RU-IP, остальное через VPN. Эквивалент sing-box smart routing для Happ.
    `?mode=full` — full tunnel (старое поведение).
    """
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    config_id = _int_param(request, "id")
    if config_id is None:
        return web.json_response({"error": "Invalid ID"}, status=400)
    config = await get_config_by_id(config_id)

    if not config or config["user_id"] != user["id"]:
        return web.json_response({"error": "Not found"}, status=404)

    if not config.get("config_data"):
        return web.json_response({"error": "Config not ready yet"}, status=404)

    # Full-tunnel .conf без правок. Smart bypass убран после
    # тестов 17.05 — iOS WG split tunneling фундаментально кривой
    # (Apple `excludedRoutes` bug + WireGuardKit ограничения).
    # Юзеры для Сбер/Yandex отключают VPN на 1 минуту.
    body = config["config_data"]

    # Human-friendly filename — `MAX VPN 🇳🇱 Amsterdam.conf` вместо
    # `tg154923518_41.conf` (наш внутренний tg-id-based label).
    # AmneziaWG / WireGuard на iOS берут tunnel name именно из filename.
    filename = await _build_friendly_filename(config)
    # RFC 5987: для UTF-8 эмодзи/кириллицы используем filename* (browser
    # parses, AmneziaWG/WG-iOS parses). Параллельно даём ASCII fallback
    # через `filename=`, иначе старые клиенты могут получить мусор.
    from urllib.parse import quote
    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii") or f"vpn_{config_id}.conf"
    encoded = quote(filename, safe="")
    return web.Response(
        body=body.encode(),
        content_type="text/plain",
        headers={
            "Content-Disposition":
                f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}",
        },
    )


async def _build_friendly_filename(config: dict) -> str:
    """Build user-facing .conf filename: `MAX VPN {flag} {city/name}.conf`.

    Берётся info о сервере (флаг + city/name). Если server-info нет —
    fallback на peer_name (`tg<id>_N`).  Файл-имя содержит только разрешённые
    в Windows/macOS/iOS символы (нет `/ \\ : * ? " < > |`).
    """
    server_id = config.get("server_id")
    server_label = None
    if server_id:
        try:
            from services.database import get_server_by_id
            server = await get_server_by_id(server_id)
            if server:
                flag = (server.get("flag") or "").strip()
                city = (server.get("city") or server.get("name") or "").strip()
                if flag and city:
                    server_label = f"{flag} {city}"
                elif city:
                    server_label = city
                elif flag:
                    server_label = flag
        except Exception:
            pass

    base = f"MAX VPN {server_label}" if server_label else (
        config.get("peer_name") or f"vpn_config_{config['id']}"
    )
    safe = base
    for bad in "/\\:*?\"<>|":
        safe = safe.replace(bad, " ")
    return f"{safe}.conf"


async def handle_vpn_config_qr(request: web.Request) -> web.Response:
    """Возвращает QR-код конфига как PNG.

    Note: AWG bypass-AllowedIPs (~350 KB) НЕ помещается в QR (max ~3 KB).
    Поэтому QR всегда отдаёт full-tunnel .conf. Для bypass-режима юзер
    скачивает .conf файл через `/download` (smart по дефолту).
    """
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    config_id = _int_param(request, "id")
    if config_id is None:
        return web.json_response({"error": "Invalid ID"}, status=400)
    config = await get_config_by_id(config_id)

    if not config or config["user_id"] != user["id"]:
        return web.json_response({"error": "Not found"}, status=404)

    if not config.get("config_data"):
        return web.json_response({"error": "Config not ready yet"}, status=404)

    import io
    import qrcode  # type: ignore
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=6, border=2)
    qr.add_data(config["config_data"])
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return web.Response(body=buf.getvalue(), content_type="image/png",
                        headers={"Cache-Control": "no-store"})


async def handle_vpn_servers(request: web.Request) -> web.Response:
    """Список активных серверов для протокола: GET /api/vpn/servers?protocol=awg"""
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    protocol = request.rel_url.query.get("protocol", "awg")
    servers = await get_servers_by_protocol(protocol)
    # Не отдаём чувствительные поля (пароль, ключ)
    safe = [{"id": s["id"], "name": s["name"], "location": s["location"]} for s in servers]
    return web.json_response(safe)


_status_cache: dict = {"data": None, "ts": 0.0}
_status_rate:  dict[str, float] = {}
_sub_rate:     dict[str, float] = {}  # rate-limit для /sub/{token}
# Rate-limit buckets для POST endpoints — защита от спама счетов/тикетов.
_invoice_rate:  dict[str, float] = {}  # /api/vpn/invoice, /api/esim/invoice
_crypto_rate:   dict[str, float] = {}  # /api/vpn/invoice/crypto
_cryptomus_rate: dict[str, float] = {}  # /api/vpn/invoice/cryptomus
_lavatop_rate:   dict[str, float] = {}  # /api/vpn/invoice/lavatop
_change_rate:   dict[str, float] = {}  # /api/vpn/subscription/change
_ticket_rate:   dict[str, float] = {}  # /api/support/ticket
_trial_rate:    dict[str, float] = {}  # /api/vpn/trial/claim

async def handle_public_status(request: web.Request) -> web.Response:
    """Публичный статус всех сервисов. Без auth — для status-страницы.

    Берёт live-снимок из последней пробы (`server_health_log`) + uptime %
    за 24h/7d/30d + 24-часовой strip + последние incidents. Probes сам
    лоит scheduler каждые 60 сек в `services/health.py`.
    """
    import asyncio
    from datetime import datetime
    from services.health import uptime_summary, last_24h_strip, last_30d_strip, recent_incidents

    now = _time.monotonic()

    # Rate limit: 1 req / 6s per IP (≈10 rpm) — с lazy eviction старых ключей.
    ip = _client_ip(request)
    if not _rate_limit_check_evict(_status_rate, ip, now, window=6.0):
        return web.json_response({"error": "rate_limit"}, status=429)

    # Cache 30s
    if _status_cache["data"] is not None and now - _status_cache["ts"] < 30.0:
        return web.json_response(_status_cache["data"])

    import aiosqlite as _aiosqlite
    from services.database import DB_PATH as _DB_PATH
    async with _aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = _aiosqlite.Row

        async with db.execute(
            "SELECT * FROM servers WHERE is_active=1 ORDER BY protocol, id"
        ) as cur:
            servers = [dict(r) for r in await cur.fetchall()]

        # Последняя проба за каждый сервер.
        last_probe: dict[int, dict] = {}
        if servers:
            placeholders = ",".join("?" * len(servers))
            server_ids = [s["id"] for s in servers]
            async with db.execute(
                f"""SELECT server_id, status, latency_ms, checked_at FROM server_health_log
                    WHERE id IN (
                      SELECT MAX(id) FROM server_health_log
                      WHERE server_id IN ({placeholders})
                      GROUP BY server_id
                    )""",
                server_ids,
            ) as cur:
                for row in await cur.fetchall():
                    last_probe[row["server_id"]] = dict(row)

    async def _enrich(server: dict) -> dict:
        sid = server["id"]
        probe = last_probe.get(sid)
        status = probe["status"] if probe else "unknown"
        latency_ms = probe["latency_ms"] if probe else None
        uptime, strip24, strip30 = await asyncio.gather(
            uptime_summary(sid),
            last_24h_strip(sid),
            last_30d_strip(sid),
        )
        return {
            "id":         sid,
            "name":       server["name"],
            "flag":       server.get("flag") or "🌍",
            "location":   server.get("location", ""),
            "protocol":   server.get("protocol", ""),
            "status":     status,
            "latency_ms": latency_ms,
            "uptime":     uptime,
            "strip_24h":  strip24,
            "strip_30d":  strip30,
        }

    if servers:
        enriched = await asyncio.gather(*[_enrich(s) for s in servers])
    else:
        enriched = []
    incidents = await recent_incidents(limit=5)

    up = sum(1 for r in enriched if r["status"] == "up")
    total = len(enriched)

    payload = {
        "bot":     "up",
        "updated": datetime.utcnow().isoformat() + "Z",
        "servers": enriched,
        "summary": {"up": up, "total": total, "all_ok": up == total and total > 0},
        "incidents": [
            {
                "id":           inc["id"],
                "server_name":  inc["server_name"],
                "flag":         inc.get("flag") or "🌍",
                "started_at":   inc["started_at"],
                "resolved_at":  inc["resolved_at"],
                "duration_sec": inc["duration_sec"],
            }
            for inc in incidents
        ],
    }
    _status_cache["data"] = payload
    _status_cache["ts"]   = _time.monotonic()
    return web.json_response(payload)


# Rate-limit для public incidents endpoint — те же 1 req / 6s per IP
_incidents_rate: dict[str, float] = {}


def _rate_limit_check_evict(bucket: dict[str, float], ip: str, now: float, window: float = 6.0) -> bool:
    """Возвращает True если запрос разрешён (не превысил rate-limit), False если 429.
    Защита от unbounded memory: чистит старые записи когда dict разрастается."""
    # Lazy eviction: при росте dict'а удаляем entries старше окна.
    # Без этого attacker с 10M уникальных IPv6 = OOM на боте (sec audit M7).
    if len(bucket) > 1000:
        cutoff = now - window * 2
        stale = [k for k, v in bucket.items() if v < cutoff]
        for k in stale:
            del bucket[k]
    if now - bucket.get(ip, 0.0) < window:
        return False
    bucket[ip] = now
    return True


async def handle_public_incidents(request: web.Request) -> web.Response:
    """GET /api/status/incidents?limit=50&offset=0 — full incident history.

    Public endpoint (без auth) для status-page incident history.
    Rate limit + cache как у /api/status.
    """
    from services.health import all_incidents

    now = _time.monotonic()
    ip = _client_ip(request)
    if not _rate_limit_check_evict(_incidents_rate, ip, now, window=6.0):
        return web.json_response({"error": "rate_limit"}, status=429)

    try:
        limit = max(1, min(200, int(request.query.get("limit", "50"))))
    except ValueError:
        limit = 50
    try:
        offset = max(0, int(request.query.get("offset", "0")))
    except ValueError:
        offset = 0

    incidents, total = await all_incidents(limit=limit, offset=offset)
    return web.json_response({
        "incidents": [
            {
                "id":           inc["id"],
                "server_name":  inc.get("server_name", "?"),
                "flag":         inc.get("flag") or "🌍",
                "started_at":   inc.get("started_at"),
                "resolved_at":  inc.get("resolved_at"),
                "duration_sec": inc.get("duration_sec"),
            }
            for inc in incidents
        ],
        "total":  total,
        "limit":  limit,
        "offset": offset,
    })


async def handle_vpn_status(request: web.Request) -> web.Response:
    """Проверка доступности серверов. Требует авторизацию."""
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()
    import asyncio
    import socket

    async def _ping(server: dict) -> dict:
        host = server.get("host", "")
        try:
            loop = asyncio.get_event_loop()
            await asyncio.wait_for(
                loop.run_in_executor(None, socket.gethostbyname, host),
                timeout=3.0,
            )
            ok = True
        except Exception:
            ok = False
        return {
            "id":       server["id"],
            "name":     server["name"],
            "location": server["location"],
            "ok":       ok,
        }

    all_servers = await get_servers_by_protocol("awg")
    results = await asyncio.gather(*[_ping(s) for s in all_servers])
    return web.json_response(list(results))


async def handle_vpn_config_activate(request: web.Request) -> web.Response:
    """
    Активирует пустой слот.
    Body: { server_id: number }  — сервер выбирает пользователь в UI.
    Если server_id не передан — берём первый активный сервер протокола.
    """
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    config_id = _int_param(request, "id")
    if config_id is None:
        return web.json_response({"error": "Invalid ID"}, status=400)
    config = await get_config_by_id(config_id)

    if not config or config["user_id"] != user["id"]:
        return web.json_response({"error": "Not found"}, status=404)

    if config["status"] != "empty":
        return web.json_response({"error": "Слот уже активен"}, status=400)

    sub = await get_active_subscription(user["id"])
    if not sub or sub["id"] != config["subscription_id"]:
        return web.json_response({"error": "Нет активной подписки"}, status=403)

    # Atomic claim — защита от race: две вкладки одновременно жмут «Добавить»
    # на одном слоте. Без claim'а обе пройдут проверку status='empty' выше,
    # обе вызовут provision_peer → два peer'а на агенте, один в БД, второй
    # orphan. Claim переводит слот в 'activating' атомарно — второй запрос
    # получит rowcount=0 и отбьётся.
    if not await claim_config_slot_for_activation(config_id):
        return web.json_response(
            {"error": "Слот уже активируется в другой вкладке"}, status=409
        )

    body = await request.json()
    server_id = body.get("server_id")

    # Получаем сервер из БД
    if server_id:
        server = await get_server_by_id(server_id)
        if not server or not server["is_active"]:
            return web.json_response({"error": "Сервер недоступен"}, status=400)
    else:
        servers = await get_servers_by_protocol(config["protocol"])
        if not servers:
            return web.json_response({"error": "Нет доступных серверов"}, status=503)
        server = servers[0]
        server_id = server["id"]

    if not server.get("agent_url") or not server.get("agent_token"):
        logger.error("Server %s has no agent_url/agent_token", server.get("name", server["id"]))
        await reset_config_slot(config_id)  # rollback activating → empty
        return web.json_response({"error": "Сервер не настроен (нет агента)"}, status=503)

    peer_name = f"tg{user['id']}_{config_id}"

    from services.vpnctl_client import provision_peer, VpnctlError
    from handlers.vpn import vless_service_for_plan

    # For VLESS, resolve speed-tier service from the subscription's plan.
    if config["protocol"] == "vless":
        service_name = vless_service_for_plan(sub["plan"])
    else:
        service_name = config["protocol"]

    try:
        result = await provision_peer(server, peer_name, service_name)
    except VpnctlError as e:
        logger.error("Activate slot #%d on server %s: %s", config_id, server.get("name", server["id"]), e, exc_info=True)
        await reset_config_slot(config_id)  # rollback activating → empty
        return web.json_response({"error": "Ошибка создания конфига на сервере"}, status=503)
    except Exception as e:
        logger.error("Activate slot #%d on server %s: %s", config_id, server.get("name", server["id"]), e, exc_info=True)
        await reset_config_slot(config_id)  # rollback activating → empty
        return web.json_response({"error": "Сервер недоступен"}, status=503)

    config_data = result.config
    if not config_data:
        await reset_config_slot(config_id)  # rollback activating → empty
        return web.json_response({"error": "Ошибка создания конфига на сервере"}, status=503)

    peer_id = result.id
    peer_ip = (result.extra or {}).get("assigned_ip")
    wg_pubkey = peer_id if config["protocol"] == "awg" else None
    vless_uuid = peer_id if config["protocol"] == "vless" else None
    await activate_config_slot(
        config_id, peer_name, config_data, server_id,
        wg_pubkey=wg_pubkey, assigned_ip=peer_ip, vless_uuid=vless_uuid,
    )
    logger.info("Слот #%d активирован на %s (%s)", config_id, server["name"], peer_name)
    return web.json_response({"ok": True})


async def handle_vpn_config_revoke(request: web.Request) -> web.Response:
    """Отзывает конфиг пользователя."""
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    config_id = _int_param(request, "id")
    if config_id is None:
        return web.json_response({"error": "Invalid ID"}, status=400)
    config = await get_config_by_id(config_id)

    if not config or config["user_id"] != user["id"]:
        return web.json_response({"error": "Not found"}, status=404)

    if config["status"] != "active":
        return web.json_response({"error": "Слот не активен"}, status=400)

    # Удаляем пир с сервера через vpnctl (best-effort)
    if config.get("peer_name") and config.get("server_id"):
        try:
            srv = await get_server_by_id(config["server_id"])
            if srv:
                from services.vpnctl_client import revoke_peer
                peer_id = config.get("vless_uuid") or config.get("wg_pubkey")
                await revoke_peer(srv, peer_id, config["protocol"])
        except Exception as e:
            logger.warning("Не удалось удалить пир %s: %s", config["peer_name"], e, exc_info=True)

    # Сбрасываем слот в empty — он остаётся доступным для повторной активации
    await reset_config_slot(config_id)
    logger.info("Слот #%d сброшен в empty пользователем %s", config_id, user["id"])
    return web.json_response({"ok": True})


# ── CryptoBot хендлеры ────────────────────────────────────────────────────────

async def handle_cryptobot_invoice(request: web.Request) -> web.Response:
    """
    POST /api/vpn/invoice/crypto  { plan_key, currency: "RUB"|"USD" }
    Создаёт инвойс через CryptoBot и возвращает { pay_url }.
    """
    # Rate-limit: CryptoBot createInvoice имеет ~50 req/час, спам блокирует всё.
    ip = _client_ip(request)
    if not _rate_limit_check_evict(_crypto_rate, ip, _time.monotonic(), window=6.0):
        return web.json_response({"error": "rate_limited"}, status=429)
    if not CRYPTOBOT_TOKEN:
        return web.json_response({"error": "CryptoBot не настроен"}, status=503)

    body = await request.json()
    user = _resolve_user(request, body)
    if user is None:
        return _unauthorized()

    plan = VPN_PLANS.get(body.get("plan_key", ""))
    if not plan:
        return web.json_response({"error": "Unknown plan"}, status=400)
    # CryptoBot multi_period — ОК. Создаём отдельный invoice на нужную сумму
    # (plan.rub/usd), периодичность зашита в plan_key который вернётся в webhook.
    # Каждый период = отдельная one-time транзакция (CryptoBot не умеет recurring).

    currency = body.get("currency", "RUB").upper()
    if currency not in ("RUB", "USD"):
        return web.json_response({"error": "currency must be RUB or USD"}, status=400)

    existing_sub = await get_active_subscription(user["id"])
    # Триал — не платная подписка, юзер должен иметь возможность купить
    # обычный тариф. Триал-пиры закроются в provision_vpn_slots_async /
    # _deliver_vpn после успешного платежа (см. _close_trial_on_paid_purchase).
    if existing_sub and existing_sub.get("plan") != "vpn_trial":
        return web.json_response(
            {"error": "У тебя уже есть активная подписка. Используй смену тарифа."},
            status=400,
        )

    amount  = plan["rub"] if currency == "RUB" else plan["usd"]
    payload = f"vpn:{user['id']}:{body['plan_key']}"

    from services.cryptobot import create_invoice
    from aiogram import Bot
    bot: Bot = request.app["bot"]
    bot_info = await bot.get_me()

    try:
        invoice = await create_invoice(
            CRYPTOBOT_TOKEN,
            fiat=currency,
            amount=amount,
            payload=payload,
            description=f"VPN {plan['name']} — 30 дней · VLESS-Reality",
            bot_username=bot_info.username,
        )
    except Exception as e:
        logger.error("CryptoBot invoice error: %s", e, exc_info=True)
        return web.json_response({"error": "Ошибка платёжного сервиса"}, status=503)

    pay_url = invoice.get("mini_app_invoice_url") or invoice.get("bot_invoice_url", "")
    logger.info("CryptoBot invoice: user=%s plan=%s cur=%s url=%s",
                user.get("id"), body["plan_key"], currency, pay_url)
    return web.json_response({"pay_url": pay_url})


async def handle_cryptobot_webhook(request: web.Request) -> web.Response:
    """
    POST /api/cryptobot/webhook
    CryptoBot уведомляет об оплате инвойса.
    """
    if not CRYPTOBOT_TOKEN:
        return web.Response(status=200)

    body_bytes = await request.read()
    signature  = request.headers.get("crypto-pay-api-signature", "")

    from services.cryptobot import verify_signature
    if not verify_signature(body_bytes, signature, CRYPTOBOT_TOKEN):
        logger.warning("CryptoBot webhook: invalid signature")
        return web.Response(status=401)

    import json
    data = json.loads(body_bytes)

    if data.get("update_type") != "invoice_paid":
        return web.Response(status=200)

    invoice = data.get("payload", {})
    # Строгая проверка: invoice.status ДОЛЖЕН быть 'paid'. Без этого пустой
    # или unknown status проходит дальше. CryptoBot теоретически может
    # пошлать 'expired'/'cancelled'/'failed' для будущих событий.
    if invoice.get("status") != "paid":
        logger.warning("CryptoBot webhook: invoice status not 'paid': %r", invoice.get("status"))
        return web.Response(status=200)
    raw_payload = invoice.get("payload", "")
    logger.info("CryptoBot payment: invoice_id=%s payload=%s",
                invoice.get("invoice_id"), raw_payload)

    # payload format: "vpn:USER_ID:PLAN_KEY"
    parts = raw_payload.split(":")
    if len(parts) != 3 or parts[0] != "vpn":
        logger.warning("CryptoBot webhook: unexpected payload %s", raw_payload)
        return web.Response(status=200)

    try:
        user_id  = int(parts[1])
    except ValueError:
        logger.warning("CryptoBot webhook: bad user_id in payload %r", raw_payload)
        return web.Response(status=200)
    plan_key = parts[2]
    plan     = VPN_PLANS.get(plan_key)
    if not plan:
        logger.warning("CryptoBot webhook: unknown plan %s", plan_key)
        return web.Response(status=200)

    # Сверяем, что инвойс был выписан именно за этот план в правильной валюте.
    # Без этого payload-у можно доверять только в том, что подпись валидна —
    # но саму подпись CryptoBot ставит на любую сумму, которую мы запросили.
    # Если бы payload подделать было нельзя, юзер всё ещё мог бы выписать
    # инвойс vpn_base (200 ₽), а потом подсунуть тот же signed-body боту
    # с payload vpn_max. Поэтому проверяем currency + amount по invoice-полям.
    fiat = (invoice.get("fiat") or "").upper()
    if fiat not in ("RUB", "USD"):
        logger.warning("CryptoBot webhook: unexpected fiat=%r for invoice %s",
                       fiat, invoice.get("invoice_id"))
        return web.Response(status=400)
    try:
        invoice_amount = float(invoice.get("amount", "0"))
        expected_amount = float(plan["rub" if fiat == "RUB" else "usd"])
    except (TypeError, ValueError):
        logger.warning("CryptoBot webhook: bad amount fields invoice=%s amount=%r plan=%r",
                       invoice.get("invoice_id"), invoice.get("amount"), plan_key)
        return web.Response(status=400)
    if invoice_amount + 1e-9 < expected_amount:
        logger.warning(
            "CryptoBot webhook: amount mismatch invoice=%s plan=%s fiat=%s got=%s expected=%s — REJECTED",
            invoice.get("invoice_id"), plan_key, fiat, invoice_amount, expected_amount,
        )
        return web.Response(status=400)

    payment_id = f"crypto_{invoice.get('invoice_id')}"

    from services.database import (
        get_subscription_by_payment_id, create_subscription,
        create_order, complete_order, create_config_record,
    )
    from datetime import datetime, timedelta

    existing = await get_subscription_by_payment_id(payment_id)
    if existing:
        logger.warning("CryptoBot: duplicate payment %s", payment_id)
        return web.Response(status=200)

    # Renew-from-grace: если у юзера grace-sub того же плана — продлеваем
    # её, шлём unthrottle на агентов, returnsim 200. Иначе обычный create.
    from services.grace import try_renew_from_grace
    bot: Bot = request.app["bot"]
    if await try_renew_from_grace(
        bot, user_id, plan_key, plan, payment_id, method="crypto",
        amount_rub=int(float(plan.get("rub", 0))),
    ):
        return web.Response(status=200)

    expires_at = datetime.utcnow() + timedelta(days=plan["duration_days"])
    sub_id = await create_subscription(
        user_id=user_id,
        plan=plan_key,
        payment_id=payment_id,
        stars_paid=0,
        amount_rub=int(float(plan.get("rub", 0))),
        expires_at=expires_at,
    )
    # None = UNIQUE-constraint сработал → дубль webhook'а от CryptoBot
    # (они любят retry на 5xx). Идемпотентный 200.
    if sub_id is None:
        logger.warning("CryptoBot: payment %s TOCTOU-duplicate, ignored", payment_id)
        return web.Response(status=200)

    order_id = await create_order(
        user_id=user_id,
        product_type="vpn",
        plan=plan_key,
        stars_paid=0,
        expires_at=expires_at,
    )
    await complete_order(order_id, payment_id=payment_id)

    # Provisioning peers через vpnctl. Раньше CryptoBot-flow создавал пустые
    # config_record-ы без peer'ов → юзер платил USDT, видел "оплачен", но в
    # Mini App конфиги вечно empty. Теперь делаем реальный провижининг —
    # тот же helper что Stars-flow.
    # bot уже извлечён выше (для try_renew_from_grace).
    try:
        from handlers.vpn import provision_vpn_slots_async, maybe_award_referral_bonus
        delivered, total = await provision_vpn_slots_async(
            bot, user_id, sub_id, plan, plan_key,
        )
        # Referral bonus (если есть). Атомарный CLAIM защищает от double-award
        # при дубль-webhook'ах (Lava/CryptoBot/Cryptomus любят ретраить).
        await maybe_award_referral_bonus(bot, user_id, sub_id)
    except Exception as e:
        logger.error("CryptoBot: provision crashed for user=%d sub=%d: %s",
                     user_id, sub_id, e, exc_info=True)
        delivered, total = 0, plan["awg_slots"] + plan["vless_slots"] + plan.get("wg_slots", 0)

    # Catastrophic provision failure: 0/N. CryptoBot не имеет refund API,
    # юзер заплатил USDT и должен либо получить configs вручную, либо refund
    # через CryptoBot dashboard (manual). НЕ удаляем sub и НЕ помечаем expired —
    # юзер уже заплатил, оставляем подписку active с пустыми config-slots'ами
    # чтобы админ мог досоздать пиры через retry. Алерт админу:
    if total > 0 and delivered == 0:
        logger.error(
            "CryptoBot provision FAILED 0/%d user=%d sub=%d payment=%s — ADMIN ALERT",
            total, user_id, sub_id, payment_id,
        )
        try:
            from config import ADMIN_ID
            if ADMIN_ID:
                # Не пишем paid_amount/paid_asset в TG-чат:
                # device-compromise → financial profiling. Сумма всегда доступна
                # в CryptoBot dashboard по invoice_id.
                await bot.send_message(
                    ADMIN_ID,
                    f"🚨 <b>CryptoBot provision FAIL</b>\n\n"
                    f"User: <code>{user_id}</code>\n"
                    f"Sub: #{sub_id}\n"
                    f"Plan: {plan_key}\n"
                    f"Invoice: <code>{invoice.get('invoice_id')}</code>\n\n"
                    f"Action: проверь CryptoBot dashboard, досоздай "
                    f"конфиги вручную ИЛИ сделай refund.",
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.error("Admin alert failed: %s", e, exc_info=True)
        try:
            await bot.send_message(
                user_id,
                "❌ <b>VPN-конфиги не создались</b>\n\n"
                "Оплата USDT прошла, но сервера временно недоступны. "
                "Я уже уведомил поддержку — они подключат вручную или вернут средства "
                "в течение нескольких часов.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return web.Response(status=200)

    # Happy path — уведомляем юзера
    try:
        paid_amount = invoice.get("paid_amount", "")
        paid_asset  = invoice.get("paid_asset", "")
        note = (
            "Открой мини-апп → <b>Мои конфиги</b>."
            if delivered == total
            else f"Часть конфигов ({delivered}/{total}) готова. Остальные появятся в мини-апп."
        )
        await bot.send_message(
            user_id,
            f"✅ <b>VPN {plan['name']} оплачен!</b>\n\n"
            f"💎 Оплата: {paid_amount} {paid_asset}\n"
            f"📅 Действует до: <b>{expires_at.strftime('%d.%m.%Y')}</b>\n\n"
            f"{note}",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("CryptoBot: failed to notify user %d: %s", user_id, e, exc_info=True)

    return web.Response(status=200)


# ── Cryptomus хендлеры ────────────────────────────────────────────────────────
# Альтернатива CryptoBot: прямые on-chain платежи (BTC/ETH/USDT-TRC20/etc).
# Юзер платит на их checkout-странице, мы получаем webhook с MD5-подписью.
# Включается только если CRYPTOMUS_ENABLED=true И заданы оба ключа.

async def handle_cryptomus_invoice(request: web.Request) -> web.Response:
    """
    POST /api/vpn/invoice/cryptomus  { plan_key, currency: "RUB"|"USD" }
    Создаёт инвойс через Cryptomus и возвращает { pay_url }.
    """
    ip = _client_ip(request)
    if not _rate_limit_check_evict(_cryptomus_rate, ip, _time.monotonic(), window=6.0):
        return web.json_response({"error": "rate_limited"}, status=429)
    if not CRYPTOMUS_ENABLED:
        return web.json_response({"error": "Cryptomus не подключён"}, status=503)

    body = await request.json()
    user = _resolve_user(request, body)
    if user is None:
        return _unauthorized()

    plan_key = body.get("plan_key", "")
    plan = VPN_PLANS.get(plan_key)
    if not plan:
        return web.json_response({"error": "Unknown plan"}, status=400)
    # multi_period для Cryptomus ОК — он по своей природе one-time crypto payment,
    # для каждого периода свой invoice с правильной суммой.

    currency = (body.get("currency") or "RUB").upper()
    if currency not in ("RUB", "USD"):
        return web.json_response({"error": "currency must be RUB or USD"}, status=400)

    existing_sub = await get_active_subscription(user["id"])
    # Триал — не платная подписка, юзер должен иметь возможность купить
    # обычный тариф. Триал-пиры закроются в provision_vpn_slots_async /
    # _deliver_vpn после успешного платежа (см. _close_trial_on_paid_purchase).
    if existing_sub and existing_sub.get("plan") != "vpn_trial":
        return web.json_response(
            {"error": "У тебя уже есть активная подписка. Используй смену тарифа."},
            status=400,
        )

    amount = plan["rub"] if currency == "RUB" else plan["usd"]
    # order_id формируется детерминированно: тот же юзер + план + минута →
    # повтор кнопки в течение минуты вернёт тот же invoice (Cryptomus
    # отдаёт 422 на duplicate order_id, но мы перехватим — см. ниже).
    # Cryptomus принимает 1..128 символов из [a-zA-Z0-9_-] (включая _).
    # Используем _ как часть plan_key (vpn_base, vpn_max_3m), а - как
    # разделитель полей — однозначно парсится на webhook'е.
    ts_min = int(_time.time() // 60)
    order_id = f"vpn-{user['id']}-{plan_key}-{ts_min}"

    base_url = WEBAPP_URL or "https://maxvpnesim.com"
    api_origin = SUB_URL_BASE or "https://maxvpnesim.com"
    callback_url = f"{api_origin}/api/cryptomus/webhook"
    return_url   = f"{base_url}/vpn"  # куда юзер вернётся после оплаты

    from services.cryptomus import create_invoice as _cm_create
    try:
        invoice = await _cm_create(
            merchant_uuid=CRYPTOMUS_MERCHANT_UUID,
            payment_key=CRYPTOMUS_PAYMENT_KEY,
            amount=str(amount),
            currency=currency,
            order_id=order_id,
            callback_url=callback_url,
            return_url=return_url,
            description=f"VPN {plan['name']} 30d",
        )
    except Exception as e:
        logger.error("Cryptomus invoice error: %s", e, exc_info=True)
        return web.json_response({"error": "Ошибка платёжного сервиса"}, status=503)

    pay_url = invoice.get("url") or invoice.get("payment_url") or ""
    if not pay_url:
        logger.error("Cryptomus: no url in response %r", invoice)
        return web.json_response({"error": "Ошибка платёжного сервиса"}, status=503)

    logger.info(
        "Cryptomus invoice: user=%s plan=%s cur=%s order=%s uuid=%s",
        user.get("id"), plan_key, currency, order_id, invoice.get("uuid"),
    )
    return web.json_response({"pay_url": pay_url})


async def handle_cryptomus_webhook(request: web.Request) -> web.Response:
    """
    POST /api/cryptomus/webhook
    Cryptomus уведомляет об оплате. MD5-подпись передаётся внутри JSON в поле "sign".
    Идемпотентность: payment_id = `cryptomus_{uuid}` + UNIQUE-constraint
    на subscriptions.payment_id защищает от дубль-webhook'ов.
    """
    if not CRYPTOMUS_ENABLED:
        return web.Response(status=200)

    body_bytes = await request.read()
    try:
        payload = json.loads(body_bytes)
    except Exception:
        logger.warning("Cryptomus webhook: invalid JSON body")
        return web.Response(status=400)
    if not isinstance(payload, dict):
        return web.Response(status=400)

    received_sign = payload.get("sign", "")
    from services.cryptomus import verify_signature
    if not verify_signature(body_bytes, received_sign, CRYPTOMUS_PAYMENT_KEY):
        logger.warning(
            "Cryptomus webhook: BAD signature order_id=%s status=%s from=%s",
            payload.get("order_id"), payload.get("status"), request.remote,
        )
        return web.Response(status=401)

    status = (payload.get("status") or "").lower()
    # 'paid' — точно сумма; 'paid_over' — юзер перевёл больше, тоже зачисляем.
    # Прочие (check, process, confirm_check, fail, cancel, wrong_amount,
    # system_fail, refund_*) — игнорируем тихо.
    if status not in ("paid", "paid_over"):
        logger.info("Cryptomus webhook: ignoring status=%s order_id=%s",
                    status, payload.get("order_id"))
        return web.Response(status=200)

    uuid_str = payload.get("uuid") or ""
    order_id = payload.get("order_id") or ""
    if not uuid_str or not order_id:
        logger.warning("Cryptomus webhook: missing uuid/order_id %r", payload)
        return web.Response(status=200)

    # order_id формат: "vpn-{user_id}-{plan_key}-{ts_min}"
    # plan_key содержит underscores (vpn_base, vpn_max_3m), - только разделитель.
    parts = order_id.split("-")
    if len(parts) != 4 or parts[0] != "vpn":
        logger.warning("Cryptomus webhook: unexpected order_id format %s", order_id)
        return web.Response(status=200)
    try:
        user_id = int(parts[1])
    except ValueError:
        logger.warning("Cryptomus webhook: bad user_id in order_id %s", order_id)
        return web.Response(status=200)
    plan_key = parts[2]
    plan = VPN_PLANS.get(plan_key)
    if not plan:
        logger.warning("Cryptomus webhook: unknown plan %s (order=%s)", plan_key, order_id)
        return web.Response(status=200)

    # Сверка суммы — Cryptomus в webhook'е передаёт реальную сумму инвойса.
    # Без неё юзер может (теоретически) выписать invoice на 1₽ через свой
    # ключ, а потом запихнуть webhook. Подпись это не ловит — она просто
    # подтверждает целостность того что нам прислали.
    try:
        invoice_amount = float(payload.get("amount") or 0)
    except (TypeError, ValueError):
        invoice_amount = 0.0
    fiat = (payload.get("currency") or "").upper()
    expected = float(plan["rub"]) if fiat == "RUB" else float(plan["usd"]) if fiat == "USD" else None
    if expected is None or invoice_amount + 1e-6 < expected:
        logger.error(
            "Cryptomus webhook: amount mismatch order=%s got=%.2f%s expected=%.2f — REJECTED",
            order_id, invoice_amount, fiat, expected or 0,
        )
        return web.Response(status=400)

    payment_id = f"cryptomus_{uuid_str}"

    from services.database import (
        get_subscription_by_payment_id, create_subscription, create_order, complete_order,
    )

    existing = await get_subscription_by_payment_id(payment_id)
    if existing:
        logger.warning("Cryptomus: duplicate payment %s", payment_id)
        return web.Response(status=200)

    # Renew-from-grace: shared с другими платёжками.
    from services.grace import try_renew_from_grace
    bot: Bot = request.app["bot"]
    if await try_renew_from_grace(
        bot, user_id, plan_key, plan, payment_id, method="cryptomus",
        amount_rub=int(float(plan.get("rub", 0))),
    ):
        return web.Response(status=200)

    expires_at = datetime.utcnow() + timedelta(days=plan["duration_days"])
    sub_id = await create_subscription(
        user_id=user_id,
        plan=plan_key,
        payment_id=payment_id,
        stars_paid=0,
        amount_rub=int(float(plan.get("rub", 0))),
        expires_at=expires_at,
    )
    if sub_id is None:
        logger.warning("Cryptomus: payment %s TOCTOU-duplicate, ignored", payment_id)
        return web.Response(status=200)

    order_db_id = await create_order(
        user_id=user_id, product_type="vpn", plan=plan_key,
        stars_paid=0, expires_at=expires_at,
    )
    await complete_order(order_db_id, payment_id=payment_id)

    # bot уже извлечён выше (для try_renew_from_grace).
    try:
        from handlers.vpn import provision_vpn_slots_async, maybe_award_referral_bonus
        delivered, total = await provision_vpn_slots_async(
            bot, user_id, sub_id, plan, plan_key,
        )
        # Referral bonus (если есть). Атомарный CLAIM защищает от double-award
        # при дубль-webhook'ах (Lava/CryptoBot/Cryptomus любят ретраить).
        await maybe_award_referral_bonus(bot, user_id, sub_id)
    except Exception as e:
        logger.error("Cryptomus: provision crashed user=%d sub=%d: %s",
                     user_id, sub_id, e, exc_info=True)
        delivered, total = 0, plan["awg_slots"] + plan["vless_slots"] + plan.get("wg_slots", 0)

    if total > 0 and delivered == 0:
        logger.error(
            "Cryptomus provision FAILED 0/%d user=%d sub=%d uuid=%s — ADMIN ALERT",
            total, user_id, sub_id, uuid_str,
        )
        try:
            if ADMIN_ID:
                await bot.send_message(
                    ADMIN_ID,
                    f"🚨 <b>Cryptomus provision FAIL</b>\n\n"
                    f"User: <code>{user_id}</code>\n"
                    f"Sub: #{sub_id}\n"
                    f"Plan: {plan_key}\n"
                    f"Cryptomus UUID: <code>{uuid_str}</code>\n\n"
                    f"Action: досоздай конфиги вручную или сделай refund в Cryptomus dashboard.",
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.error("Cryptomus admin alert failed: %s", e, exc_info=True)
        try:
            await bot.send_message(
                user_id,
                "❌ <b>VPN-конфиги не создались</b>\n\n"
                "Оплата прошла, но сервера временно недоступны. "
                "Поддержка уже уведомлена — подключим вручную или вернём средства.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return web.Response(status=200)

    # Успех — уведомление юзеру
    try:
        await bot.send_message(
            user_id,
            f"✅ <b>VPN {plan['name']} оплачен!</b>\n\n"
            f"📅 Действует до: <b>{expires_at.strftime('%d.%m.%Y')}</b>\n\n"
            f"Конфиги доступны в Mini App.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("Cryptomus: failed to notify user %d: %s", user_id, e, exc_info=True)

    return web.Response(status=200)


# ── Lava.top хендлеры (карты + СБП + recurring подписка) ──────────────────────
# Auth: X-Api-Key. Email используется как primary identifier (нет custom payload).
# Recurring: первая оплата создаёт sub с parent_contract_id; продления приходят
# webhook'ами subscription.recurring.payment.success — продлеваем existing sub.

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def _parse_user_id_from_email(email: str) -> int | None:
    """email формата tg-{user_id}@maxvpnesim.com — fallback identifier когда мы
    создавали invoice без реального email. Возвращает None если email не наш."""
    if not email.startswith("tg-"):
        return None
    rest = email.split("@", 1)[0][3:]
    try:
        return int(rest)
    except ValueError:
        return None


async def handle_lavatop_invoice(request: web.Request) -> web.Response:
    """
    POST /api/vpn/invoice/lavatop  { plan_key, email }
    Создаёт Lava-инвойс. Email обязателен — Lava им идентифицирует юзера.
    Возвращает { pay_url }.
    """
    ip = _client_ip(request)
    if not _rate_limit_check_evict(_lavatop_rate, ip, _time.monotonic(), window=6.0):
        return web.json_response({"error": "rate_limited"}, status=429)
    if not LAVATOP_ENABLED:
        return web.json_response({"error": "Lava.top не подключён"}, status=503)

    body = await request.json()
    user = _resolve_user(request, body)
    if user is None:
        return _unauthorized()

    plan_key = body.get("plan_key", "")
    plan = VPN_PLANS.get(plan_key)
    if not plan:
        return web.json_response({"error": "Unknown plan"}, status=400)

    # Lava один offer_id поддерживает все 4 периодичности (MONTHLY /
    # PERIOD_90_DAYS / PERIOD_180_DAYS / PERIOD_YEAR). Базовый offer_id
    # маппится по корню plan_key (vpn_base / vpn_max) — суффикс _3m/_6m/_12m
    # определяет periodicity, передаваемое в API.
    from services.lavatop import periodicity_for_plan_key
    base_key = plan_key
    for suf in ("_3m", "_6m", "_12m"):
        if plan_key.endswith(suf):
            base_key = plan_key[:-len(suf)]
            break
    offer_id = LAVATOP_OFFERS.get(base_key)
    periodicity = periodicity_for_plan_key(plan_key)
    if not offer_id:
        return web.json_response(
            {"error": f"Lava offer_id для плана {plan_key} не настроен"}, status=503,
        )

    # Email опционален в нашем UI — Lava его требует API'но, но не валидирует
    # на доставку, только на формат публичного TLD. Если юзер ничего не передал,
    # генерим tg-{id}@maxvpnesim.com (наш реальный домен) — Lava принимает.
    # По email в webhook парсим user_id обратно (_parse_user_id_from_email).
    # Раньше использовали @maxvpnesim.local — Lava отбила как невалидный TLD.
    raw_email = (body.get("email") or "").strip().lower()
    if raw_email and _EMAIL_RE.match(raw_email):
        email = raw_email  # юзер сам ввёл — используем (для receipt'а Lava)
    else:
        email = f"tg-{user['id']}@maxvpnesim.com"

    existing_sub = await get_active_subscription(user["id"])
    # Триал — не платная подписка, юзер должен иметь возможность купить
    # обычный тариф. Триал-пиры закроются в provision_vpn_slots_async /
    # _deliver_vpn после успешного платежа (см. _close_trial_on_paid_purchase).
    if existing_sub and existing_sub.get("plan") != "vpn_trial":
        return web.json_response(
            {"error": "У тебя уже есть активная подписка. Используй смену тарифа."},
            status=400,
        )

    # Сохраняем email юзера — пригодится для recurring webhook'ов
    # (если parent_contract_id не нашли — fallback по email).
    try:
        from services.database import set_user_email
        await set_user_email(user["id"], email)
    except Exception as e:
        logger.warning("Lava: set_user_email failed user=%d: %s", user["id"], e, exc_info=True)

    from services.lavatop import create_invoice as _lava_create, LavaError
    try:
        resp = await _lava_create(
            api_key=LAVATOP_API_KEY,
            email=email,
            offer_id=offer_id,
            currency="RUB",
            buyer_language="RU",
            periodicity=periodicity,
        )
    except LavaError as e:
        logger.warning("Lava invoice rejected: status=%d msg=%s", e.status, e.lava_message)
        # Маппим типичные Lava-ошибки на user-friendly RU тексты
        msg = e.lava_message.lower()
        if "incorrect email" in msg or "self" in msg:
            user_msg = "Lava не разрешает покупать у себя — введи другой email."
        elif "email" in msg:
            user_msg = "Email отклонён платёжной системой — попробуй другой."
        else:
            user_msg = "Платёжная система отклонила запрос. Попробуй другой email или метод оплаты."
        return web.json_response({"error": user_msg}, status=400)
    except Exception as e:
        logger.error("Lava invoice error: %s", e, exc_info=True)
        return web.json_response({"error": "Ошибка платёжного сервиса"}, status=503)

    pay_url = resp.get("paymentUrl") or ""
    if not pay_url:
        logger.error("Lava: empty paymentUrl in response %r", resp)
        return web.json_response({"error": "Ошибка платёжного сервиса"}, status=503)

    logger.info(
        "Lava invoice: user=%s plan=%s email=%s contract=%s",
        user.get("id"), plan_key, email, resp.get("id"),
    )
    return web.json_response({"pay_url": pay_url, "contract_id": resp.get("id")})


async def handle_lavatop_webhook(request: web.Request) -> web.Response:
    """
    POST /api/lavatop/webhook
    Lava.top уведомляет: payment.success / payment.failed /
    subscription.recurring.payment.success / subscription.recurring.payment.failed /
    subscription.cancelled.

    Auth: X-Api-Key header (тот же что для исходящих запросов, либо отдельный
    LAVATOP_WEBHOOK_KEY).
    Идемпотентность: contractId + UNIQUE(payment_id) для первой оплаты;
    recurring продления коррелируем по parent_contract_id.
    """
    if not LAVATOP_ENABLED:
        return web.Response(status=200)

    incoming_key = request.headers.get("X-Api-Key", "")
    from services.lavatop import verify_webhook_key
    if not verify_webhook_key(incoming_key, LAVATOP_WEBHOOK_KEY):
        logger.warning("Lava webhook: BAD X-Api-Key from %s", request.remote)
        return web.Response(status=401)

    try:
        payload = await request.json()
    except Exception:
        logger.warning("Lava webhook: invalid JSON body")
        return web.Response(status=400)
    if not isinstance(payload, dict):
        return web.Response(status=400)

    event = (payload.get("eventType") or "").lower()
    contract_id = payload.get("contractId") or ""
    parent_id   = payload.get("parentContractId") or ""
    email       = ((payload.get("buyer") or {}).get("email") or "").strip().lower()
    amount      = float(payload.get("amount") or 0)
    currency    = (payload.get("currency") or "").upper()
    status      = (payload.get("status") or "").lower()

    logger.info(
        "Lava webhook: event=%s status=%s contract=%s parent=%s email=%s amount=%.2f%s",
        event, status, contract_id, parent_id, email, amount, currency,
    )

    bot: Bot = request.app["bot"]

    # ── 1. Cancel: подписка остановлена (из Lava-кабинета или нашего API) ───
    if event == "subscription.cancelled":
        # parent_id = id первого контракта серии; ищем по нему sub-row
        from services.database import get_subscription_by_parent_contract, disable_auto_renew
        sub = await get_subscription_by_parent_contract(parent_id or contract_id)
        if not sub:
            logger.warning("Lava cancel: sub not found for contract=%s parent=%s",
                           contract_id, parent_id)
            return web.Response(status=200)
        await disable_auto_renew(sub["id"])
        will_expire = payload.get("willExpireAt") or sub.get("expires_at") or ""
        try:
            await bot.send_message(
                sub["user_id"],
                "❎ <b>Автопродление отключено</b>\n\n"
                f"VPN продолжит работать до <b>{will_expire[:10]}</b>.\n"
                "Чтобы вернуть автопродление — оформи новую подписку.",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("Lava cancel notify failed user=%d: %s", sub["user_id"], e, exc_info=True)
        return web.Response(status=200)

    # ── 2. Recurring продление (success) ────────────────────────────────────
    if event == "subscription.recurring.payment.success":
        from services.database import (
            get_subscription_by_parent_contract, extend_subscription_expires_at,
            is_payment_recorded, record_payment,
        )
        sub = await get_subscription_by_parent_contract(parent_id or contract_id)
        if not sub:
            logger.error("Lava recurring success: sub not found parent=%s contract=%s",
                         parent_id, contract_id)
            return web.Response(status=200)
        plan = VPN_PLANS.get(sub["plan"])
        if not plan:
            logger.error("Lava recurring: unknown plan %s sub=%d", sub["plan"], sub["id"])
            return web.Response(status=200)

        # Sanity: amount должна совпадать с plan.rub ± 10% (audit 17.05 #7).
        # Без проверки Lava-misconfig или mock event мог бы экстендить
        # подписку любого плана при любом amount.
        plan_rub = float(plan.get("rub", 0))
        if plan_rub > 0 and (amount < plan_rub * 0.9 or amount > plan_rub * 1.5):
            logger.error(
                "Lava recurring: amount mismatch sub=%d expected=%.2f got=%.2f",
                sub["id"], plan_rub, amount,
            )
            return web.Response(status=200)

        # Idempotency на recurring contract_id (per-charge). Audit 17.05 #1:
        # без записи в payments дважды экстендили sub на 30 дней.
        recurring_tx_id = f"lavatop_recur_{contract_id}"
        if await is_payment_recorded(recurring_tx_id):
            logger.warning("Lava recurring duplicate %s ignored", recurring_tx_id)
            return web.Response(status=200)
        # record_payment FIRST — atomic UNIQUE gate.
        inserted = await record_payment(
            user_id=sub["user_id"], subscription_id=sub["id"],
            method="lavatop", stars=0,
            amount_usd=amount,  # сохраняем как usd для аналитики, фактически ₽
            tx_id=recurring_tx_id,
        )
        if not inserted:
            logger.warning("Lava recurring race-duplicate %s ignored", recurring_tx_id)
            return web.Response(status=200)

        # Продлеваем от max(now, current_expires_at) + duration — если был
        # grace и юзер просрочил, expires_at в прошлом → продлеваем от now,
        # иначе от старого expires_at (не теряем неиспользованные дни).
        try:
            cur_expires = datetime.fromisoformat(sub.get("expires_at") or datetime.utcnow().isoformat())
        except Exception:
            cur_expires = datetime.utcnow()
        base = max(cur_expires, datetime.utcnow())
        new_expires = base + timedelta(days=plan["duration_days"])
        await extend_subscription_expires_at(sub["id"], new_expires.isoformat())
        try:
            await bot.send_message(
                sub["user_id"],
                f"🔁 <b>Подписка продлена автоматически</b>\n\n"
                f"VPN {plan['name']} активен до <b>{new_expires.strftime('%d.%m.%Y')}</b>.",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("Lava recurring notify failed user=%d: %s", sub["user_id"], e, exc_info=True)
        return web.Response(status=200)

    # ── 3. Recurring неудача (нет денег и т.д.) ─────────────────────────────
    if event == "subscription.recurring.payment.failed":
        from services.database import get_subscription_by_parent_contract
        sub = await get_subscription_by_parent_contract(parent_id or contract_id)
        if sub:
            try:
                await bot.send_message(
                    sub["user_id"],
                    "⚠️ <b>Не удалось продлить подписку</b>\n\n"
                    "Lava не смогла списать оплату с твоей карты. "
                    "Lava попробует ещё раз через сутки. Если не получится — VPN перейдёт "
                    "в режим медленной скорости (256 кбит/с) на 14 дней.\n\n"
                    "Проверь баланс карты или оплати вручную через меню.",
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning("Lava recurring fail notify err user=%d: %s", sub["user_id"], e, exc_info=True)
        return web.Response(status=200)

    # ── 4. Первая оплата (payment.success) ─────────────────────────────────
    if event != "payment.success":
        # payment.failed, unknown events — лог + 200, чтобы Lava не ретраила
        logger.info("Lava webhook: ignoring event=%s", event)
        return web.Response(status=200)

    # Идентифицируем юзера: сначала по email (синтетический tg-{id}@maxvpnesim.com
    # или сохранённый реальный), потом fallback на поиск по users.email.
    user_id = _parse_user_id_from_email(email)
    if user_id is None and email:
        # Реальный email — ищем юзера в БД
        from services.database import get_user_id_by_email
        user_id = await get_user_id_by_email(email)
    if user_id is None:
        logger.error("Lava webhook: cannot resolve user from email=%s contract=%s",
                     email, contract_id)
        return web.Response(status=200)

    # Определяем plan по сумме (Lava не передаёт offer_id в webhook;
    # амаунт сверяем с плановой ценой → план найден).
    plan_key = None
    for pk, plan_def in VPN_PLANS.items():
        if abs(float(plan_def.get("rub", 0)) - amount) < 0.5:
            plan_key = pk
            break
    if plan_key is None:
        logger.error("Lava webhook: cannot match plan by amount=%.2f%s contract=%s",
                     amount, currency, contract_id)
        return web.Response(status=200)
    plan = VPN_PLANS[plan_key]

    payment_id = f"lavatop_{contract_id}"

    from services.database import (
        get_subscription_by_payment_id, create_subscription, create_order, complete_order,
    )
    existing = await get_subscription_by_payment_id(payment_id)
    if existing:
        logger.warning("Lava: duplicate payment %s", payment_id)
        return web.Response(status=200)

    # Renew-from-grace: первый платёж по новому Lava-контракту, но у юзера
    # есть grace-sub того же плана.  Продлеваем существующую (не создаём
    # новую с parent_contract_id — это нюанс: при следующей покупке
    # автопродления Lava сам подключит контракт через email).
    from services.grace import try_renew_from_grace
    if await try_renew_from_grace(
        bot, user_id, plan_key, plan, payment_id, method="lavatop",
        amount_rub=int(round(amount)),
    ):
        return web.Response(status=200)

    expires_at = datetime.utcnow() + timedelta(days=plan["duration_days"])
    is_subscription = status == "subscription-active"
    sub_id = await create_subscription(
        user_id=user_id,
        plan=plan_key,
        payment_id=payment_id,
        stars_paid=0,
        amount_rub=int(round(amount)),
        expires_at=expires_at,
        parent_contract_id=contract_id if is_subscription else None,
        auto_renew=is_subscription,
        payment_provider="lavatop",
    )
    if sub_id is None:
        logger.warning("Lava: payment %s TOCTOU-duplicate, ignored", payment_id)
        return web.Response(status=200)

    order_db_id = await create_order(
        user_id=user_id, product_type="vpn", plan=plan_key,
        stars_paid=0, expires_at=expires_at,
    )
    await complete_order(order_db_id, payment_id=payment_id)

    try:
        from handlers.vpn import provision_vpn_slots_async, maybe_award_referral_bonus
        delivered, total = await provision_vpn_slots_async(
            bot, user_id, sub_id, plan, plan_key,
        )
        # Referral bonus (если есть). Атомарный CLAIM защищает от double-award
        # при дубль-webhook'ах (Lava/CryptoBot/Cryptomus любят ретраить).
        await maybe_award_referral_bonus(bot, user_id, sub_id)
    except Exception as e:
        logger.error("Lava: provision crashed user=%d sub=%d: %s",
                     user_id, sub_id, e, exc_info=True)
        delivered, total = 0, plan["awg_slots"] + plan["vless_slots"] + plan.get("wg_slots", 0)

    if total > 0 and delivered == 0:
        logger.error(
            "Lava provision FAILED 0/%d user=%d sub=%d contract=%s — ADMIN ALERT",
            total, user_id, sub_id, contract_id,
        )
        try:
            if ADMIN_ID:
                await bot.send_message(
                    ADMIN_ID,
                    f"🚨 <b>Lava provision FAIL</b>\n\n"
                    f"User: <code>{user_id}</code>\n"
                    f"Sub: #{sub_id}\n"
                    f"Plan: {plan_key}\n"
                    f"Contract: <code>{contract_id}</code>\n\n"
                    f"Action: досоздай конфиги вручную или сделай refund в Lava-кабинете.",
                    parse_mode="HTML",
                )
        except Exception:
            pass
        try:
            await bot.send_message(
                user_id,
                "❌ <b>VPN-конфиги не создались</b>\n\n"
                "Оплата прошла, но сервера временно недоступны. "
                "Поддержка уже уведомлена — подключим вручную или вернём средства.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return web.Response(status=200)

    renew_note = ("\n\n🔁 Автопродление включено — продляется автоматически "
                  "каждый месяц. Отменить можно в разделе VPN.") if is_subscription else ""
    try:
        await bot.send_message(
            user_id,
            f"✅ <b>VPN {plan['name']} оплачен!</b>\n\n"
            f"📅 Действует до: <b>{expires_at.strftime('%d.%m.%Y')}</b>"
            f"{renew_note}\n\n"
            f"Конфиги доступны в Mini App.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("Lava: failed to notify user %d: %s", user_id, e, exc_info=True)

    return web.Response(status=200)


# ── eSIM хендлеры ──────────────────────────────────────────────────────────────

async def handle_esim_countries(request: web.Request) -> web.Response:
    countries = await esim.get_countries()
    return web.json_response(countries)


async def handle_esim_packages(request: web.Request) -> web.Response:
    country = request.rel_url.query.get("country", "")
    if not country:
        return web.json_response({"error": "country required"}, status=400)
    packages = await esim.get_packages_for(country.upper())
    return web.json_response(packages)


async def handle_esim_invoice(request: web.Request) -> web.Response:
    # Rate-limit: каждый eSIM invoice = вызов esimaccess API (rate-limited).
    ip = _client_ip(request)
    if not _rate_limit_check_evict(_invoice_rate, ip, _time.monotonic(), window=6.0):
        return web.json_response({"error": "rate_limited"}, status=429)
    body = await request.json()
    user = _resolve_user(request, body)
    if user is None:
        return _unauthorized()

    pkg_code = body.get("package_code", "")
    if not pkg_code:
        return web.json_response({"error": "Invalid params"}, status=400)

    pkg = await esim.find_package(pkg_code)
    if not pkg:
        return web.json_response({"error": "Package not found"}, status=404)

    price = pkg.get("price", 0)
    stars = esim.stars_for(price)
    name  = body.get("name") or pkg.get("name", "eSIM")

    bot: Bot = request.app["bot"]
    payload = f"esim:{pkg_code}:{price}"
    url = await bot.create_invoice_link(
        title=name,
        description=f"eSIM: {name}. Активация при первом подключении.",
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice(label=name, amount=stars)],
        provider_token="",
    )
    logger.info("eSIM invoice: user=%s pkg=%s stars=%d rub=%d", user.get("id"), pkg_code, stars, pkg.get("priceRub", 0))
    return web.json_response({"invoice_url": url})


# ── eSIM webhook (esimaccess → /api/esim/webhook) ─────────────────────────────

async def handle_esim_webhook(request: web.Request) -> web.Response:
    """esimaccess.com шлёт сюда уведомления о готовности eSIM, статусах и
    низком балансе. Зарегистрировать URL у них через esim_api.set_webhook()
    или в их веб-кабинете.

    Ожидаемые типы (notifyType):
      ORDER_STATUS  — заказ готов, профили аллоцированы (главный триггер!)
      ESIM_STATUS   — обновление статуса eSIM (DOWNLOADED / ENABLED / DELETED)
      SMDP_EVENT    — события на стороне SM-DP+ сервера
      LOW_BALANCE   — баланс упал ниже 25% или 10%
    """
    # Sec audit H4 (15.05): ESIM_WEBHOOK_SECRET ОБЯЗАТЕЛЕН в проде. Раньше
    # пустой секрет тихо отключал auth → любой мог POST'ить fake ORDER_STATUS.
    if not ESIM_WEBHOOK_SECRET:
        if not DEBUG:
            logger.error("eSIM webhook: ESIM_WEBHOOK_SECRET не задан в prod — отклоняем")
            return web.json_response({"error": "webhook auth not configured"}, status=503)
        # В DEBUG разрешаем (для локальных тестов)
    else:
        import hmac as _hmac_lib
        incoming = request.headers.get("X-Api-Key", "") or request.headers.get("Authorization", "").removeprefix("Bearer ")
        # Constant-time compare защищает от timing-attack на secret.
        if not _hmac_lib.compare_digest(incoming.encode(), ESIM_WEBHOOK_SECRET.encode()):
            logger.warning("eSIM webhook: bad secret from %s", request.remote)
            return web.json_response({"error": "unauthorized"}, status=401)

    from services.database import (
        get_esim_by_order_no, fulfill_esim_profile, get_esim_by_tran_no,
    )
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "bad JSON"}, status=400)

    notify_type = data.get("notifyType") or ""
    content     = data.get("content") or {}
    logger.info("eSIM webhook: type=%s content_keys=%s", notify_type, list(content.keys())[:10])

    bot: Bot = request.app["bot"]

    if notify_type == "ORDER_STATUS":
        order_no = content.get("orderNo") or data.get("orderNo")
        if not order_no:
            return web.json_response({"ok": True})
        profile = await get_esim_by_order_no(order_no)
        if not profile:
            logger.warning("eSIM webhook: profile for order_no=%s not found", order_no)
            return web.json_response({"ok": True})
        try:
            resp = await esim.query_by_order_no(order_no)
        except Exception as e:
            logger.error("eSIM webhook: query failed for %s: %s", order_no, e, exc_info=True)
            return web.json_response({"ok": True})
        esim_list = (resp.get("obj") or {}).get("esimList") or []
        if not esim_list:
            return web.json_response({"ok": True})
        if await fulfill_esim_profile(profile["id"], esim_list[0]):
            from handlers.vpn import deliver_esim_to_user
            await deliver_esim_to_user(bot, profile["id"])

    elif notify_type == "LOW_BALANCE":
        from config import ADMIN_ID
        if ADMIN_ID:
            level = content.get("level") or "?"
            balance = content.get("balance", 0)
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"⚠️ <b>eSIM low balance</b>\nLevel: {level}\nBalance: {balance / 10000:.2f} USD",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    # ESIM_STATUS / SMDP_EVENT — пока просто логируем для аналитики.
    return web.json_response({"ok": True})


async def handle_my_esims(request: web.Request) -> web.Response:
    """GET /api/esim/my — список eSIM-профилей пользователя для Mini App."""
    from services.database import get_user_esim_profiles
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    profiles = await get_user_esim_profiles(user["id"])
    out = []
    for p in profiles:
        used = p.get("used_volume") or 0
        total = p.get("total_volume") or 0
        out.append({
            "id":            p["id"],
            "status":        p["status"],          # pending / ready / failed
            "packageName":   p.get("package_name", "eSIM"),
            "locationCode":  p.get("location_code"),
            "iccid":         p.get("iccid"),
            "ac":            p.get("ac"),
            "qrUrl":         p.get("qr_url"),
            "shortUrl":      p.get("short_url"),
            "smdpAddress":   p.get("smdp_address"),
            "matchingId":    p.get("matching_id"),
            "usedBytes":     used,
            "totalBytes":    total,
            "usedPct":       round(100 * used / total, 1) if total else 0,
            "expireAt":      p.get("expire_at"),
            "lastSyncAt":    p.get("last_sync_at"),
            "createdAt":     p.get("created_at"),
        })
    return web.json_response(out)


async def handle_vpn_subscription(request: web.Request) -> web.Response:
    """GET /api/vpn/subscription — активная подписка пользователя."""
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    from datetime import datetime
    from services.database import get_or_create_sub_token, get_active_vless_configs_for_user

    # sub_url выдаём ВСЕМ юзерам у которых хотя бы один VLESS-конфиг есть.
    # Это persistent URL — Happ обновляет его раз в 12 ч и подхватывает
    # новые серверы / migrations grace inbound автоматически.
    async def _sub_url_for(uid: int) -> str | None:
        vcfgs = await get_active_vless_configs_for_user(uid)
        if not vcfgs:
            return None
        tok = await get_or_create_sub_token(uid)
        # WEBAPP_URL может быть GitHub Pages URL мини-аппа — это не наш домен
        # для sub-endpoint.  Sub-host явный, без env-флага не определить.
        sub_host = SUB_URL_BASE or "https://maxvpnesim.com"
        return f"{sub_host.rstrip('/')}/sub/{tok}"

    sub = await get_active_subscription(user["id"])
    if sub is None:
        expired = await get_last_expired_subscription(user["id"])
        if expired is None:
            return web.json_response(None)
        return web.json_response({
            "id":             expired["id"],
            "plan":           expired["plan"],
            "stars_paid":     expired["stars_paid"],
            "expires_at":     expired["expires_at"],
            "pending_plan":   None,
            "days_remaining": 0,
            "status":         "expired",
            "sub_url":        await _sub_url_for(user["id"]),
        })

    expires = datetime.fromisoformat(sub["expires_at"])
    now = datetime.utcnow()
    remaining_days = max(0, (expires - now).days)

    # Grace-период: подписка истекла, но 14 дней работает на 256 кбит/с.
    # UI должен показать баннер «Подписка истекла, осталось N дней» + CTA продлить.
    is_grace = sub.get("status") == "grace"
    grace_days_left = 0
    if is_grace and sub.get("grace_until"):
        grace_until = datetime.fromisoformat(sub["grace_until"])
        grace_days_left = max(0, (grace_until - now).days)

    return web.json_response({
        "id":              sub["id"],
        "plan":            sub["plan"],
        "stars_paid":      sub["stars_paid"],
        "expires_at":      sub["expires_at"],
        "pending_plan":    sub["pending_plan"],
        "days_remaining":  remaining_days,
        "status":          "grace" if is_grace else "active",
        "grace_until":     sub.get("grace_until"),
        "grace_days_left": grace_days_left,
        "sub_url":         await _sub_url_for(user["id"]),
        # Lava recurring: показываем юзеру статус автопродления и даём отменить
        "auto_renew":      bool(sub.get("auto_renew")),
        "payment_provider": sub.get("payment_provider"),
        "parent_contract_id": sub.get("parent_contract_id"),
    })


async def handle_cancel_renewal(request: web.Request) -> web.Response:
    """POST /api/vpn/subscription/cancel-renewal — выключает автопродление
    Lava recurring подписки. Существующий период дослужит до expires_at.
    """
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    sub = await get_active_subscription(user["id"])
    if sub is None or not sub.get("parent_contract_id"):
        return web.json_response({"error": "Нет активной recurring-подписки"}, status=400)
    if not sub.get("auto_renew"):
        return web.json_response({"ok": True, "already_cancelled": True})

    contract_id = sub["parent_contract_id"]
    # Сначала пытаемся отменить на стороне Lava — без этого она продолжит
    # списания. Если Lava вернёт ошибку — всё равно ставим у себя auto_renew=0
    # (юзер увидит «отменено» в UI), но логируем.
    from services.lavatop import cancel_subscription as _lava_cancel
    from services.database import disable_auto_renew
    ok = False
    if LAVATOP_ENABLED:
        try:
            ok = await _lava_cancel(api_key=LAVATOP_API_KEY, contract_id=contract_id)
        except Exception as e:
            logger.error("Lava cancel exception sub=%d: %s", sub["id"], e, exc_info=True)
    else:
        logger.warning("cancel-renewal: LAVATOP_ENABLED=false sub=%d", sub["id"])
    await disable_auto_renew(sub["id"])

    if not ok and LAVATOP_ENABLED:
        # Lava вернула ошибку — webhook subscription.cancelled может не прийти.
        # Алертим админа: возможна ситуация когда Lava продолжит списания пока
        # ручной отмены не произойдёт в их кабинете.
        logger.error(
            "Lava cancel API FAILED sub=%d contract=%s — manual cancel in Lava dashboard required",
            sub["id"], contract_id,
        )
        try:
            if ADMIN_ID:
                bot: Bot = request.app["bot"]
                await bot.send_message(
                    ADMIN_ID,
                    f"⚠️ <b>Lava cancel API failed</b>\n\n"
                    f"User: <code>{user['id']}</code>\n"
                    f"Sub: #{sub['id']}\n"
                    f"Contract: <code>{contract_id}</code>\n\n"
                    f"Отмени вручную в Lava-кабинете чтобы не списалось повторно.",
                    parse_mode="HTML",
                )
        except Exception:
            pass

    return web.json_response({"ok": True, "lava_cancel_ok": ok})


async def handle_vpn_trial_status(request: web.Request) -> web.Response:
    """GET /api/vpn/trial — eligible: можно ли юзеру взять триал.
    duration_days — 3 или 7 (для referred-юзеров). UI должен показать
    правильное число в CTA «получить триал» (отдельная мотивация для тех
    кто пришёл по реферальной ссылке)."""
    from services.trial import can_claim_trial, trial_days_for
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()
    days = await trial_days_for(user["id"])
    return web.json_response({
        "eligible":      await can_claim_trial(user["id"]),
        "duration_days": days,
    })


async def handle_vpn_trial_claim(request: web.Request) -> web.Response:
    """POST /api/vpn/trial/claim — выдать бесплатный триал."""
    from services.trial import (
        provision_trial,
        TrialAlreadyClaimed,
        TrialBlockedByActiveSub,
        TrialNoServer,
    )
    from services.vpnctl_client import VpnctlError

    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    # Rate-limit: каждый claim = provision на агенте, спам = DoS на VPN-сервер.
    # 60 сек / юзер: легитимный clamер кликает раз, реальный спам отрезается.
    if not _rate_limit_check_evict(_trial_rate, str(user["id"]), _time.monotonic(), window=60.0):
        return web.json_response({"error": "rate_limited"}, status=429)

    try:
        result = await provision_trial(user["id"])
    except TrialBlockedByActiveSub:
        return web.json_response(
            {"error": "active_subscription",
             "message": "У тебя уже активная подписка."},
            status=409,
        )
    except TrialAlreadyClaimed:
        return web.json_response(
            {"error": "already_claimed",
             "message": "Пробный период уже использован."},
            status=409,
        )
    except TrialNoServer:
        return web.json_response(
            {"error": "no_server",
             "message": "Серверы временно недоступны, попробуй позже."},
            status=503,
        )
    except VpnctlError as e:
        logger.warning("trial provision failed: %s", e, exc_info=True)
        return web.json_response(
            {"error": "provision_failed",
             "message": "Не удалось создать конфиг. Попробуй позже."},
            status=500,
        )

    # Дублируем URL в чат с ботом — Mini App success-баннер хорош, но юзеру
    # нужно куда-то скопировать ссылку, и чат естественнее.
    try:
        bot: Bot = request.app["bot"]
        expires_str = result["expires_at"].strftime("%d.%m.%Y %H:%M")
        has_awg = bool(result.get("awg_config"))

        if has_awg:
            msg = (
                f"🎁 <b>Trial на {result['duration_days']} дня активирован</b>\n\n"
                f"📅 До: <b>{expires_str}</b>\n"
                f"🚀 Скорость: 60 Mbps (как на тарифе База)\n\n"
                f"<b>1) AmneziaWG</b> — главный обфускатор, работает на МТС\n"
                f"   Открой Configs (📁 в Mini App) → скачай AWG-конфиг\n\n"
                f"<b>2) VLESS Subscription URL</b> (для Happ / V2Box):\n"
                f"<code>{result['sub_url']}</code>\n\n"
                f"📖 Инструкция: /howto\n"
                f"💎 После trial — выбери постоянный тариф в /start"
            )
        else:
            msg = (
                f"🎁 <b>Trial на {result['duration_days']} дня активирован</b>\n\n"
                f"📅 До: <b>{expires_str}</b>\n"
                f"🚀 Скорость: 60 Mbps\n\n"
                f"<b>Subscription URL</b> (импортируй в Happ один раз):\n"
                f"<code>{result['sub_url']}</code>\n\n"
                f"📖 Инструкция: /howto\n"
                f"💎 После trial — выбери постоянный тариф в /start"
            )
        await bot.send_message(user["id"], msg, parse_mode="HTML")
    except Exception as e:
        logger.warning("trial notify failed for user=%d: %s", user["id"], e, exc_info=True)

    return web.json_response({
        "sub_id":         result["sub_id"],
        "sub_url":        result["sub_url"],
        "awg_config_id":  result.get("awg_config_id"),
        "has_awg":        bool(result.get("awg_config")),
        "expires_at":     result["expires_at"].isoformat(),
        "duration_days":  result["duration_days"],
    })


async def handle_vpn_change_plan(request: web.Request) -> web.Response:
    """
    POST /api/vpn/subscription/change { plan_key }
    Апгрейд  → возвращает { invoice_url }
    Даунгрейд → возвращает { ok: true, scheduled: true }
    Отмена даунгрейда → возвращает { ok: true, cancelled: true }
    """
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    body     = await request.json()
    plan_key = body.get("plan_key", "")
    new_plan = VPN_PLANS.get(plan_key)
    if not new_plan:
        return web.json_response({"error": "Неизвестный тариф"}, status=400)

    sub = await get_active_subscription(user["id"])
    if sub is None:
        return web.json_response({"error": "Нет активной подписки"}, status=400)

    cur_plan = VPN_PLANS.get(sub["plan"])
    if cur_plan is None:
        return web.json_response({"error": "Ошибка: текущий тариф не распознан"}, status=400)

    if plan_key == sub["plan"]:
        return web.json_response({"ok": True, "same": True})

    from datetime import datetime
    expires       = datetime.fromisoformat(sub["expires_at"])
    remaining_days = max(0, (expires - datetime.utcnow()).days)

    # Сравниваем per-day цену, не абсолютные stars — иначе multi-period
    # планы (1m/3m/6m/12m, commit 5fab925) классифицируются неправильно:
    # vpn_base_12m (1525⭐ / 365 дн = 4.2⭐/day) vs vpn_max (450⭐ / 30 = 15⭐/day)
    # — naive compare `1525 > 450` дал бы «downgrade», на деле upgrade.
    # Audit 17.05 #Y1.
    cur_per_day = cur_plan["stars"] / max(1, cur_plan.get("duration_days", 30))
    new_per_day = new_plan["stars"] / max(1, new_plan.get("duration_days", 30))
    is_upgrade = new_per_day > cur_per_day

    # Rate-limit: только для upgrade (создаёт CryptoBot invoice — стоит денег
    # и медленный). Downgrade/cancel — только DB UPDATE, бесплатно и быстро,
    # 60-сек window раньше блокировал отмену «случайного» downgrade'а сразу
    # после клика (юзер 17.05 поймал это).
    if is_upgrade and not _rate_limit_check_evict(
        _change_rate, str(user["id"]), _time.monotonic(), window=10.0,
    ):
        return web.json_response({"error": "rate_limited"}, status=429)

    if is_upgrade:
        from math import ceil as _ceil
        cur_rub = int(cur_plan.get("rub", cur_plan["stars"]))
        new_rub = int(new_plan.get("rub", new_plan["stars"]))

        # Pricing зависит от статуса sub:
        # - Active: pro-rated delta `(new - cur) × remaining_days / 30`.
        #   Юзер платит за «улучшение оставшегося периода».
        # - Grace: full new-plan цена.  Юзер уже в просрочке (плата за
        #   старый период истекла), upgrade = новый период с 0.  Раньше
        #   `remaining_days=0` → rub_price=1 → юзер платил 1₽ и получал
        #   30 дней нового плана. Audit 17.05 #4.
        if sub.get("status") == "grace":
            rub_price = new_rub
            upgrade_desc = f"Подписка «{new_plan['name']}»"
        else:
            rub_price = max(1, _ceil((new_rub - cur_rub) * remaining_days / 30))
            upgrade_desc = f"Апгрейд до «{new_plan['name']}». Доплата за {remaining_days} дн."

        awg_delta   = new_plan["awg_slots"]   - cur_plan["awg_slots"]
        vless_delta = new_plan["vless_slots"] - cur_plan["vless_slots"]
        wg_delta    = new_plan.get("wg_slots", 0) - cur_plan.get("wg_slots", 0)

        if not CRYPTOBOT_TOKEN:
            return web.json_response({"error": "Оплата апгрейда временно недоступна"}, status=503)

        from services.cryptobot import create_invoice
        bot: Bot = request.app["bot"]
        bot_info = await bot.get_me()
        payload  = f"plan_upgrade:{sub['id']}:{plan_key}:{awg_delta}:{vless_delta}:{wg_delta}"

        try:
            invoice = await create_invoice(
                CRYPTOBOT_TOKEN,
                fiat="RUB",
                amount=str(rub_price),
                payload=payload,
                description=upgrade_desc,
                bot_username=bot_info.username,
            )
        except Exception as e:
            logger.error("CryptoBot upgrade invoice error: %s", e, exc_info=True)
            return web.json_response({"error": "Ошибка платёжного сервиса"}, status=503)

        pay_url = invoice.get("mini_app_invoice_url") or invoice.get("bot_invoice_url", "")
        return web.json_response({"invoice_url": pay_url})

    else:
        # Даунгрейд — планируем на следующий месяц
        # Если уже запланирован тот же — отменяем
        if sub.get("pending_plan") == plan_key:
            await schedule_plan_change(sub["id"], None)
            return web.json_response({"ok": True, "cancelled": True})

        await schedule_plan_change(sub["id"], plan_key)
        return web.json_response({"ok": True, "scheduled": True})


# ── Subscription URL для VPN-клиентов ──────────────────────────────────────────

async def handle_user_subscription(request: web.Request) -> web.Response:
    """GET /sub/{token} — возвращает base64-encoded список vless URL клиента.
    Happ / Streisand / sing-box обновляют его в фоне, поэтому при throttle
    или смене UUID юзер автоматически получает свежие конфиги.

    Подписочные HTTP-заголовки (Profile-Title, Subscription-Userinfo)
    дают клиенту красивый заголовок с трафиком и датой истечения —
    как у Outline/StealthSurf и других платных провайдеров."""
    from datetime import datetime
    from services.database import (
        get_user_by_sub_token, get_active_vless_configs_for_user
    )
    import aiosqlite
    from services.database import DB_PATH

    # Rate limit: публичный endpoint без auth. Защита от brute-force token'а
    # (32+ chars entropy, но без лимита нельзя — лог-флуд + DDoS).
    # Happ/Streisand тянут URL раз в 12 часов (Profile-Update-Interval) →
    # 6 сек/IP rate-limit с запасом.
    ip = _client_ip(request)
    now = _time.monotonic()
    if not _rate_limit_check_evict(_sub_rate, ip, now, window=6.0):
        return web.Response(text="rate limited", status=429)

    token = request.match_info.get("token", "").strip()
    if not token or len(token) < 16:
        return web.Response(text="invalid", status=400)

    user = await get_user_by_sub_token(token)
    if not user:
        return web.Response(text="not found", status=404)

    configs = await get_active_vless_configs_for_user(user["id"])
    urls = [c["config_data"] for c in configs if c.get("config_data")]

    # Plain base64-encoded vless:// list. Universal формат поддерживаемый
    # всеми VLESS-клиентами (Happ, Streisand, V2Box, sing-box).
    # Smart routing был snatched: iOS архитектура не даёт реально обходить
    # VPN-туннель для отдельных сайтов из стандартных клиентов (NetworkExt
    # sandbox + WireGuardKit limitations). Full tunnel = universally
    # работает. Для Сбер/Yandex юзер выключает VPN на 1 минуту.
    body_text = "\n".join(urls)
    encoded = base64.b64encode(body_text.encode("utf-8")).decode("ascii")

    # Edge audit H1: если у юзера нет active/grace конфигов (post-grace expiry),
    # вернуть НЕ пустоту, а явный «expired» header. Иначе Happ показывает
    # «0 серверов» и юзер думает что подписка отвалилась раньше времени.
    if not urls:
        import time as _t
        return web.Response(
            text="",
            headers={
                "Content-Type":           "text/plain; charset=utf-8",
                "Cache-Control":          "no-cache, no-store, must-revalidate",
                "Subscription-Userinfo":  f"download=0; upload=0; total=1; expire={int(_t.time()) - 1}",
                "Profile-Update-Interval": "12",
                "Profile-Title":          "❌ MAX VPN — подписка истекла",
                "Profile-Web-Page-Url":   "https://t.me/maxvpnesim_bot",
                "Support-Url":            "https://t.me/maxvpnesim_bot",
            },
        )

    # ── Build Subscription-Userinfo header ───────────────────────────────────
    # Найдём активную подписку юзера + лимиты её плана + использованный трафик
    used_bytes = 0
    total_bytes = 0
    expire_unix = 0
    plan_name = "MAX VPN"
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            sub = await (await db.execute(
                """SELECT id, plan, expires_at FROM subscriptions
                   WHERE user_id=? AND status='active'
                   ORDER BY id DESC LIMIT 1""",
                (user["id"],),
            )).fetchone()
            if sub:
                plan = VPN_PLANS.get(sub["plan"], {})
                # vpn_trial отсутствует в VPN_PLANS (нечего покупать), но как
                # активная подписка в Happ-Profile-Title должен выглядеть
                # узнаваемо, а не как «VPN» (default).
                if sub["plan"] == "vpn_trial":
                    plan_name = "Пробный 🎁"
                else:
                    plan_name = plan.get("name", "VPN")
                cap_gb = plan.get("soft_cap_gb")
                if cap_gb:
                    total_bytes = int(cap_gb) * 1024 ** 3
                # Cумма трафика по конфигам подписки
                row = await (await db.execute(
                    """SELECT COALESCE(SUM(rx_bytes),0)+COALESCE(SUM(tx_bytes),0) AS used
                       FROM configs WHERE subscription_id=? AND status='active'""",
                    (sub["id"],),
                )).fetchone()
                if row:
                    used_bytes = int(row["used"] or 0)
                # expire_at
                try:
                    exp = sub["expires_at"]
                    if exp:
                        # формат может быть "2026-05-28 21:00:58" или ISO
                        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                                    "%Y-%m-%d %H:%M:%S"):
                            try:
                                expire_unix = int(datetime.strptime(exp, fmt).timestamp())
                                break
                            except ValueError:
                                continue
                except Exception:
                    pass
    except Exception as e:
        logger.warning("subscription header build failed: %s", e, exc_info=True)

    # download = upload + общий используемый объём (Happ показывает download)
    sub_userinfo_parts = [f"download={used_bytes}", "upload=0"]
    if total_bytes > 0:
        sub_userinfo_parts.append(f"total={total_bytes}")
    if expire_unix > 0:
        sub_userinfo_parts.append(f"expire={expire_unix}")
    sub_userinfo = "; ".join(sub_userinfo_parts)

    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Subscription-Userinfo": sub_userinfo,
        "Profile-Update-Interval": "12",
        "Profile-Title": f"🌐 MAX VPN · {plan_name}",
        "Profile-Web-Page-Url": "https://t.me/maxvpnesim_bot",
        "Support-Url": "https://t.me/maxvpnesim_bot",
    }
    return web.Response(text=encoded, headers=headers)


# ── Статистика пользователя ────────────────────────────────────────────────────

async def handle_user_stats(request: web.Request) -> web.Response:
    user = _resolve_user(request)
    if not user:
        return web.json_response({"error": "Unauthorized"}, status=401)

    uid = user["id"]
    from services.database import DB_PATH
    import aiosqlite as _sq
    async with _sq.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(stars_paid),0) FROM subscriptions WHERE user_id=?", (uid,)
        ) as cur:
            stars_spent = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COALESCE(ref_bonus_days,0) FROM users WHERE id=?", (uid,)
        ) as cur:
            row = await cur.fetchone()
            bonus_days = row[0] if row else 0
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE referred_by=?", (uid,)
        ) as cur:
            invited = (await cur.fetchone())[0]
        # converted — сколько из приглашённых реально оформили подписку.
        # Используется на Home banner: «3 пригласил · 1 уже оформил».
        async with db.execute(
            """SELECT COUNT(DISTINCT u.id) FROM users u
               JOIN subscriptions s ON s.user_id=u.id
               WHERE u.referred_by=? AND s.status IN ('active','expired','grace')""",
            (uid,),
        ) as cur:
            converted = (await cur.fetchone())[0]

    return web.json_response({
        "stars_spent": stars_spent,
        "bonus_days":  bonus_days,
        "invited":     invited,
        "converted":   converted,
    })


# ── Реферальная программа ─────────────────────────────────────────────────────

async def handle_referral_stats(request: web.Request) -> web.Response:
    user = _resolve_user(request)
    if not user:
        return web.json_response({"error": "Unauthorized"}, status=401)

    bot: Bot = request.app["bot"]
    bot_info = await bot.get_me()
    stats = await db_get_referral_stats(user["id"])
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user['id']}"

    # Расширения для manual-redeem flow:
    # - can_refer: есть ли paid sub (только paid юзеры могут делиться ссылкой)
    # - bonus_days_pending: сколько накоплено в банке (= ref_bonus_days)
    # - has_active_sub: для UI логики «можно redeem'нуть СЕЙЧАС или нет»
    from services.database import has_active_paid_sub
    can_refer = await has_active_paid_sub(user["id"])
    has_sub = can_refer  # active paid = has redeemable target
    return web.json_response({
        "ref_link":   ref_link,
        "invited":    stats["invited"],
        "converted":  stats["converted"],
        "bonus_days": stats["bonus_days"],          # legacy display
        "bonus_days_pending": stats["bonus_days"],  # alias — это и есть pending bank
        "can_refer":  can_refer,
        "has_active_sub": has_sub,
    })


async def handle_referral_redeem(request: web.Request) -> web.Response:
    """POST /api/referral/redeem — активация bonus-дней к active sub."""
    user = _resolve_user(request)
    if not user:
        return web.json_response({"error": "Unauthorized"}, status=401)

    from services.database import redeem_referral_bonus
    result = await redeem_referral_bonus(user["id"])
    if result is None:
        # Узнаем причину для понятного error message
        from services.database import has_active_paid_sub
        if not await has_active_paid_sub(user["id"]):
            return web.json_response({"error": "no_active_sub"}, status=400)
        return web.json_response({"error": "no_bonus"}, status=400)

    # Notify юзеру в чат бота
    try:
        bot: Bot = request.app["bot"]
        new_date = result["new_expires_at"][:10]
        await bot.send_message(
            user["id"],
            f"🎁 <b>Бонусные дни активированы!</b>\n\n"
            f"Добавлено: <b>+{result['days']} дней</b>\n"
            f"Подписка действует до: <b>{new_date}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("referral redeem notify failed user=%d: %s", user["id"], e, exc_info=True)

    return web.json_response({
        "ok": True,
        "days_applied":   result["days"],
        "new_expires_at": result["new_expires_at"],
    })


# ── Поддержка ──────────────────────────────────────────────────────────────────

CATEGORY_LABELS: dict[str, str] = {
    "vpn":     "Проблема с VPN",
    "esim":    "Проблема с eSIM",
    "payment": "Вопрос по оплате",
    "other":   "Другое",
}

async def handle_support_ticket(request: web.Request) -> web.Response:
    user = _resolve_user(request)
    if not user:
        return web.json_response({"error": "Unauthorized"}, status=401)

    # Rate-limit: 10 сек / юзер.  Каждый тикет = сообщение админу в TG, спам
    # затопит чат поддержки и DB.  10 сек — успеть исправить опечатку + повторить,
    # но не флудить (30с предыдущее окно — оказалось fluently-печатающего юзера блокировало).
    if not _rate_limit_check_evict(_ticket_rate, str(user["id"]), _time.monotonic(), window=10.0):
        return web.json_response({"error": "rate_limited"}, status=429)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Bad request"}, status=400)

    category = str(body.get("category", "other"))
    message  = str(body.get("message", "")).strip()
    if not message:
        return web.json_response({"error": "Пустое сообщение"}, status=400)
    if len(message) > 2000:
        return web.json_response({"error": "Сообщение слишком длинное"}, status=400)

    ticket_id = await create_support_ticket(user["id"], category, message)

    bot: Bot = request.app["bot"]
    cat_label = CATEGORY_LABELS.get(category, category)
    username  = f"@{user['username']}" if user.get("username") else f"id:{user['id']}"
    name      = user.get("first_name") or "—"
    text = (
        f"🎫 <b>Тикет #{ticket_id}</b>\n"
        f"👤 {name} ({username})\n"
        f"📂 {cat_label}\n\n"
        f"{message}"
    )
    try:
        sent = await bot.send_message(ADMIN_ID, text, parse_mode="HTML")
        await update_ticket_admin_msg(ticket_id, sent.message_id)
    except Exception as e:
        logger.warning("Не удалось отправить тикет #%d админу: %s", ticket_id, e, exc_info=True)

    return web.json_response({"ok": True, "ticket_id": ticket_id})


# ── CORS middleware ────────────────────────────────────────────────────────────

ALLOWED_ORIGINS = {
    "https://maxvpnesim.com",
    "https://www.maxvpnesim.com",
    "https://lemonov911.github.io",
    "http://localhost:5173",
    "http://localhost:4173",
}

@web.middleware
async def cors_middleware(request: web.Request, handler):
    origin = request.headers.get("Origin", "")
    allow_origin = origin if origin in ALLOWED_ORIGINS else ""

    if request.method == "OPTIONS":
        if not allow_origin:
            return web.Response(status=403)
        return web.Response(
            status=204,
            headers={
                "Access-Control-Allow-Origin":  allow_origin,
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, X-Telegram-Init-Data",
                "Access-Control-Max-Age":       "86400",
            },
        )

    response = await handler(request)
    if allow_origin:
        response.headers["Access-Control-Allow-Origin"]  = allow_origin
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Telegram-Init-Data"
    return response


# ── Admin API (для Next.js admin панели) ──────────────────────────────────────

def _check_admin_secret(request: web.Request) -> bool:
    """Проверяет X-Admin-Secret header. Без него все admin endpoints — 403."""
    import hmac as _hmac_lib
    if not ADMIN_API_SECRET:
        return False
    incoming = request.headers.get("X-Admin-Secret", "")
    return _hmac_lib.compare_digest(incoming.encode(), ADMIN_API_SECRET.encode())


async def handle_admin_ticket_reply(request: web.Request) -> web.Response:
    """POST /api/admin/tickets/{id}/reply
    Body: { "text": "...", "close": true|false }
    Шлёт юзеру ответ от имени бота. Опционально закрывает тикет.
    """
    if not _check_admin_secret(request):
        return web.json_response({"error": "forbidden"}, status=403)

    from services.database import get_ticket_by_id, close_ticket

    ticket_id_str = request.match_info.get("id", "")
    try:
        ticket_id = int(ticket_id_str)
    except ValueError:
        return web.json_response({"error": "bad id"}, status=400)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)

    text = (body.get("text") or "").strip()
    close = bool(body.get("close", True))
    if not text:
        return web.json_response({"error": "text is required"}, status=400)
    if len(text) > 4000:
        return web.json_response({"error": "text too long (max 4000)"}, status=400)

    ticket = await get_ticket_by_id(ticket_id)
    if not ticket:
        return web.json_response({"error": "ticket not found"}, status=404)

    bot: Bot = request.app["bot"]
    msg_text = (
        f"💬 <b>Ответ от поддержки</b> (#{ticket_id})\n\n"
        f"<i>На твоё обращение:</i>\n"
        f"<blockquote>{(ticket.get('message') or '')[:300]}</blockquote>\n\n"
        f"{text}"
    )
    try:
        await bot.send_message(ticket["user_id"], msg_text, parse_mode="HTML")
    except Exception as e:
        logger.warning("admin reply to user %d failed: %s", ticket["user_id"], e, exc_info=True)
        return web.json_response({"error": f"send failed: {e}"}, status=502)

    if close:
        await close_ticket(ticket_id)

    return web.json_response({"ok": True, "closed": close})


async def handle_admin_ticket_close(request: web.Request) -> web.Response:
    """POST /api/admin/tickets/{id}/close — закрыть тикет без отправки сообщения."""
    if not _check_admin_secret(request):
        return web.json_response({"error": "forbidden"}, status=403)

    from services.database import close_ticket
    ticket_id_str = request.match_info.get("id", "")
    try:
        ticket_id = int(ticket_id_str)
    except ValueError:
        return web.json_response({"error": "bad id"}, status=400)

    await close_ticket(ticket_id)
    return web.json_response({"ok": True})


# ── Admin write-ops: extend / refund / ban ───────────────────────────────────

def _parse_path_int(request: web.Request, key: str) -> int | None:
    try:
        return int(request.match_info.get(key, ""))
    except (TypeError, ValueError):
        return None


async def handle_admin_sub_extend(request: web.Request) -> web.Response:
    """POST /api/admin/sub/{id}/extend
    Body: { "days": 7, "reason": "compensation" }
    Добавляет N дней к expires_at. Из grace возвращает в active.
    """
    if not _check_admin_secret(request):
        return web.json_response({"error": "forbidden"}, status=403)

    sub_id = _parse_path_int(request, "id")
    if sub_id is None:
        return web.json_response({"error": "bad id"}, status=400)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)

    days = body.get("days")
    if not isinstance(days, int) or not (1 <= days <= 365):
        return web.json_response({"error": "days must be int in [1, 365]"}, status=400)
    reason = (body.get("reason") or "").strip()[:200] or None

    from services.database import extend_subscription, audit_log_record
    updated = await extend_subscription(sub_id, days)
    if updated is None:
        return web.json_response({"error": "sub not found"}, status=404)

    await audit_log_record(
        admin_id=0, action="sub_extend",
        target=f"sub:{sub_id}",
        details=f"+{days}d reason={reason or '-'} new_expiry={updated['expires_at']}",
    )
    return web.json_response({"ok": True, "subscription": updated})


async def handle_admin_sub_refund(request: web.Request) -> web.Response:
    """POST /api/admin/sub/{id}/refund
    Body: { "reason": "...", "stars_refund": true|false }
    Помечает подписку refunded.  Если stars_refund=true и платёж был Stars —
    дополнительно вызывает refund_star_payment у Telegram (необратимо).
    """
    if not _check_admin_secret(request):
        return web.json_response({"error": "forbidden"}, status=403)

    sub_id = _parse_path_int(request, "id")
    if sub_id is None:
        return web.json_response({"error": "bad id"}, status=400)

    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = (body.get("reason") or "").strip()[:200] or None
    do_stars_refund = bool(body.get("stars_refund", False))

    from services.database import (
        get_subscription_by_id, mark_subscription_refunded,
        is_payment_refunded, mark_payment_refunded,
        rollback_referral_bonus, audit_log_record,
    )
    sub = await get_subscription_by_id(sub_id)
    if not sub:
        return web.json_response({"error": "sub not found"}, status=404)

    # Получаем payment_id отдельным запросом — get_subscription_by_id его не возвращает.
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT payment_id FROM subscriptions WHERE id=?", (sub_id,),
        ) as cur:
            row = await cur.fetchone()
            payment_id = row[0] if row else None

    # Определяем provider по префиксу payment_id.  Stars = всё что НЕ
    # crypto_/cryptomus_/lavatop_/free_.  Раньше `not startswith(crypto_,
    # free_)` ошибочно классифицировало `cryptomus_` и `lavatop_` как Stars
    # → bot.refund_star_payment получал чужой charge_id → Telegram 400.
    _NON_STARS_PREFIXES = ("crypto_", "cryptomus_", "lavatop_", "free_")
    is_stars = payment_id and not payment_id.startswith(_NON_STARS_PREFIXES)
    stars_refund_done = False

    if do_stars_refund and is_stars and payment_id:
        if await is_payment_refunded(payment_id):
            stars_refund_done = True  # уже было
        else:
            bot: Bot = request.app["bot"]
            try:
                await bot.refund_star_payment(sub["user_id"], payment_id)
                await mark_payment_refunded(payment_id)
                stars_refund_done = True
            except Exception as e:
                logger.error("admin Stars refund failed sub=%d charge=%s: %s",
                              sub_id, payment_id, e, exc_info=True)
                return web.json_response(
                    {"error": f"Stars refund failed: {e}"}, status=502,
                )

    await mark_subscription_refunded(sub_id)
    # Откат реф-бонуса если он был начислен на эту подписку
    await rollback_referral_bonus(sub_id)

    # Источник платежа — для audit log + UI чтобы админ видел корректный канал.
    payment_source = "stars"
    if payment_id:
        if payment_id.startswith("cryptomus_"): payment_source = "cryptomus"
        elif payment_id.startswith("lavatop_"): payment_source = "lavatop"
        elif payment_id.startswith("crypto_"): payment_source = "cryptobot"
        elif payment_id.startswith("free_"): payment_source = "free"

    await audit_log_record(
        admin_id=0, action="sub_refund",
        target=f"sub:{sub_id}",
        details=f"user={sub['user_id']} method={payment_source} stars_refund={stars_refund_done} reason={reason or '-'}",
    )
    return web.json_response({
        "ok": True,
        "stars_refund_done": stars_refund_done,
        "payment_source": payment_source,
        # backwards-compat: was_crypto был только CryptoBot. Если фронт
        # ориентируется на этот флаг — он по-прежнему получит ожидаемое.
        "was_crypto": payment_id and payment_id.startswith("crypto_"),
    })


async def handle_admin_user_ban(request: web.Request) -> web.Response:
    """POST /api/admin/user/{id}/ban
    Body: { "reason": "..." }
    Ставит is_banned=1.  Существующие конфиги работают до естественного expiry —
    отдельной кнопкой можно сделать refund подписки если нужно отрезать сразу.
    """
    if not _check_admin_secret(request):
        return web.json_response({"error": "forbidden"}, status=403)

    user_id = _parse_path_int(request, "id")
    if user_id is None:
        return web.json_response({"error": "bad id"}, status=400)

    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = (body.get("reason") or "").strip()[:200] or None

    from services.database import set_user_banned, audit_log_record
    ok = await set_user_banned(user_id, banned=True, reason=reason)
    if not ok:
        return web.json_response({"error": "user not found"}, status=404)

    await audit_log_record(
        admin_id=0, action="user_ban",
        target=f"user:{user_id}",
        details=f"reason={reason or '-'}",
    )
    return web.json_response({"ok": True})


async def handle_admin_vless_backfill(request: web.Request) -> web.Response:
    """POST /api/admin/servers/{id}/backfill-vless
    Multi-location backfill: для нового VLESS-сервера провижит пиры всех
    активных слотов (которые сейчас только на других серверах). Slot UUID
    переиспользуется, юзер видит новую локацию в Happ-дропдауне без
    переимпорта подписки. Идемпотентна — повторный запуск пропустит уже-
    реплицированные слоты.
    """
    if not _check_admin_secret(request):
        return web.json_response({"error": "forbidden"}, status=403)

    server_id = _parse_path_int(request, "id")
    if server_id is None:
        return web.json_response({"error": "bad id"}, status=400)

    from services.database import (
        get_server_by_id, get_vless_slots_missing_from_server,
        create_config_record, save_peer_to_config, update_server_peer_count,
        audit_log_record,
    )
    from services.vpnctl_client import provision_peer, VpnctlError
    from services.plans import vless_service_for_plan
    from urllib.parse import quote as _q

    server = await get_server_by_id(server_id)
    if not server:
        return web.json_response({"error": "server not found"}, status=404)
    if (server.get("protocol") or "") != "vless":
        return web.json_response({"error": "not a vless server"}, status=400)
    if not server.get("is_active"):
        return web.json_response({"error": "server is drained (is_active=0)"}, status=400)
    if not server.get("agent_url") or not server.get("agent_token"):
        return web.json_response({"error": "server has no agent configured"}, status=400)

    slots = await get_vless_slots_missing_from_server(server_id)
    scanned = len(slots)
    created = 0
    failed = 0
    failures: list[dict] = []

    loc = " ".join(filter(None, [
        (server.get("flag") or "").strip(),
        (server.get("city") or server.get("name") or "").strip(),
    ])).strip() or f"Server {server_id}"

    for slot in slots:
        sub_id = slot["subscription_id"]
        user_id = slot["user_id"]
        uuid_ = slot["vless_uuid"]
        plan = slot["plan"] or "vpn_base"
        sub_status = slot["sub_status"]

        # Grace-подписки сидят в vless-grace inbound (256 kbps).  При backfill
        # на новый сервер пир должен попасть туда же, иначе grace-юзер получит
        # full-speed на одной локации.
        service = "vless-grace" if sub_status == "grace" else vless_service_for_plan(plan)
        flag_compact = (server.get("flag") or "").replace(" ", "")
        label = f"u{user_id}_v_{flag_compact or server_id}"

        try:
            peer = await provision_peer(server, label, service, peer_id=uuid_)
            cfg_data = peer.config or ""
            if cfg_data.startswith("vless://"):
                base = cfg_data.split("#", 1)[0]
                cfg_data = f"{base}#{_q(loc, safe='')}"
            config_id = await create_config_record(
                sub_id, user_id, protocol="vless", server_id=server_id,
            )
            await save_peer_to_config(
                config_id, server_id, peer.id,
                "", cfg_data, label, vless_uuid=uuid_,
            )
            await update_server_peer_count(server_id, +1)
            created += 1
        except VpnctlError as e:
            logger.warning(
                "vless backfill failed server=%d sub=%d uuid=%s: %s",
                server_id, sub_id, uuid_, e,
            )
            failed += 1
            if len(failures) < 10:
                failures.append({"sub_id": sub_id, "error": str(e)[:200]})
        except Exception as e:
            logger.error(
                "vless backfill error server=%d sub=%d uuid=%s: %s",
                server_id, sub_id, uuid_, e, exc_info=True,
            )
            failed += 1
            if len(failures) < 10:
                failures.append({"sub_id": sub_id, "error": str(e)[:200]})

    await audit_log_record(
        admin_id=0, action="vless_backfill",
        target=f"server:{server_id}",
        details=f"scanned={scanned} created={created} failed={failed}",
    )

    return web.json_response({
        "ok": True,
        "scanned": scanned,
        "created": created,
        "failed": failed,
        "failures": failures,
    })


async def handle_admin_user_unban(request: web.Request) -> web.Response:
    """POST /api/admin/user/{id}/unban — снимает бан."""
    if not _check_admin_secret(request):
        return web.json_response({"error": "forbidden"}, status=403)

    user_id = _parse_path_int(request, "id")
    if user_id is None:
        return web.json_response({"error": "bad id"}, status=400)

    from services.database import set_user_banned, audit_log_record
    ok = await set_user_banned(user_id, banned=False)
    if not ok:
        return web.json_response({"error": "user not found"}, status=404)

    await audit_log_record(
        admin_id=0, action="user_unban",
        target=f"user:{user_id}",
    )
    return web.json_response({"ok": True})


# ── Фабрика приложения ─────────────────────────────────────────────────────────

async def handle_health(request: web.Request) -> web.Response:
    """Public health-check + версия. Используется monitoring'ом и manual debug
    «какая версия катится сейчас». No auth — для внешних probes."""
    try:
        from bot import BOT_VERSION
    except Exception:
        BOT_VERSION = "unknown"
    return web.json_response({
        "status": "ok",
        "version": BOT_VERSION,
        "service": "vpnbot",
        "features": {
            "esim":      SHOW_ESIM,
            "cryptobot": bool(CRYPTOBOT_TOKEN),
            "cryptomus": CRYPTOMUS_ENABLED,
            "lavatop":   LAVATOP_ENABLED,
        },
    })


def create_api_app(bot: Bot) -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app["bot"] = bot

    # Health-check — public, для monitoring + version probe
    app.router.add_get ("/api/health",                     handle_health)

    # VPN
    app.router.add_post("/api/vpn/invoice",                handle_vpn_invoice)
    app.router.add_post("/api/vpn/invoice/crypto",         handle_cryptobot_invoice)
    app.router.add_get ("/api/vpn/configs",                handle_vpn_configs)
    app.router.add_get ("/api/vpn/servers",                handle_vpn_servers)
    app.router.add_get ("/api/vpn/status",                 handle_vpn_status)
    # Public — no auth, for /status page
    app.router.add_get ("/api/status",                     handle_public_status)
    app.router.add_get ("/api/status/incidents",           handle_public_incidents)
    # Admin (Next.js админка проксирует с X-Admin-Secret header)
    app.router.add_post("/api/admin/tickets/{id}/reply",   handle_admin_ticket_reply)
    app.router.add_post("/api/admin/tickets/{id}/close",   handle_admin_ticket_close)
    app.router.add_post("/api/admin/sub/{id}/extend",      handle_admin_sub_extend)
    app.router.add_post("/api/admin/sub/{id}/refund",      handle_admin_sub_refund)
    app.router.add_post("/api/admin/user/{id}/ban",        handle_admin_user_ban)
    app.router.add_post("/api/admin/user/{id}/unban",      handle_admin_user_unban)
    app.router.add_post("/api/admin/servers/{id}/backfill-vless", handle_admin_vless_backfill)
    app.router.add_get ("/api/vpn/config/{id}/download",   handle_vpn_config_download)
    app.router.add_get ("/api/vpn/config/{id}/qr",        handle_vpn_config_qr)
    app.router.add_post("/api/vpn/config/{id}/activate",   handle_vpn_config_activate)
    app.router.add_post("/api/vpn/config/{id}/revoke",     handle_vpn_config_revoke)
    app.router.add_get ("/api/vpn/subscription",           handle_vpn_subscription)
    app.router.add_post("/api/vpn/subscription/change",    handle_vpn_change_plan)
    app.router.add_get ("/api/vpn/trial",                  handle_vpn_trial_status)
    app.router.add_post("/api/vpn/trial/claim",            handle_vpn_trial_claim)
    # Subscription URL для VPN-клиентов (Happ/Streisand): один URL — все его vless-конфиги
    app.router.add_get ("/sub/{token}",                    handle_user_subscription)

    # CryptoBot webhook
    app.router.add_post("/api/cryptobot/webhook",          handle_cryptobot_webhook)

    # Cryptomus — endpoint'ы регистрируем всегда (упрощает frontend
    # logic), но они отдают 503 пока CRYPTOMUS_ENABLED=false.
    app.router.add_post("/api/vpn/invoice/cryptomus",      handle_cryptomus_invoice)
    app.router.add_post("/api/cryptomus/webhook",          handle_cryptomus_webhook)

    # Lava.top — карты/СБП + recurring подписка. То же — endpoint'ы всегда
    # зарегистрированы, без LAVATOP_ENABLED отдают 503.
    app.router.add_post("/api/vpn/invoice/lavatop",        handle_lavatop_invoice)
    app.router.add_post("/api/lavatop/webhook",            handle_lavatop_webhook)
    app.router.add_post("/api/vpn/subscription/cancel-renewal", handle_cancel_renewal)

    # eSIM — гарды по SHOW_ESIM. Webhook оставляем зарегистрированным
    # потому что esimaccess может слать notifications для уже-проданных
    # eSIM (юзеры купившие до выключения флага). Catalog/invoice/my
    # выключаем — фронт всё равно их не показывает, но любопытные могут
    # дёргать через curl.
    if SHOW_ESIM:
        app.router.add_get ("/api/esim/countries",         handle_esim_countries)
        app.router.add_get ("/api/esim/packages",          handle_esim_packages)
        app.router.add_post("/api/esim/invoice",           handle_esim_invoice)
        app.router.add_get ("/api/esim/my",                handle_my_esims)
    app.router.add_post("/api/esim/webhook",               handle_esim_webhook)

    # Поддержка
    app.router.add_post("/api/support/ticket",             handle_support_ticket)

    # Статистика пользователя
    app.router.add_get ("/api/user/stats",                 handle_user_stats)

    # Реферальная программа
    app.router.add_get ("/api/referral/stats",             handle_referral_stats)
    app.router.add_post("/api/referral/redeem",            handle_referral_redeem)

    return app

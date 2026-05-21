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

import asyncio
import base64
import json
import logging
import os
import re
import time as _time
from html import escape as html_escape

from aiohttp import web
from aiogram import Bot
from aiogram.types import LabeledPrice

from config import (
    DEBUG, ADMIN_ID, ADMIN_IDS, BOT_TOKEN, CRYPTOBOT_TOKEN, WEBAPP_URL,
    ESIM_WEBHOOK_SECRET, ADMIN_API_SECRET, SHOW_ESIM, SUB_URL_BASE,
    LAVATOP_API_KEY, LAVATOP_WEBHOOK_KEY, LAVATOP_ENABLED, LAVATOP_OFFERS,
    OXAPAY_API_KEY, OXAPAY_ENABLED,
)
from services.auth import verify_init_data
from services.i18n_plural import plural_ru, DAYS
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

# Background task registry — keeps strong references so GC can't collect tasks
# that are still running.  Same pattern as scheduler._spawn_bg.
_BG_TASKS: set[asyncio.Task] = set()


def _on_bg_done(task: asyncio.Task) -> None:
    """done-callback: убираем task из реестра + логируем upexpected exception.
    Без этого `task.add_done_callback(set.discard)` глотает исключения и
    диагностика «что-то не работает в фоне» превращается в шаманство.
    """
    _BG_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "bg task %s failed: %s",
            task.get_name() or "<unnamed>",
            exc,
            exc_info=exc,
        )


def _spawn_bg(coro, *, name: str | None = None) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name) if name else asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_on_bg_done)
    return task


def _safe_header(value: str) -> str:
    """Percent-encode non-ASCII chars for HTTP header values.

    RFC 7230 restricts header values to ISO-8859-1. Cyrillic + emoji aren't
    in that set: aiohttp on Python 3.12+ refuses to encode them (raises
    UnicodeEncodeError mid-response), older versions silently truncate.

    Happ/Streisand tolerate percent-encoded UTF-8 in Profile-Title and
    decode it back for display. Other clients (sing-box) just show the
    percent-encoded form, which is ugly but doesn't break the subscription.
    """
    from urllib.parse import quote as _urlquote
    return _urlquote(value.encode('utf-8'), safe=' .,/-_:()')


# Per-server lock для handle_admin_migrate_configs. Без него два параллельных
# admin-call'а на один сервер обработали бы те же configs дважды:
# - первый успел provision_peer на новом сервере + activate_config_slot
# - второй прочитал get_active_configs_for_migration ещё ДО первого UPDATE'а
#   (snapshot был старый) → второй второй раз provision'ит того же юзера,
#   создаёт duplicate peer, потом activate_config_slot overwrite'ит первый
#   результат — старая запись висит orphan на новом сервере.
# pre-check `lock.locked()` отдаёт 409 моментально вместо ожидания в очереди
# (миграция может идти минутами).
_migrate_locks: dict[int, asyncio.Lock] = {}


def _migrate_lock_for(server_id: int) -> asyncio.Lock:
    if server_id not in _migrate_locks:
        _migrate_locks[server_id] = asyncio.Lock()
    return _migrate_locks[server_id]


# Per-sub lock для CryptoBot plan_upgrade webhook (PS2). Параллельные invoice'ы
# на одну sub'у (юзер открыл апгрейд из двух окон) должны сериализоваться:
# второй webhook читает уже обновлённый sub.plan и отказывает (expected_from
# не совпадает) → админ refund'ит вручную. Без лока два webhook'а читали бы
# исходный план одновременно и оба применяли свои стейл deltas.
_upgrade_locks: dict[int, asyncio.Lock] = {}


def _upgrade_lock_for(sub_id: int) -> asyncio.Lock:
    if sub_id not in _upgrade_locks:
        _upgrade_locks[sub_id] = asyncio.Lock()
    return _upgrade_locks[sub_id]


# Тарифы — services.plans (единственный источник истины).
from services.plans import VPN_PLANS, vless_service_for_plan, plan_display_name  # noqa: F401


# ─────────────────────────────────────────────────────────────────────────────
#  Dynamic VLESS URL resolution (2026-05-21 refactor — см. database.py)
#
#  Раньше: подписка читала `configs.config_data` и отдавала уже-готовые URL —
#  один peer на конкретный сервер. Чтобы добавить новый сервер, требовалось
#  N HTTP-вызовов на провижининг UUID + N INSERT-ов в configs (по одному
#  на юзера). Хрупко.
#
#  Теперь: для VLESS URL строятся на лету из (users.vless_uuid, plan-tier,
#  servers.xray_*). Добавить сервер = одна INSERT-row в `servers` + проход
#  скриптом по существующим UUID. Никаких per-user DB-записей.
# ─────────────────────────────────────────────────────────────────────────────


async def _resolve_vless_urls(user_id: int) -> list[str]:
    """Returns vless:// URLs the user should see in /sub/{token}.

    Поведение:
      1. Если у юзера `users.vless_uuid` задан И есть ≥1 active VLESS-сервер
         с заполненным `xray_pubkey` → строим URL динамически (по одному
         per server, tier выбирается из подписки).
      2. Иначе fallback: читаем `configs.config_data` как раньше (legacy mode).
         Этот путь нужен пока backfill `users.vless_uuid` не прошёл; после
         него код всё ещё работает корректно — просто не пользуется.
    """
    import aiosqlite
    from urllib.parse import quote as _url_quote
    from services.database import (
        DB_PATH as _DB_PATH,
        active_vless_servers, get_relevant_vless_subscription,
        vless_port_column, get_active_vless_configs_for_user,
    )

    # --- Dynamic path ---
    # Берём UUID юзера; если NULL — пытаемся allocate (если есть хотя бы 1
    # backfilled VLESS-сервер). Без allocate юзер бы навсегда залип в legacy
    # mode даже после миграции — UUID создаётся только при purchase, а
    # старые юзеры с pre-2026-05 покупкой никогда туда не попадут.
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT vless_uuid FROM users WHERE id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            user_uuid = row["vless_uuid"] if row else None

    if not user_uuid:
        # Если есть хотя бы один backfilled VLESS-сервер — allocate UUID.
        # Если ни одного — fall through в legacy (configs.config_data).
        if await active_vless_servers():
            from services.database import ensure_user_vless_uuid
            user_uuid = await ensure_user_vless_uuid(user_id)

    if user_uuid:
        sub = await get_relevant_vless_subscription(user_id)
        if sub:
            # Tier по плану + grace-override (как у scheduler._current_vless_service,
            # но проще: grace всегда vless-grace, иначе — план).
            if sub["status"] == "grace":
                tier = "vless-grace"
            else:
                tier = vless_service_for_plan(sub["plan"])

            port_col = vless_port_column(tier)
            servers = await active_vless_servers()
            urls: list[str] = []
            for s in servers:
                port = s.get(port_col)
                if not port:
                    # Legacy/недонастроенный сервер без per-tier порта —
                    # fallback на base port. Например legacy-grace-сервер с
                    # xray_port_grace=NULL: лучше отдать full-speed URL
                    # (потеря tier-семантики, но юзер хотя бы получит VPN),
                    # чем silently выкинуть его из подписки.
                    # Раньше тут было `continue` → юзер видел пустой список
                    # URL без видимой причины.
                    fallback = s.get("xray_port_base") or s.get("xray_port")
                    if not fallback:
                        logger.error(
                            "_resolve_vless_urls: server %d has no %s and no base port, skipping",
                            s["id"], port_col,
                        )
                        continue
                    logger.warning(
                        "_resolve_vless_urls: server %d missing %s (tier=%s), falling back to base port %s",
                        s["id"], port_col, tier, fallback,
                    )
                    port = fallback
                # Reality REQUIRES sni — without it client TLS hello mismatches
                # server's `serverNames` and connection drops. Fail-fast at URL
                # build time (rather than silently serving a broken URL); the
                # DB filter in active_vless_servers() already drops sni-less
                # rows, so this is double protection for prod misconfig.
                sni = s.get("xray_sni") or ""
                if not sni:
                    logger.error(
                        "Skipping server %d in /sub/ — xray_sni is NULL/empty. "
                        "Reality REQUIRES sni; serving this URL would break the client.",
                        s.get("id"),
                    )
                    continue
                # vless:// URL — sni обязателен; pbk/sid тоже.
                params = [
                    "encryption=none",
                    f"security=reality",
                    f"pbk={s['xray_pubkey']}",
                    f"sid={s['xray_short_id'] or ''}",
                    f"fp={s.get('xray_fingerprint') or 'chrome'}",
                    "type=tcp",
                    "headerType=none",
                    "spx=%2F",
                    f"sni={sni}",
                ]
                # Per-tier flow= must match agent's xray_flow config (see
                # agent/main.go:77-89). Wrong flow = handshake fails.
                from services.plans import VLESS_FLOW_BY_SERVICE
                flow = VLESS_FLOW_BY_SERVICE.get(tier, "")
                if flow:
                    params.append(f"flow={flow}")
                # Fragment — название как в Happ: «🇺🇸 Charlotte»
                label = f"{s.get('flag') or '🌐'} {s.get('city') or s.get('name') or ''}".strip()
                frag = _url_quote(label, safe="")
                query = "&".join(params)
                urls.append(f"vless://{user_uuid}@{s['host']}:{port}?{query}#{frag}")
            return urls

    # --- Legacy fallback ---
    configs = await get_active_vless_configs_for_user(user_id)
    return [c["config_data"] for c in configs if c.get("config_data")]


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


async def _user_err(
    user_id: int | None,
    error_code: str,
    i18n_key: str,
    status: int,
    **fmt: object,
) -> web.Response:
    """User-facing API error → bilingual response.

    Returns JSON with:
      `error`   — machine-readable stable code (legacy callers + analytics)
      `message` — bilingual user-facing text in the user's preferred lang
                  (defaults to 'ru' when user_id is None or lang missing).

    Frontend wrapper (webapp/src/api/index.ts) prefers `message` over `error`.
    """
    from services.database import get_user_lang
    from services.i18n_bot import t as _t
    lang: str | None = None
    if user_id is not None:
        try:
            lang = await get_user_lang(user_id)
        except Exception:
            lang = None
    return web.json_response(
        {"error": error_code, "message": _t(lang, i18n_key, **fmt)},
        status=status,
    )


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
        return await _user_err(user["id"], "Unknown plan", "bot_api_err_unknown_plan", 400)

    # Блокируем покупку если уже есть активная подписка
    existing_sub = await get_active_subscription(user["id"])
    # Триал — не платная подписка, юзер должен иметь возможность купить
    # обычный тариф. Триал-пиры закроются в provision_vpn_slots_async /
    # _deliver_vpn после успешного платежа (см. _close_trial_on_paid_purchase).
    if existing_sub and existing_sub.get("plan") != "vpn_trial" and existing_sub.get("status") != "grace":
        return await _user_err(user["id"], "active_subscription", "bot_api_err_active_sub_exists", 400)

    bot: Bot = request.app["bot"]

    # Auto-renew подписка через Telegram Stars: subscription_period=2592000 (30 дней).
    # Доступно ТОЛЬКО для 1м планов (vpn_base, vpn_max без суффикса) — Telegram
    # не поддерживает другие периоды subscription'ов.
    # Multi-period (3/6/12) — всегда one-time, флаг recurring игнорируем.
    recurring = bool(body.get("recurring")) and not plan.get("multi_period")

    # F8: Telegram-caches the invoice; description must match user's lang.
    from services.database import get_user_lang as _gul_inv
    from services.i18n_bot import t as _t_inv
    _inv_lang = await _gul_inv(user["id"])
    invoice_desc = _t_inv(_inv_lang, "bot_invoice_desc_vpn", days=plan["duration_days"])

    invoice_kwargs: dict = dict(
        title=f"VPN {plan['name']}",
        description=invoice_desc,
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

    # F7: bilingual device label fallback — lookup user lang once.
    from services.database import get_user_lang as _gul_cfg
    from services.i18n_bot import t as _t_cfg
    _cfg_lang = await _gul_cfg(user["id"])

    # Форматируем трафик и убираем чувствительные поля
    result = []
    for c in configs:
        result.append({
            "id":           c["id"],
            "protocol":     c["protocol"],
            "label":        c["label"] or c["peer_name"] or _t_cfg(_cfg_lang, "bot_device_fallback", n=c["slot_num"]),
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
            # server_active: 0 = сервер auto-deactivated (health-check failed),
            # конфиг в БД active, но реально не работает.  Фронт показывает
            # warning + кнопку «пересоздать». NULL (legacy слот без server_id)
            # трактуем как active=true чтобы не пугать ложными warning'ами.
            "server_active": bool(c.get("server_active") if c.get("server_active") is not None else True),
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
_oxapay_rate:    dict[str, float] = {}  # /api/vpn/invoice/oxapay
_lavatop_rate:   dict[str, float] = {}  # /api/vpn/invoice/lavatop
_change_rate:   dict[str, float] = {}  # /api/vpn/subscription/change
_ticket_rate:   dict[str, float] = {}  # /api/support/ticket
_trial_rate:    dict[str, float] = {}  # /api/vpn/trial/claim
_admin_rate:    dict[str, float] = {}  # /api/admin/* — брутфорс-защита

async def handle_public_status(request: web.Request) -> web.Response:
    """Публичный статус всех сервисов. Без auth — для status-страницы.

    Берёт live-снимок из последней пробы (`server_health_log`) + uptime %
    за 24h/7d/30d + 24-часовой strip + последние incidents. Probes сам
    лоит scheduler каждые 60 сек в `services/health.py`.
    """
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

    if config["status"] == "active":
        return await _user_err(user["id"], "slot_already_active", "bot_api_err_slot_already_active", 400)
    if config["status"] == "activating":
        return await _user_err(user["id"], "slot_activating", "bot_api_err_slot_activating", 409)
    if config["status"] != "empty":
        return await _user_err(user["id"], "slot_bad_status", "bot_api_err_slot_bad_status", 400)

    sub = await get_active_subscription(user["id"])
    if not sub or sub["id"] != config["subscription_id"]:
        return await _user_err(user["id"], "no_active_sub", "bot_api_err_no_active_sub", 403)

    # Atomic claim — защита от race: две вкладки одновременно жмут «Добавить»
    # на одном слоте. Без claim'а обе пройдут проверку status='empty' выше,
    # обе вызовут provision_peer → два peer'а на агенте, один в БД, второй
    # orphan. Claim переводит слот в 'activating' атомарно — второй запрос
    # получит rowcount=0 и отбьётся.
    if not await claim_config_slot_for_activation(config_id):
        return await _user_err(user["id"], "slot_activating", "bot_api_err_slot_activating", 409)

    try:
        body = await request.json()
    except Exception:
        await reset_config_slot(config_id)
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    server_id = body.get("server_id") if isinstance(body, dict) else None

    # Получаем сервер из БД
    if server_id:
        server = await get_server_by_id(server_id)
        if not server or not server["is_active"]:
            await reset_config_slot(config_id)
            return await _user_err(user["id"], "server_unavailable", "bot_api_err_server_unavailable", 400)
    else:
        # Load-balance по active_peers/capacity (минимально загруженный).
        # Раньше брали servers[0] — это всегда первый по INSERT-порядку, и
        # все Mini-App-активации сыпались на один сервер пока admin не
        # переключал is_active. get_best_server уже фильтрует is_active=1 +
        # agent_url IS NOT NULL + capacity>0.
        from services.database import get_best_server
        server = await get_best_server(config["protocol"])
        if not server:
            await reset_config_slot(config_id)
            return await _user_err(user["id"], "no_servers", "bot_api_err_no_servers", 503)
        server_id = server["id"]

    if not server.get("agent_url") or not server.get("agent_token"):
        logger.error("Server %s has no agent_url/agent_token", server.get("name", server["id"]))
        await reset_config_slot(config_id)  # rollback activating → empty
        return await _user_err(user["id"], "server_no_agent", "bot_api_err_server_no_agent", 503)

    peer_name = f"tg{user['id']}_{config_id}"

    from services.vpnctl_client import provision_peer, VpnctlError
    from handlers.vpn import vless_service_for_plan

    # For VLESS, resolve speed-tier service from the subscription's plan.
    # Grace-status guard: юзер в grace активирует empty слот → если выдать
    # `vless-base` (full speed), он получит unthrottled пир пока остальные
    # его пиры throttle'нутые в vless-grace (256 кбит/с). Это speed-bypass:
    # юзер раз в 14 дней grace добавляет «свежий» слот и обходит throttle.
    # Поэтому в grace — всегда `vless-grace` (port 9453).
    sub_status = sub.get("status") or ""
    if config["protocol"] == "vless":
        if sub_status == "grace":
            service_name = "vless-grace"
        else:
            service_name = vless_service_for_plan(sub["plan"])
    else:
        service_name = config["protocol"]

    try:
        result = await provision_peer(server, peer_name, service_name)
    except VpnctlError as e:
        logger.error("Activate slot #%d on server %s: %s", config_id, server.get("name", server["id"]), e, exc_info=True)
        await reset_config_slot(config_id)  # rollback activating → empty
        return await _user_err(user["id"], "config_create_failed", "bot_api_err_config_create_failed", 503)
    except Exception as e:
        logger.error("Activate slot #%d on server %s: %s", config_id, server.get("name", server["id"]), e, exc_info=True)
        await reset_config_slot(config_id)  # rollback activating → empty
        return await _user_err(user["id"], "server_unavailable", "bot_api_err_server_unavailable", 503)

    config_data = result.config
    if not config_data:
        await reset_config_slot(config_id)  # rollback activating → empty
        return await _user_err(user["id"], "config_create_failed", "bot_api_err_config_create_failed", 503)

    peer_id = result.id
    peer_ip = (result.extra or {}).get("assigned_ip")
    wg_pubkey = peer_id if config["protocol"] == "awg" else None
    vless_uuid = peer_id if config["protocol"] == "vless" else None
    await activate_config_slot(
        config_id, peer_name, config_data, server_id,
        wg_pubkey=wg_pubkey, assigned_ip=peer_ip, vless_uuid=vless_uuid,
    )
    # Same pattern as handlers/vpn.py:_deliver_vpn — bump active_peers so
    # get_best_server load-balances correctly. Without this, a slot activated
    # via Mini App never counts toward the chosen server's load, and that
    # server keeps "winning" the load-balancer for all subsequent peers.
    from services.database import update_server_peer_count
    await update_server_peer_count(server["id"], +1)

    # AWG grace-status throttle: VLESS уже идёт в vless-grace inbound (см.
    # service_name выше), но AWG не имеет отдельного «grace inbound» —
    # throttle делается через tc-фильтр на awg0 по dst IP. Без этого
    # AWG-пиры в grace activate'нутся на full speed (256 кбит/с throttle
    # применится только при следующем scheduler tick'е grace_expired, что
    # = bypass на много часов / дней).
    if sub_status == "grace" and config["protocol"] == "awg" and peer_ip:
        try:
            from services.vpnctl_client import throttle_peer
            await throttle_peer(server, peer_name, "awg", peer_ip, kbps=256)
            logger.info(
                "Слот #%d (AWG grace): применён throttle 256 кбит/с на %s",
                config_id, peer_ip,
            )
        except Exception as e:
            logger.warning(
                "grace-activate AWG throttle failed cfg=%d ip=%s: %s",
                config_id, peer_ip, e, exc_info=True,
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
        return await _user_err(user["id"], "slot_not_active", "bot_api_err_slot_not_active", 400)

    # Удаляем пир с сервера через vpnctl (best-effort).
    # `revoke_peer(server, peer_id, service_name)` — 3-й параметр это имя
    # vpnctl-сервиса (awg / vless-base / vless-base-slow / vless-grace / ...),
    # а НЕ row-level proto. Для VLESS нужно resolve'ить через current_vless_service
    # (учитывает throttle-порт + plan_key), иначе агент возвращает 404 и пир
    # висит на сервере (active_peers рассинхронизирован).
    revoked_on_server = False
    if config.get("peer_name") and config.get("server_id"):
        try:
            srv = await get_server_by_id(config["server_id"])
            if srv:
                from services.vpnctl_client import revoke_peer
                peer_id = config.get("vless_uuid") or config.get("wg_pubkey")
                if peer_id:
                    if config["protocol"] in ("vless", "vless-reality"):
                        from services.revoke import current_vless_service
                        from services.database import get_subscription_by_id
                        sub = await get_subscription_by_id(config["subscription_id"])
                        plan_key = sub["plan"] if sub else "vpn_base"
                        svc = current_vless_service(config.get("config_data") or "", plan_key)
                        await revoke_peer(srv, str(peer_id), svc)
                    else:
                        # awg / wg — service name совпадает с протоколом
                        await revoke_peer(srv, str(peer_id), config["protocol"])
                    revoked_on_server = True
        except Exception as e:
            logger.warning("Не удалось удалить пир %s: %s", config["peer_name"], e, exc_info=True)

    # Сбрасываем слот в empty — он остаётся доступным для повторной активации
    await reset_config_slot(config_id)
    # Декремент active_peers — симметричный аналог activate-path. Только если
    # peer реально снят с сервера (иначе active_peers уйдёт в рассинхрон с
    # реальным состоянием агента; max(0, ...) внутри update_server_peer_count
    # защищает от ухода в минус).
    if revoked_on_server and config.get("server_id"):
        from services.database import update_server_peer_count
        await update_server_peer_count(config["server_id"], -1)
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
        # User not yet identified — fall back to ru-default via _user_err(None,...).
        return await _user_err(None, "cryptobot_disabled", "bot_api_err_cryptobot_disabled", 503)

    body = await request.json()
    user = _resolve_user(request, body)
    if user is None:
        return _unauthorized()

    plan = VPN_PLANS.get(body.get("plan_key", ""))
    if not plan:
        return await _user_err(user["id"], "Unknown plan", "bot_api_err_unknown_plan", 400)
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
    if existing_sub and existing_sub.get("plan") != "vpn_trial" and existing_sub.get("status") != "grace":
        return await _user_err(user["id"], "active_subscription", "bot_api_err_active_sub_exists", 400)

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
            bot_username=bot_info.username or "",
        )
    except Exception as e:
        logger.error("CryptoBot invoice error: %s", e, exc_info=True)
        return await _user_err(user["id"], "payment_service", "bot_api_err_payment_service", 503)

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
    payment_id  = f"crypto_{invoice.get('invoice_id')}"
    logger.info("CryptoBot payment: invoice_id=%s payload=%s",
                invoice.get("invoice_id"), raw_payload)

    # payload format (PS2):
    #   "vpn:USER_ID:PLAN_KEY"
    #   "plan_upgrade:SUB_ID:PLAN_KEY:EXPECTED_FROM"  (new)
    #   "plan_upgrade:SUB_ID:PLAN_KEY:AWG:VLESS[:WG]" (legacy, in-flight pre-PS2)
    parts = raw_payload.split(":")

    if parts[0] == "plan_upgrade":
        # ── CryptoBot upgrade payment ──────────────────────────────────────────
        # PS2 payload форматы:
        #   Новый: plan_upgrade:{sub_id}:{plan_key}:{expected_from}
        #     Deltas пересчитываем под per-sub lock от ТЕКУЩЕГО sub.plan.
        #   Legacy: plan_upgrade:{sub_id}:{plan_key}:{awg}:{vless}[:{wg}]
        #     In-flight invoice'ы со stale-deltas (до PS2-rollout).
        expected_from: str | None = None
        legacy_deltas: tuple[int, int, int] | None = None
        try:
            if len(parts) == 4:
                up_sub_id   = int(parts[1])
                up_plan_key = parts[2]
                expected_from = parts[3]
            elif len(parts) in (5, 6):
                up_sub_id   = int(parts[1])
                up_plan_key = parts[2]
                legacy_deltas = (
                    int(parts[3]),
                    int(parts[4]),
                    int(parts[5]) if len(parts) == 6 else 0,
                )
            else:
                logger.warning("CryptoBot webhook: malformed plan_upgrade payload %r", raw_payload)
                return web.Response(status=200)
        except ValueError:
            logger.warning("CryptoBot webhook: bad int in plan_upgrade payload %r", raw_payload)
            return web.Response(status=200)

        from services.database import (
            get_subscription_by_id, change_subscription_plan,
            record_payment as _rp, is_payment_recorded,
            get_configs_for_subscription, get_server_by_id, update_config_data,
        )
        if await is_payment_recorded(payment_id):
            logger.warning("CryptoBot plan_upgrade: already processed invoice %s", invoice.get("invoice_id"))
            return web.Response(status=200)

        # PS2: всю обработку оборачиваем в per-sub lock. Конкурентные upgrade-
        # webhook'и сериализуются — второй увидит уже обновлённый sub.plan
        # и (если expected_from не совпадает) отправит платёж админу на refund.
        async with _upgrade_lock_for(up_sub_id):
            up_sub = await get_subscription_by_id(up_sub_id)
            if not up_sub:
                logger.error("CryptoBot plan_upgrade: sub #%d not found invoice %s",
                             up_sub_id, invoice.get("invoice_id"))
                return web.Response(status=200)
            up_user_id = up_sub["user_id"]
            up_plan    = VPN_PLANS.get(up_plan_key)
            if not up_plan:
                logger.warning("CryptoBot plan_upgrade: unknown plan %r", up_plan_key)
                return web.Response(status=200)

            old_plan_key = up_sub["plan"]

            # PS2: если payload новый и план уже двинулся — alert админа.
            if expected_from is not None and up_sub["plan"] != expected_from:
                logger.error(
                    "CryptoBot upgrade race: user %d sub=%d already moved from %s to %s, "
                    "received payment for %s→%s (invoice=%s) — admin alert",
                    up_user_id, up_sub_id, expected_from, up_sub["plan"],
                    expected_from, up_plan_key, invoice.get("invoice_id"),
                )
                try:
                    if ADMIN_ID:
                        bot_alert: Bot = request.app["bot"]
                        await bot_alert.send_message(
                            ADMIN_ID,
                            f"🚨 <b>CryptoBot upgrade race</b>\n\n"
                            f"User: <code>{up_user_id}</code>\n"
                            f"Sub: #{up_sub_id}\n"
                            f"Expected plan: <code>{expected_from}</code>\n"
                            f"Current plan: <code>{up_sub['plan']}</code>\n"
                            f"Requested target: <code>{up_plan_key}</code>\n"
                            f"Invoice: <code>{invoice.get('invoice_id')}</code>\n"
                            f"Amount: {invoice.get('paid_amount', '')} {invoice.get('paid_asset', '')}\n\n"
                            "Деньги получены CryptoBot — refund через CryptoBot вручную.",
                            parse_mode="HTML",
                        )
                except Exception:
                    pass
                return web.Response(status=200)

            # Пересчитываем deltas от ТЕКУЩЕГО sub.plan (новый формат) или
            # берём baked-in (legacy формат, in-flight pre-PS2 invoice'ы).
            if legacy_deltas is not None:
                up_awg, up_vless, up_wg = legacy_deltas
            else:
                cur_plan_obj = VPN_PLANS.get(old_plan_key)
                if not cur_plan_obj:
                    logger.error(
                        "CryptoBot upgrade: unknown current plan %r for sub=%d",
                        old_plan_key, up_sub_id,
                    )
                    return web.Response(status=200)
                up_awg   = up_plan["awg_slots"]   - cur_plan_obj["awg_slots"]
                up_vless = up_plan["vless_slots"] - cur_plan_obj["vless_slots"]
                up_wg    = up_plan.get("wg_slots", 0) - cur_plan_obj.get("wg_slots", 0)

            was_grace_up = up_sub.get("status") == "grace"
            applied = await change_subscription_plan(
                up_sub_id, up_plan_key, up_user_id, up_awg, up_vless, up_wg,
                duration_days=up_plan["duration_days"],
                new_payment_id=payment_id,
            )
            if not applied:
                # PS5 status guard: sub expired/refunded mid-flight. Записываем
                # payment-row + алертим админа.
                logger.error(
                    "CryptoBot upgrade rejected by status guard: sub=%d plan=%s→%s invoice=%s",
                    up_sub_id, old_plan_key, up_plan_key, invoice.get("invoice_id"),
                )
                await _rp(user_id=up_user_id, subscription_id=up_sub_id,
                          method="crypto", tx_id=payment_id)
                try:
                    if ADMIN_ID:
                        bot_alert2: Bot = request.app["bot"]
                        await bot_alert2.send_message(
                            ADMIN_ID,
                            f"🚨 <b>CryptoBot upgrade rejected (status guard)</b>\n\n"
                            f"User: <code>{up_user_id}</code>\n"
                            f"Sub: #{up_sub_id}\n"
                            f"Plan: <code>{old_plan_key}</code> → <code>{up_plan_key}</code>\n"
                            f"Invoice: <code>{invoice.get('invoice_id')}</code>\n\n"
                            "Sub была expired/refunded к моменту оплаты — refund CryptoBot вручную.",
                            parse_mode="HTML",
                        )
                except Exception:
                    pass
                return web.Response(status=200)

            await _rp(user_id=up_user_id, subscription_id=up_sub_id, method="crypto", tx_id=payment_id)

            # PS3: если апгрейд уменьшил число слотов в каком-то протоколе —
            # отозвать лишние configs. Без этого юзер "upgrade'нул" на план
            # с меньшим набором слотов, но старые пиры продолжают работать.
            if up_awg < 0 or up_vless < 0 or up_wg < 0:
                try:
                    from services.revoke import revoke_excess_configs_on_downgrade
                    rev, fail = await revoke_excess_configs_on_downgrade(
                        up_sub_id, old_plan_key=old_plan_key, new_plan_key=up_plan_key,
                        log_prefix=f"crypto_upgrade_shrink_sub{up_sub_id}",
                    )
                    logger.info(
                        "CryptoBot upgrade with fewer slots sub=%d: revoked %d, failed %d",
                        up_sub_id, rev, fail,
                    )
                except Exception as e:
                    logger.error("CryptoBot upgrade shrink revoke sub=%d: %s",
                                 up_sub_id, e, exc_info=True)

            if was_grace_up:
                try:
                    from services.vpnctl_client import client_for_server
                    from services.plans import vless_service_for_plan
                    configs = await get_configs_for_subscription(up_sub_id)
                    for cfg in configs:
                        srv_id = cfg.get("server_id")
                        if not srv_id:
                            continue
                        server = await get_server_by_id(srv_id)
                        if not server or not server.get("agent_url"):
                            continue
                        try:
                            client = client_for_server(server)
                            proto       = cfg.get("protocol", "")
                            peer_id     = cfg.get("vless_uuid") or cfg.get("peer_name") or ""
                            assigned_ip = cfg.get("assigned_ip") or ""
                            if proto == "awg" and peer_id and assigned_ip:
                                await client.unthrottle_peer("awg", peer_id, assigned_ip)
                            elif proto in ("vless", "vless-reality") and peer_id:
                                target_svc   = vless_service_for_plan(up_plan_key)
                                normal_added = False
                                try:
                                    new_peer = await client.add_peer(
                                        target_svc, f"u{up_user_id}_c{cfg['id']}", peer_id=peer_id,
                                    )
                                    normal_added = True
                                    for _svc in ("vless-grace", "vless-base-slow", "vless-max-slow"):
                                        await client.remove_peer(_svc, peer_id)
                                except Exception:
                                    if normal_added:
                                        try:
                                            await client.remove_peer(target_svc, peer_id)
                                        except Exception:
                                            pass
                                    raise
                                if new_peer.config:
                                    await update_config_data(cfg["id"], new_peer.config)
                        except Exception as e:
                            logger.warning("CryptoBot plan_upgrade unthrottle cfg #%d: %s", cfg["id"], e)
                except Exception as e:
                    logger.error("CryptoBot plan_upgrade unthrottle outer: %s", e)

            bot_up: Bot = request.app["bot"]
            try:
                await bot_up.send_message(
                    up_user_id,
                    f"✅ <b>Тариф изменён на «{up_plan['name']}»!</b>\n\n"
                    f"💎 Оплата: {invoice.get('paid_amount', '')} {invoice.get('paid_asset', '')}\n\n"
                    "Открой <b>Мои конфиги</b> — новые слоты уже там.",
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning("CryptoBot plan_upgrade: failed to notify user %d: %s", up_user_id, e)
            return web.Response(status=200)

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

    from services.database import (
        get_subscription_by_payment_id, create_subscription,
        create_order, complete_order, create_config_record,
        is_payment_recorded,
    )
    from datetime import datetime, timedelta

    existing = await get_subscription_by_payment_id(payment_id)
    if existing:
        logger.warning("CryptoBot: duplicate payment %s", payment_id)
        return web.Response(status=200)

    # Идемпотентность: если payment уже записан в payments.tx_id через
    # grace-renew path (sub не имеет этот payment_id в subscriptions.payment_id,
    # поэтому get_subscription_by_payment_id вернул None), но повторный
    # webhook не должен создавать вторую sub.
    if await is_payment_recorded(payment_id):
        logger.warning("CryptoBot: payment %s already processed (grace-renew path), skip", payment_id)
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

    # Cross-method dedup: invoice creation проверяла active sub, но между этим
    # моментом и приходом webhook'а юзер мог завершить параллельную оплату
    # другим методом (Stars/Lava/OxaPay) → теперь sub существует. Создадим
    # вторую → 2 active row в БД, занятый слот, неправильная аналитика.
    # Crypto refund невозможен через API — алертим админа для ручного возврата.
    _racing = await get_active_subscription(user_id)
    if _racing and _racing.get("plan") != "vpn_trial" and _racing.get("status") == "active":
        logger.error(
            "CryptoBot cross-method duplicate: user=%d already has sub=%d plan=%s, "
            "received payment=%s for plan=%s — manual refund needed",
            user_id, _racing["id"], _racing.get("plan"), payment_id, plan_key,
        )
        try:
            if ADMIN_ID:
                await bot.send_message(
                    ADMIN_ID,
                    f"🚨 <b>CryptoBot dup payment</b>\n\n"
                    f"User: <code>{user_id}</code>\n"
                    f"Existing sub: #{_racing['id']} ({_racing.get('plan')})\n"
                    f"New invoice: <code>{payment_id}</code> ({plan_key})\n"
                    f"Paid: {invoice.get('paid_amount', '?')} {invoice.get('paid_asset', '')}\n\n"
                    "Нужно вернуть юзеру вручную через CryptoBot.",
                    parse_mode="HTML",
                )
        except Exception:
            pass
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
            "CryptoBot provision FAILED 0/%d user=%d sub=%d payment=%s — admin alert + sub expire",
            total, user_id, sub_id, payment_id,
        )
        # Mark sub expired so user can re-buy. Admin handles refund manually
        # (CryptoBot has no refund API — must go through their dashboard).
        from services.database import mark_subscription_expired
        try:
            await mark_subscription_expired(sub_id)
        except Exception as e:
            logger.error("CryptoBot 0/N: mark_subscription_expired sub=%d: %s",
                         sub_id, e, exc_info=True)
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
                    "Sub marked expired. Refund manually via CryptoBot dashboard.",
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.error("Admin alert failed: %s", e, exc_info=True)
        try:
            from services.database import get_user_lang as _gul_pf
            from services.i18n_bot import t as _i18n_t_pf
            _lang_pf = await _gul_pf(user_id) or "ru"
            await bot.send_message(
                user_id,
                _i18n_t_pf(_lang_pf, "bot_provision_failed"),
                parse_mode="HTML",
            )
        except Exception:
            pass
        return web.Response(status=200)

    # Happy path — full UX (AWG configs as documents + sub URL + buttons).
    try:
        from handlers.vpn import send_purchase_success_message
        await send_purchase_success_message(
            bot, user_id, sub_id, plan, plan_key, expires_at,
            delivered, total,
        )
    except Exception as e:
        logger.warning(
            "CryptoBot: send_purchase_success_message failed user=%d: %s",
            user_id, e, exc_info=True,
        )

    return web.Response(status=200)


# ── OxaPay хендлеры ───────────────────────────────────────────────────────────
# Крипто-платёжный шлюз: USDT/BTC/ETH и другие монеты, конвертация в RUB/USD.
# HMAC-SHA512 webhook через заголовок `HMAC`.  Идентификация по order_id (тот же
# формат что и у Cryptomus: vpn-{user_id}-{plan_key}-{ts_min}).


async def handle_oxapay_invoice(request: web.Request) -> web.Response:
    """
    POST /api/vpn/invoice/oxapay  { plan_key, currency: "RUB"|"USD" }
    Создаёт инвойс через OxaPay и возвращает { pay_url }.
    """
    ip = _client_ip(request)
    if not _rate_limit_check_evict(_oxapay_rate, ip, _time.monotonic(), window=6.0):
        return web.json_response({"error": "rate_limited"}, status=429)
    if not OXAPAY_ENABLED:
        return await _user_err(None, "oxapay_disabled", "bot_api_err_oxapay_disabled", 503)

    body = await request.json()
    user = _resolve_user(request, body)
    if user is None:
        return _unauthorized()

    plan_key = body.get("plan_key", "")
    plan = VPN_PLANS.get(plan_key)
    if not plan:
        return await _user_err(user["id"], "Unknown plan", "bot_api_err_unknown_plan", 400)

    currency = (body.get("currency") or "RUB").upper()
    if currency not in ("RUB", "USD"):
        return web.json_response({"error": "currency must be RUB or USD"}, status=400)

    existing_sub = await get_active_subscription(user["id"])
    if existing_sub and existing_sub.get("plan") != "vpn_trial" and existing_sub.get("status") != "grace":
        return await _user_err(user["id"], "active_subscription", "bot_api_err_active_sub_exists", 400)

    amount = float(plan["rub"] if currency == "RUB" else plan["usd"])
    ts_min  = int(_time.time() // 60)
    order_id = f"vpn-{user['id']}-{plan_key}-{ts_min}"

    api_origin = SUB_URL_BASE or "https://maxvpnesim.com"
    base_url   = WEBAPP_URL or "https://maxvpnesim.com"
    callback_url = f"{api_origin}/api/oxapay/webhook"
    return_url   = f"{base_url}/vpn"

    from services.oxapay import create_invoice as _op_create
    try:
        invoice = await _op_create(
            api_key=OXAPAY_API_KEY,
            amount=amount,
            currency=currency,
            order_id=order_id,
            callback_url=callback_url,
            return_url=return_url,
            description=f"VPN {plan['name']} 30d",
        )
    except Exception as e:
        logger.error("OxaPay invoice error: %s", e, exc_info=True)
        return await _user_err(user["id"], "payment_service", "bot_api_err_payment_service", 503)

    pay_url = invoice.get("payment_url") or ""
    if not pay_url:
        logger.error("OxaPay: no payment_url in response %r", invoice)
        return await _user_err(user["id"], "payment_service", "bot_api_err_payment_service", 503)

    logger.info(
        "OxaPay invoice: user=%s plan=%s cur=%s order=%s track_id=%s",
        user.get("id"), plan_key, currency, order_id, invoice.get("track_id"),
    )
    return web.json_response({"pay_url": pay_url})


async def handle_oxapay_webhook(request: web.Request) -> web.Response:
    """
    POST /api/oxapay/webhook
    OxaPay уведомляет об оплате.  HMAC-SHA512 передаётся в заголовке `HMAC`.
    Идемпотентность: payment_id = `oxapay_{track_id}` + UNIQUE-constraint.
    """
    if not OXAPAY_ENABLED:
        return web.Response(status=200)

    body_bytes = await request.read()
    try:
        payload = json.loads(body_bytes)
    except Exception:
        logger.warning("OxaPay webhook: invalid JSON body")
        return web.Response(status=400)
    if not isinstance(payload, dict):
        return web.Response(status=400)

    received_hmac = request.headers.get("HMAC", "")
    from services.oxapay import verify_signature as _op_verify
    if not _op_verify(body_bytes, received_hmac, OXAPAY_API_KEY):
        logger.warning(
            "OxaPay webhook: BAD signature order_id=%s status=%s from=%s",
            payload.get("order_id"), payload.get("status"), request.remote,
        )
        return web.Response(status=401)

    status = (payload.get("status") or "").lower()
    # OxaPay: "Paid" — подтверждено. "Paying" — ещё ждём подтверждений.
    if status != "paid":
        logger.info("OxaPay webhook: ignoring status=%s order_id=%s",
                    status, payload.get("order_id"))
        return web.Response(status=200)

    track_id = str(payload.get("track_id") or "")
    order_id  = str(payload.get("order_id") or "")
    if not track_id or not order_id:
        logger.warning("OxaPay webhook: missing track_id/order_id %r", payload)
        return web.Response(status=200)

    # order_id формат: "vpn-{user_id}-{plan_key}-{ts_min}"
    parts = order_id.split("-")
    if len(parts) != 4 or parts[0] != "vpn":
        logger.warning("OxaPay webhook: unexpected order_id format %s", order_id)
        return web.Response(status=200)
    try:
        user_id = int(parts[1])
    except ValueError:
        logger.warning("OxaPay webhook: bad user_id in order_id %s", order_id)
        return web.Response(status=200)
    plan_key = parts[2]
    plan = VPN_PLANS.get(plan_key)
    if not plan:
        logger.warning("OxaPay webhook: unknown plan %s (order=%s)", plan_key, order_id)
        return web.Response(status=200)

    # Сверка суммы — защита от подделки webhook'а.
    try:
        invoice_amount = float(payload.get("amount") or 0)
    except (TypeError, ValueError):
        invoice_amount = 0.0
    fiat = (payload.get("currency") or "").upper()
    expected = float(plan["rub"]) if fiat == "RUB" else float(plan["usd"]) if fiat == "USD" else None
    if expected is None or invoice_amount + 1e-6 < expected:
        logger.error(
            "OxaPay webhook: amount mismatch order=%s got=%.2f%s expected=%.2f — REJECTED",
            order_id, invoice_amount, fiat, expected or 0,
        )
        return web.Response(status=400)

    payment_id = f"oxapay_{track_id}"

    from services.database import (
        get_subscription_by_payment_id, create_subscription, create_order, complete_order,
        is_payment_recorded, record_payment as _rp_oxapay,
    )

    existing = await get_subscription_by_payment_id(payment_id)
    if existing:
        logger.warning("OxaPay: duplicate payment %s", payment_id)
        return web.Response(status=200)

    # Идемпотентность при webhook retry: grace-renew path записывает payment
    # в payments.tx_id, но не в subscriptions.payment_id — повторный вебхук
    # иначе создаст вторую sub.
    if await is_payment_recorded(payment_id):
        logger.warning("OxaPay: payment %s already processed (grace-renew path), skip", payment_id)
        return web.Response(status=200)

    from services.grace import try_renew_from_grace
    bot: Bot = request.app["bot"]
    if await try_renew_from_grace(
        bot, user_id, plan_key, plan, payment_id, method="oxapay",
        amount_rub=int(float(plan.get("rub", 0))),
    ):
        return web.Response(status=200)

    # Cross-method dedup (см. CryptoBot handler выше для контекста).
    _racing_ox = await get_active_subscription(user_id)
    if _racing_ox and _racing_ox.get("plan") != "vpn_trial" and _racing_ox.get("status") == "active":
        logger.error(
            "OxaPay cross-method duplicate: user=%d already has sub=%d plan=%s, "
            "received payment=%s for plan=%s — manual refund needed",
            user_id, _racing_ox["id"], _racing_ox.get("plan"), payment_id, plan_key,
        )
        try:
            if ADMIN_ID:
                await bot.send_message(
                    ADMIN_ID,
                    f"🚨 <b>OxaPay dup payment</b>\n\n"
                    f"User: <code>{user_id}</code>\n"
                    f"Existing sub: #{_racing_ox['id']} ({_racing_ox.get('plan')})\n"
                    f"New track: <code>{payment_id}</code> ({plan_key})\n"
                    f"Paid: {invoice_amount:.2f} {fiat}\n\n"
                    "Refund вручную через OxaPay dashboard.",
                    parse_mode="HTML",
                )
        except Exception:
            pass
        return web.Response(status=200)

    from datetime import datetime, timedelta
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
        logger.warning("OxaPay: payment %s TOCTOU-duplicate, ignored", payment_id)
        return web.Response(status=200)

    order_db_id = await create_order(
        user_id=user_id, product_type="vpn", plan=plan_key,
        stars_paid=0, expires_at=expires_at,
    )
    await complete_order(order_db_id, payment_id=payment_id)
    await _rp_oxapay(
        user_id=user_id, subscription_id=sub_id, method="oxapay", tx_id=payment_id,
        stars=0, amount_usd=float(plan.get("usd", 0)),
    )

    try:
        from handlers.vpn import provision_vpn_slots_async, maybe_award_referral_bonus
        delivered, total = await provision_vpn_slots_async(bot, user_id, sub_id, plan, plan_key)
        await maybe_award_referral_bonus(bot, user_id, sub_id)
    except Exception as e:
        logger.error("OxaPay: provision crashed user=%d sub=%d: %s",
                     user_id, sub_id, e, exc_info=True)
        delivered, total = 0, plan["awg_slots"] + plan["vless_slots"] + plan.get("wg_slots", 0)

    if total > 0 and delivered == 0:
        logger.error(
            "OxaPay provision FAILED 0/%d user=%d sub=%d track=%s — admin alert + sub expire",
            total, user_id, sub_id, track_id,
        )
        # Mark sub expired so user can re-buy. Admin handles refund manually
        # via OxaPay dashboard.
        from services.database import mark_subscription_expired
        try:
            await mark_subscription_expired(sub_id)
        except Exception as e:
            logger.error("OxaPay 0/N: mark_subscription_expired sub=%d: %s",
                         sub_id, e, exc_info=True)
        try:
            if ADMIN_ID:
                await bot.send_message(
                    ADMIN_ID,
                    f"🚨 <b>OxaPay provision FAIL</b>\n\n"
                    f"User: <code>{user_id}</code>\n"
                    f"Sub: #{sub_id}\n"
                    f"Plan: {plan_key}\n"
                    f"OxaPay track_id: <code>{track_id}</code>\n\n"
                    "Sub marked expired. Refund manually via OxaPay dashboard.",
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.error("OxaPay admin alert failed: %s", e, exc_info=True)
        try:
            from services.database import get_user_lang as _gul_pf
            from services.i18n_bot import t as _i18n_t_pf
            _lang_pf = await _gul_pf(user_id) or "ru"
            await bot.send_message(
                user_id,
                _i18n_t_pf(_lang_pf, "bot_provision_failed"),
                parse_mode="HTML",
            )
        except Exception:
            pass
        return web.Response(status=200)

    try:
        from handlers.vpn import send_purchase_success_message
        await send_purchase_success_message(
            bot, user_id, sub_id, plan, plan_key, expires_at,
            delivered, total,
        )
    except Exception as e:
        logger.warning(
            "OxaPay: send_purchase_success_message failed user=%d: %s",
            user_id, e, exc_info=True,
        )
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
        return await _user_err(None, "lava_disabled", "bot_api_err_lava_disabled", 503)

    body = await request.json()
    user = _resolve_user(request, body)
    if user is None:
        return _unauthorized()

    plan_key = body.get("plan_key", "")
    plan = VPN_PLANS.get(plan_key)
    if not plan:
        return await _user_err(user["id"], "Unknown plan", "bot_api_err_unknown_plan", 400)

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
        logger.info(
            "Lava invoice: synthetic email %s for user_id=%d (frontend did not "
            "collect email). User cannot self-service refund via Lava cabinet.",
            email, user["id"],
        )

    existing_sub = await get_active_subscription(user["id"])
    # Триал — не платная подписка, юзер должен иметь возможность купить
    # обычный тариф. Триал-пиры закроются в provision_vpn_slots_async /
    # _deliver_vpn после успешного платежа (см. _close_trial_on_paid_purchase).
    if existing_sub and existing_sub.get("plan") != "vpn_trial" and existing_sub.get("status") != "grace":
        return await _user_err(user["id"], "active_subscription", "bot_api_err_active_sub_exists", 400)

    # Сохраняем email юзера — пригодится для recurring webhook'ов
    # (если parent_contract_id не нашли — fallback по email).
    from services.database import set_user_email
    if not email.startswith(f"tg-{user['id']}@"):
        # Реальный user-provided email. Должен персистить для webhook lookup —
        # иначе recurring charge не свяжется обратно с юзером.
        try:
            await set_user_email(user["id"], email)
        except Exception as e:
            logger.error(
                "Lava invoice: set_user_email FAILED for real email %s user=%d: %s — "
                "ABORTING invoice creation",
                email, user["id"], e,
            )
            return web.json_response(
                {"error": "Failed to register email. Try again or contact support."},
                status=503,
            )
    else:
        # Synthetic email — set_user_email best-effort
        try:
            await set_user_email(user["id"], email)
        except Exception as e:
            logger.warning("Lava: set_user_email synthetic failed user=%d: %s", user["id"], e)

    from services.lavatop import create_invoice as _lava_create, LavaError
    # F10: Lava receipt и checkout-UI берут язык из buyer_language. Используем
    # users.lang (set при /start из Telegram language_code) — EN-юзеры видят
    # английский checkout, остальные — русский.
    from services.database import get_user_lang as _gul_lava
    _u_lang = await _gul_lava(user["id"])
    buyer_lang = "EN" if (_u_lang or "").lower().startswith("en") else "RU"
    try:
        resp = await _lava_create(
            api_key=LAVATOP_API_KEY,
            email=email,
            offer_id=offer_id,
            currency="RUB",
            buyer_language=buyer_lang,
            periodicity=periodicity,
        )
    except LavaError as e:
        logger.warning("Lava invoice rejected: status=%d msg=%s", e.status, e.lava_message)
        # Маппим типичные Lava-ошибки на bilingual i18n-ключи.
        from services.i18n_bot import t as _i18n_t
        msg = e.lava_message.lower()
        if "incorrect email" in msg or "self" in msg:
            user_msg = _i18n_t(_u_lang, "bot_lava_err_self_buy")
        elif "email" in msg:
            user_msg = _i18n_t(_u_lang, "bot_lava_err_email_rejected")
        else:
            user_msg = _i18n_t(_u_lang, "bot_lava_err_payment_rejected")
        return web.json_response({"error": user_msg}, status=400)
    except Exception as e:
        logger.error("Lava invoice error: %s", e, exc_info=True)
        return await _user_err(user["id"], "payment_service", "bot_api_err_payment_service", 503)

    pay_url = resp.get("paymentUrl") or ""
    if not pay_url:
        logger.error("Lava: empty paymentUrl in response %r", resp)
        return await _user_err(user["id"], "payment_service", "bot_api_err_payment_service", 503)

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
    from datetime import datetime, timedelta  # нет module-level import — нужен локально

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
            from services.database import get_user_lang as _gul_cancel
            from services.i18n_bot import t as _i18n_t_cancel
            _lang_cancel = await _gul_cancel(sub["user_id"])
            await bot.send_message(
                sub["user_id"],
                _i18n_t_cancel(
                    _lang_cancel, "bot_lava_cancelled", until=will_expire[:10],
                ),
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
        # Арифметика max()/+days делается АТОМАРНО внутри SQL — иначе два
        # параллельных webhook'а на один parent_contract_id оба прочитали бы
        # один stale expires_at и второй overwrite'ил бы первого (lost update).
        # extend_subscription_expires_at переключает status grace→active атомарно
        # и возвращает флаг — использовать его вместо pre-fetch'нутого sub.status,
        # чтобы избежать race со scheduler'ом между чтением и записью.
        # None = sub уже expired (webhook пришёл слишком поздно) — не воскрешаем.
        was_grace = await extend_subscription_expires_at(sub["id"], plan["duration_days"])

        if was_grace is None:
            # FFF3: либо sub уже expired (webhook поздний), либо юзер отменил
            # автопродление, но Lava cancel-API провалился ранее и шарж всё-таки
            # пришёл. Различаем по auto_renew_disabled_at.
            from services.database import get_subscription_by_id as _get_sub_fff3
            fresh_sub = await _get_sub_fff3(sub["id"])
            is_user_cancelled = bool(
                fresh_sub
                and fresh_sub.get("auto_renew_disabled_at")
                and not fresh_sub.get("auto_renew")
            )
            if is_user_cancelled:
                logger.error(
                    "Lava charge on CANCELLED sub #%d user=%d — Lava cancel "
                    "failed previously. Need manual refund + retry cancel.",
                    sub["id"], sub["user_id"],
                )
                try:
                    if ADMIN_ID:
                        await bot.send_message(
                            ADMIN_ID,
                            f"🚨 <b>Lava charge on cancelled sub</b>\n\n"
                            f"Sub: #{sub['id']}\n"
                            f"User: {sub['user_id']}\n"
                            f"Plan: {sub.get('plan')}\n"
                            f"Amount: {amount:.2f} {currency}\n\n"
                            f"Юзер отменил автопродление, но Lava cancel не сработал ранее. "
                            f"Refund вручную через Lava-кабинет + повторно cancel contract.",
                            parse_mode="HTML",
                        )
                except Exception:
                    pass
                # Best-effort повторный cancel — Lava должна перестать списывать.
                _parent = sub.get("parent_contract_id") or contract_id
                if LAVATOP_API_KEY and _parent:
                    try:
                        from services.lavatop import cancel_subscription as _lava_cancel_retry
                        await _lava_cancel_retry(api_key=LAVATOP_API_KEY, contract_id=_parent)
                    except Exception as e:
                        logger.warning("FFF3 lava cancel retry failed: %s", e)
                return web.Response(status=200)

            logger.error(
                "Lava recurring: sub #%d user=%d already expired — cannot extend, "
                "alerting admin (юзер заплатил, но VPN-слоты пусты)",
                sub["id"], sub["user_id"],
            )
            try:
                if ADMIN_ID:
                    await bot.send_message(
                        ADMIN_ID,
                        f"🚨 <b>Lava recurring: expired sub!</b>\n\n"
                        f"Sub #{sub['id']} user {sub['user_id']} уже expired — "
                        f"webhook пришёл слишком поздно. Нужно вручную создать новую sub.",
                        parse_mode="HTML",
                    )
            except Exception:
                pass
            return web.Response(status=200)

        # Если sub была в grace — снять throttle на агентах (AWG tc + VLESS inbound).
        if was_grace:
            from services.grace import unthrottle_sub_configs
            _spawn_bg(
                unthrottle_sub_configs(sub["id"], sub["user_id"], sub["plan"]),
                name=f"unthrottle_lava_sub{sub['id']}",
            )

        # Fetch updated expires_at для отображения юзеру (SQL посчитал max+days
        # атомарно — пересчитать в Python нельзя без race).
        from services.database import get_subscription_by_id
        updated_sub = await get_subscription_by_id(sub["id"])
        try:
            new_expires_dt = datetime.fromisoformat(
                (updated_sub.get("expires_at") if updated_sub else "") or datetime.utcnow().isoformat()
            )
        except Exception:
            new_expires_dt = datetime.utcnow() + timedelta(days=plan["duration_days"])

        # Renewal-success notice with full UX (inline buttons, sub URL).
        # AWG configs не пере-отправляем — у юзера на устройстве те же файлы,
        # они продолжают работать после продления. Sub URL включаем — юзер мог
        # потерять ссылку, а Happ продолжает синкаться по тому же токену.
        try:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
            from services.database import get_user_lang as _gul, get_or_create_sub_token
            from services.i18n_bot import t as _i18n_t

            _lang = await _gul(sub["user_id"])
            _until = new_expires_dt.strftime("%d.%m.%Y")

            # Sub URL — idempotent get-or-create (не ротируем при продлении).
            _sub_url = ""
            try:
                _tok = await get_or_create_sub_token(sub["user_id"])
                _sub_url = f"https://maxvpnesim.com/sub/{_tok}"
            except Exception as e:
                logger.warning("Lava renew: sub_token user=%d: %s", sub["user_id"], e)

            _parts = [_i18n_t(_lang, "bot_lava_renewed", plan=plan_display_name(plan, _lang or "ru"), until=_until)]
            if was_grace:
                _parts.append(_i18n_t(_lang, "bot_lava_renewed_grace"))
            if _sub_url:
                _parts.append("")
                _parts.append(_i18n_t(_lang, "bot_purchase_success_sub_url", url=_sub_url))

            _kb_rows: list[list[InlineKeyboardButton]] = []
            if WEBAPP_URL:
                _kb_rows.append([InlineKeyboardButton(
                    text=_i18n_t(_lang, "bot_btn_my_configs"),
                    web_app=WebAppInfo(url=f"{WEBAPP_URL}/configs"),
                )])
            _kb_rows.append([InlineKeyboardButton(
                text=_i18n_t(_lang, "bot_btn_howto"),
                callback_data="vpn:howto",
            )])

            await bot.send_message(
                sub["user_id"],
                "\n".join(_parts),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=_kb_rows) if _kb_rows else None,
            )
        except Exception as e:
            logger.warning("Lava recurring notify failed user=%d: %s", sub["user_id"], e, exc_info=True)
        return web.Response(status=200)

    # ── 3. Recurring неудача (нет денег и т.д.) ─────────────────────────────
    if event == "subscription.recurring.payment.failed":
        from services.database import (
            get_subscription_by_parent_contract,
            get_user_lang as _gul,
            DB_PATH as _DB_PATH,
        )
        from services.i18n_bot import t as _i18n_t
        import aiosqlite as _aiosqlite
        sub = await get_subscription_by_parent_contract(parent_id or contract_id)
        if sub:
            try:
                _lang = await _gul(sub["user_id"])
                await bot.send_message(
                    sub["user_id"],
                    _i18n_t(_lang, "bot_lava_charge_failed"),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning("Lava recurring fail notify err user=%d: %s", sub["user_id"], e, exc_info=True)
            # Persist флаг чтобы Mini App мог показать yellow warning
            # «не удалось списать с карты» рядом с auto-renew баннером.
            # Сбрасывается в NULL при успешном extend (см. extend_subscription_expires_at).
            try:
                async with _aiosqlite.connect(_DB_PATH) as db:
                    await db.execute(
                        "UPDATE subscriptions SET last_charge_failed_at=CURRENT_TIMESTAMP WHERE id=?",
                        (sub["id"],),
                    )
                    await db.commit()
            except Exception as e:
                logger.warning("Lava recurring fail flag err sub=%d: %s", sub["id"], e, exc_info=True)
        return web.Response(status=200)

    # ── 3b. Первая оплата не прошла (payment.failed) ───────────────────────
    # Карту отбили или 3DS не пройден. Юзер видел "Оплачено?" на стороне Lava
    # без подтверждения → надо явно сказать что не получилось, иначе пойдёт
    # в саппорт с "оплатил, ничего не дали".
    if event == "payment.failed":
        _maybe_user = _parse_user_id_from_email(email) if email else None
        if _maybe_user is None and email:
            from services.database import get_user_id_by_email
            _maybe_user = await get_user_id_by_email(email)
        if _maybe_user is not None:
            try:
                from services.database import get_user_lang as _gul
                from services.i18n_bot import t as _i18n_t
                _lang = await _gul(_maybe_user)
                await bot.send_message(
                    _maybe_user,
                    _i18n_t(_lang, "bot_payment_failed"),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning("Lava payment.failed notify err user=%d: %s", _maybe_user, e)
        return web.Response(status=200)

    # ── 4. Первая оплата (payment.success) ─────────────────────────────────
    if event != "payment.success":
        # unknown events — лог + 200, чтобы Lava не ретраила
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
        record_payment as _rp_lava, is_payment_recorded as _is_recorded_lava,
    )
    existing = await get_subscription_by_payment_id(payment_id)
    if existing:
        logger.warning("Lava: duplicate payment %s", payment_id)
        return web.Response(status=200)
    # Idempotency for grace-renewal path: try_renew_from_grace records the payment
    # but does not create a new sub — on retry, get_subscription_by_payment_id returns
    # None, but the payment is already recorded. Without this check a second sub gets created.
    if await _is_recorded_lava(payment_id):
        logger.warning("Lava payment.success: already processed %s (grace-renew path), skip", payment_id)
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

    # Cross-method dedup. Если уже есть active non-trial sub — параллельная
    # оплата другим методом успела создать sub. Lava-recurring контракт уже
    # привязан к карте, нужно отменить иначе будет повторно списывать.
    _racing_lv = await get_active_subscription(user_id)
    if _racing_lv and _racing_lv.get("plan") != "vpn_trial" and _racing_lv.get("status") == "active":
        logger.error(
            "Lava cross-method duplicate: user=%d already has sub=%d plan=%s, "
            "received contract=%s for plan=%s — cancelling Lava + admin alert",
            user_id, _racing_lv["id"], _racing_lv.get("plan"), contract_id, plan_key,
        )
        # Если это recurring контракт — сразу отменяем чтобы Lava не списывала.
        if status == "subscription-active" and contract_id and LAVATOP_API_KEY:
            from services.lavatop import cancel_subscription as _lava_cancel
            try:
                await _lava_cancel(api_key=LAVATOP_API_KEY, contract_id=contract_id)
            except Exception as e:
                logger.warning("Lava dup cancel failed: %s", e)
        try:
            if ADMIN_ID:
                await bot.send_message(
                    ADMIN_ID,
                    f"🚨 <b>Lava dup payment</b>\n\n"
                    f"User: <code>{user_id}</code>\n"
                    f"Existing sub: #{_racing_lv['id']} ({_racing_lv.get('plan')})\n"
                    f"New contract: <code>{contract_id}</code> ({plan_key})\n"
                    f"Paid: {amount:.2f} {currency}\n\n"
                    "Refund вручную через Lava-кабинет.",
                    parse_mode="HTML",
                )
        except Exception:
            pass
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
    await _rp_lava(
        user_id=user_id, subscription_id=sub_id, method="lavatop", tx_id=payment_id,
        stars=0, amount_usd=amount,  # фактически ₽, хранится в поле usd для аналитики
    )

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
            "Lava provision FAILED 0/%d user=%d sub=%d contract=%s — admin alert + sub expire",
            total, user_id, sub_id, contract_id,
        )
        # Mark sub expired so user can re-buy. Admin handles refund manually.
        from services.database import mark_subscription_expired
        try:
            await mark_subscription_expired(sub_id)
        except Exception as e:
            logger.error("Lava 0/N: mark_subscription_expired sub=%d: %s",
                         sub_id, e, exc_info=True)

        # For Lava-recurring: cancel contract so card doesn't get charged again
        # for a sub that delivered nothing.
        if is_subscription and contract_id and LAVATOP_API_KEY:
            from services.database import disable_auto_renew
            from services.lavatop import cancel_subscription as _lava_cancel
            try:
                await disable_auto_renew(sub_id)
            except Exception as e:
                logger.warning("Lava 0/N: disable_auto_renew sub=%d: %s", sub_id, e)
            try:
                await _lava_cancel(api_key=LAVATOP_API_KEY, contract_id=contract_id)
            except Exception as e:
                logger.warning("Lava cancel after 0/N failure: %s", e)
        try:
            if ADMIN_ID:
                await bot.send_message(
                    ADMIN_ID,
                    f"🚨 <b>Lava provision FAIL</b>\n\n"
                    f"User: <code>{user_id}</code>\n"
                    f"Sub: #{sub_id}\n"
                    f"Plan: {plan_key}\n"
                    f"Contract: <code>{contract_id}</code>\n\n"
                    "Sub marked expired. Refund manually via Lava-кабинет.",
                    parse_mode="HTML",
                )
        except Exception:
            pass
        try:
            from services.database import get_user_lang as _gul_pf
            from services.i18n_bot import t as _i18n_t_pf
            _lang_pf = await _gul_pf(user_id) or "ru"
            await bot.send_message(
                user_id,
                _i18n_t_pf(_lang_pf, "bot_provision_failed"),
                parse_mode="HTML",
            )
        except Exception:
            pass
        return web.Response(status=200)

    # Full purchase-success UX (AWG configs as documents + sub URL +
    # inline buttons). Shared с CryptoBot/OxaPay path'ами — единый код.
    try:
        from handlers.vpn import send_purchase_success_message
        await send_purchase_success_message(
            bot, user_id, sub_id, plan, plan_key, expires_at,
            delivered, total,
        )
    except Exception as e:
        logger.warning(
            "Lava: send_purchase_success_message failed user=%d: %s",
            user_id, e, exc_info=True,
        )

    if is_subscription:
        try:
            from services.database import get_user_lang as _gul_ar
            from services.i18n_bot import t as _i18n_t_ar
            _lang_ar = await _gul_ar(user_id) or "ru"
            await bot.send_message(
                user_id,
                _i18n_t_ar(_lang_ar, "bot_lava_autorenew_note"),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("Lava: renew_note failed user=%d: %s", user_id, e)

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
    # F8: bilingual invoice description (Telegram caches it).
    from services.database import get_user_lang as _gul_esim
    from services.i18n_bot import t as _t_esim
    _esim_lang = await _gul_esim(user["id"])
    esim_desc = _t_esim(_esim_lang, "bot_invoice_desc_esim", name=name)
    url = await bot.create_invoice_link(
        title=name,
        description=esim_desc,
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
        # Сигнал «у юзера КОГДА-ТО был включён auto-renew, потом отключился».
        # Нужен фронту чтобы показывать «работает до X (без автопродления)»
        # только тем, кто реально отменил recurring (а не one-time Stars-юзерам).
        "auto_renew_disabled_at": sub.get("auto_renew_disabled_at"),
        # FFF4: timestamp последнего failed recurring charge. Mini App рисует
        # yellow warning banner если флаг non-NULL и auto_renew=1 — юзер
        # видит проблему до того, как Lava сделает следующий retry.
        "last_charge_failed_at": sub.get("last_charge_failed_at"),
    })


async def handle_cancel_renewal(request: web.Request) -> web.Response:
    """POST /api/vpn/subscription/cancel-renewal — выключает автопродление
    Lava recurring подписки. Существующий период дослужит до expires_at.
    """
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    sub = await get_active_subscription(user["id"])
    if sub is None:
        return await _user_err(user["id"], "no_recurring_sub", "bot_api_err_no_recurring_sub", 400)
    if not sub.get("auto_renew"):
        # Уже отменено ранее — повторный клик; возвращаем ok чтобы UI не ругался.
        return web.json_response({"ok": True, "already_cancelled": True})

    # Stars-recurring sub: auto_renew=1, payment_provider='stars',
    # parent_contract_id=NULL. Telegram не даёт API для отмены — юзер
    # должен сам зайти в TG → Настройки → Звёзды → Подписки.
    # Возвращаем manual_cancel=true чтобы фронт показал инструкцию вместо
    # успешной отмены (раньше тут был 400 — кнопка ломалась с ошибкой).
    if sub.get("payment_provider") == "stars":
        # Telegram не даёт API отменить Stars-recurring — юзер делает это сам в
        # TG → Настройки → Звёзды → Подписки. Но мы ВСЁ РАВНО ставим auto_renew=0,
        # чтобы наши UI/reminders перестали говорить «спишется в след. месяце».
        # Если Telegram таки спишет ещё раз (юзер не отменил в TG), то
        # successful_payment-хендлер обработает это как свежую покупку
        # (отдельный payment_id) и создаст новую sub — это приемлемо.
        from services.database import disable_auto_renew, get_user_lang as _gul_stars
        from services.i18n_bot import t as _i18n_t_stars
        await disable_auto_renew(sub["id"])
        _lang_stars = await _gul_stars(user["id"])
        return web.json_response({
            "manual_cancel": True,
            "provider": "stars",
            "instructions": _i18n_t_stars(_lang_stars, "bot_stars_cancel_instructions"),
        })

    # Дальше — Lava recurring (требует contract_id).
    if not sub.get("parent_contract_id"):
        return await _user_err(user["id"], "sub_not_recurring", "bot_api_err_sub_not_recurring", 400)

    contract_id = sub["parent_contract_id"]
    # CAS-первым: атомарно снимаем auto_renew, и только победитель идёт в
    # Lava. При гонке двух устройств второе получает was_renewed=False и
    # отвечает «уже отменено» — иначе оба бы дёрнули Lava cancel, второй
    # raise 404 и спамил admin-алерты.
    from services.lavatop import cancel_subscription as _lava_cancel
    from services.database import disable_auto_renew
    was_renewed = await disable_auto_renew(sub["id"])
    if not was_renewed:
        # Параллельный cancel из другого устройства уже отработал.
        return web.json_response({"ok": True, "already_cancelled": True})

    ok = False
    if LAVATOP_ENABLED:
        try:
            ok = await _lava_cancel(api_key=LAVATOP_API_KEY, contract_id=contract_id)
        except Exception as e:
            logger.error("Lava cancel exception sub=%d: %s", sub["id"], e, exc_info=True)
    else:
        logger.warning("cancel-renewal: LAVATOP_ENABLED=false sub=%d", sub["id"])

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

    from services.database import get_user_lang as _gul_trial_err
    from services.i18n_bot import t as _i18n_t_err
    _err_lang = await _gul_trial_err(user["id"])
    try:
        result = await provision_trial(user["id"])
    except TrialBlockedByActiveSub:
        return web.json_response(
            {"error": "active_subscription",
             "message": _i18n_t_err(_err_lang, "trial_blocked_active_sub")},
            status=409,
        )
    except TrialAlreadyClaimed:
        return web.json_response(
            {"error": "already_claimed",
             "message": _i18n_t_err(_err_lang, "trial_already_claimed")},
            status=409,
        )
    except TrialNoServer:
        return web.json_response(
            {"error": "no_server",
             "message": _i18n_t_err(_err_lang, "trial_no_server")},
            status=503,
        )
    except VpnctlError as e:
        logger.warning("trial provision failed: %s", e, exc_info=True)
        return web.json_response(
            {"error": "provision_failed",
             "message": _i18n_t_err(_err_lang, "trial_provision_error", error=str(e))},
            status=500,
        )

    # Дублируем URL в чат с ботом — Mini App success-баннер хорош, но юзеру
    # нужно куда-то скопировать ссылку, и чат естественнее.
    try:
        bot: Bot = request.app["bot"]
        expires_str = result["expires_at"].strftime("%d.%m.%Y %H:%M")
        has_awg = bool(result.get("awg_config"))

        from services.database import get_user_lang as _gul_trial
        from services.i18n_bot import t as _i18n_t_trial, day_word as _dw_trial
        _lang_trial = await _gul_trial(user["id"])
        is_en = (_lang_trial or "").lower().startswith("en")
        win_note = (
            "💻 <b>Windows</b>: download <a href=\"https://amnezia.org/downloads\">Amnezia VPN</a>, not WireGuard.exe"
            if is_en else
            "💻 <b>Windows</b>: качай <a href=\"https://amnezia.org/downloads\">Amnezia VPN</a>, не WireGuard.exe"
        )
        duration_days = result['duration_days']
        day_w = _dw_trial(_lang_trial, duration_days)
        key = "trial_success_awg" if has_awg else "trial_success_vless"
        msg = _i18n_t_trial(
            _lang_trial, key,
            days=duration_days, day_word=day_w,
            expires=expires_str, sub_url=result['sub_url'],
        )
        msg += f"\n\n{win_note}"
        await bot.send_message(user["id"], msg, parse_mode="HTML", disable_web_page_preview=True)
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
        return await _user_err(user["id"], "unknown_plan", "bot_api_err_unknown_plan", 400)

    sub = await get_active_subscription(user["id"])
    if sub is None:
        return await _user_err(user["id"], "no_active_sub", "bot_api_err_no_active_sub", 400)

    cur_plan = VPN_PLANS.get(sub["plan"])
    if cur_plan is None:
        return await _user_err(user["id"], "current_plan_unknown", "bot_api_err_current_plan_unknown", 400)

    if plan_key == sub["plan"]:
        return web.json_response({"ok": True, "same": True})

    from datetime import datetime
    expires          = datetime.fromisoformat(sub["expires_at"])
    remaining_days_f = max(0.0, (expires - datetime.utcnow()).total_seconds() / 86400)
    remaining_days   = int(remaining_days_f)  # display only

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
        # - Active с remaining > 0: pro-rated delta `(new - cur) × remaining_days / 30`.
        #   Юзер платит за «улучшение оставшегося периода».
        # - Grace ИЛИ active с remaining_days_f <= 0 (only-just expired, ждёт
        #   scheduler tick): full new-plan цена. Без второй ветки между
        #   expires_at и тиком scheduler'а юзер апгрейдил vpn_base→vpn_max
        #   за 1₽ (audit 17.05 #4, расширенный аналог для not-yet-grace окна).
        # F9: bilingual upgrade descriptions (shown in CryptoBot checkout).
        from services.database import get_user_lang as _gul_up
        from services.i18n_bot import t as _t_up
        from services.plans import plan_display_name as _pdn
        _up_lang_raw = await _gul_up(user["id"])
        _up_lang = "en" if (_up_lang_raw or "").lower().startswith("en") else "ru"
        _new_name = _pdn(new_plan, _up_lang)
        if sub.get("status") == "grace" or remaining_days_f <= 0:
            rub_price = new_rub
            upgrade_desc = _t_up(_up_lang, "bot_upgrade_desc_grace", name=_new_name)
        else:
            rub_price = max(1, _ceil((new_rub - cur_rub) * remaining_days_f / 30))
            upgrade_desc = _t_up(_up_lang, "bot_upgrade_desc_active", name=_new_name, days=remaining_days)

        if not CRYPTOBOT_TOKEN:
            return await _user_err(user["id"], "upgrade_unavailable", "bot_api_err_upgrade_unavailable", 503)

        from services.cryptobot import create_invoice
        bot: Bot = request.app["bot"]
        bot_info = await bot.get_me()
        # PS2: новый payload — без baked-in deltas. Webhook пересчитает их
        # под per-sub lock от ТЕКУЩЕГО sub.plan. Параллельные upgrade-invoice'ы
        # больше не аккумулируют stale deltas. `expected_from` нужен чтобы
        # webhook отказался применять платёж если план уже двинулся
        # (юзер открыл два invoice'а, оплатил оба — второй идёт админу на refund).
        payload = f"plan_upgrade:{sub['id']}:{plan_key}:{sub['plan']}"

        try:
            invoice = await create_invoice(
                CRYPTOBOT_TOKEN,
                fiat="RUB",
                amount=str(rub_price),
                payload=payload,
                description=upgrade_desc,
                bot_username=bot_info.username or "",
            )
        except Exception as e:
            logger.error("CryptoBot upgrade invoice error: %s", e, exc_info=True)
            return await _user_err(user["id"], "payment_service", "bot_api_err_payment_service", 503)

        pay_url = invoice.get("mini_app_invoice_url") or invoice.get("bot_invoice_url", "")
        return web.json_response({"invoice_url": pay_url})

    else:
        # Даунгрейд — планируем на следующий месяц.
        # CAS защищает от гонки: параллельная сессия из второго устройства
        # могла уже изменить pending_plan между нашим SELECT и UPDATE.
        prev_pending = sub.get("pending_plan") or ""
        if sub.get("pending_plan") == plan_key:
            ok = await schedule_plan_change(
                sub["id"], None, expected_previous=prev_pending,
            )
            if not ok:
                return await _user_err(
                    user["id"], "concurrent_modification",
                    "bot_api_err_concurrent_plan_change", 409,
                )
            return web.json_response({"ok": True, "cancelled": True})

        ok = await schedule_plan_change(
            sub["id"], plan_key, expected_previous=prev_pending,
        )
        if not ok:
            return await _user_err(
                user["id"], "concurrent_modification",
                "bot_api_err_concurrent_plan_change", 409,
            )
        return web.json_response({"ok": True, "scheduled": True})


# ── Subscription URL для VPN-клиентов ──────────────────────────────────────────

async def handle_user_subscription(request: web.Request) -> web.Response:
    """GET /sub/{token} — возвращает base64-encoded список vless URL клиента.
    Happ / Streisand / sing-box обновляют его в фоне, поэтому при throttle
    или смене UUID юзер автоматически получает свежие конфиги.

    Подписочные HTTP-заголовки (Profile-Title, Subscription-Userinfo)
    дают клиенту красивый заголовок с трафиком и датой истечения —
    как у Outline/StealthSurf и других платных провайдеров."""
    from datetime import datetime, timezone
    from services.database import (
        get_user_by_sub_token, get_active_vless_configs_for_user
    )
    import aiosqlite
    from services.database import DB_PATH

    token = request.match_info.get("token", "").strip()
    if not token or len(token) < 16:
        return web.Response(text="invalid", status=400)

    # Rate limit: публичный endpoint без auth. Защита от brute-force token'а
    # (32+ chars entropy, но без лимита нельзя — лог-флуд + DDoS).
    # Happ/Streisand тянут URL раз в 12 часов (Profile-Update-Interval) →
    # 6 сек/(IP,token) rate-limit с запасом.
    # Per-(ip, token) instead of ip-only: carrier NAT users with multiple
    # devices (phone + laptop + router) share the same public IP but pull
    # different sub URLs simultaneously — pure ip-key would 429 the second
    # device on every refresh tick.
    ip = _client_ip(request)
    rate_key = f"{ip}:{token[:16]}"  # truncate token to keep dict key small
    now = _time.monotonic()
    if not _rate_limit_check_evict(_sub_rate, rate_key, now, window=6.0):
        return web.Response(text="rate limited", status=429)

    user = await get_user_by_sub_token(token)
    if not user:
        # 410 Gone + Profile-Title: токен ротировали (новая покупка, refund,
        # ban). Happ/Streisand при 404 показывают «server error», а на 410
        # с Profile-Title — внятный заголовок. Profile-Update-Interval=0
        # говорит клиенту прекратить poll'ить этот URL.
        return web.Response(
            text="subscription token expired — please reopen the bot to get a new URL",
            status=410,
            headers={
                "Content-Type":            "text/plain; charset=utf-8",
                # ASCII-only here, but use _safe_header for consistency.
                "Profile-Title":           _safe_header("MAX VPN — token expired"),
                "Profile-Update-Interval": "0",
                # No-cache on 410: client may have cached the old (valid)
                # body for hours; without no-cache it might keep using
                # rotated token for a full Profile-Update-Interval cycle
                # even though we already told it the token is gone.
                "Cache-Control":           "no-cache, no-store, must-revalidate",
                "Pragma":                  "no-cache",
                "Profile-Web-Page-Url":    "https://t.me/maxvpnesim_bot",
                "Support-Url":             "https://t.me/maxvpnesim_bot",
            },
        )

    # Динамический URL-резолвер: для backfilled юзеров строит URL'ы из
    # `users.vless_uuid × servers.xray_*` (одна строка добавления сервера
    # — и она автоматически появляется во всех подписках). Для не-backfilled
    # фолбэчится на `configs.config_data`.
    urls = await _resolve_vless_urls(user["id"])

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
        # Omit total= on expired path. expire= in the past is the standard
        # signal; total=1 sentinel was a workaround for early Happ versions
        # that no longer applies and may confuse other parsers (sing-box
        # reads total=1 with download=0 as «1 byte cap, 0 used» — a data
        # anomaly, not «expired»).
        return web.Response(
            text="",
            headers={
                "Content-Type":           "text/plain; charset=utf-8",
                "Cache-Control":          "no-cache, no-store, must-revalidate",
                "Subscription-Userinfo":  f"download=0; upload=0; expire={int(_t.time()) - 1}",
                "Profile-Update-Interval": "12",
                "Profile-Title":          _safe_header("❌ MAX VPN — подписка истекла"),
                "Profile-Web-Page-Url":   "https://t.me/maxvpnesim_bot",
                "Support-Url":            "https://t.me/maxvpnesim_bot",
            },
        )

    # ── Build Subscription-Userinfo header ───────────────────────────────────
    # Найдём активную подписку юзера + лимиты её плана + использованный трафик
    rx_total = 0    # bytes received BY peer (= client download)
    tx_total = 0    # bytes sent BY peer (= client upload)
    total_bytes = 0
    expire_unix = 0
    plan_name = "MAX VPN"
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            # Включаем grace: тело /sub/{token} возвращает рабочие VLESS URLs
            # и для grace-подписок (vless-grace inbound, 256 кбит/с). Без
            # status IN ('active','grace') юзер в grace-периоде получал пустой
            # Subscription-Userinfo header и Profile-Title без plan_name.
            sub = await (await db.execute(
                """SELECT id, plan, expires_at, status FROM subscriptions
                   WHERE user_id=? AND status IN ('active', 'grace')
                   ORDER BY created_at DESC LIMIT 1""",
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
                # В grace юзер видит, что подписка истекла, но всё ещё работает
                # на пониженной скорости — даём явный визуальный сигнал в title.
                if sub["status"] == "grace":
                    plan_name = f"{plan_name} (grace)"
                cap_gb = plan.get("soft_cap_gb")
                if cap_gb:
                    total_bytes = int(cap_gb) * 1024 ** 3
                # Сумма трафика по конфигам подписки. Splitting rx/tx так
                # как Happ показывает download/upload отдельно. Orientation:
                # agent reports `rx_bytes` = bytes received BY the peer (=
                # data the VPN server sent to the client = client download),
                # `tx_bytes` = bytes sent BY peer (= client upload).
                # See agent vpnctl peer stats + services.scheduler._sync_vless_stats.
                row = await (await db.execute(
                    """SELECT COALESCE(SUM(rx_bytes),0) AS rx,
                              COALESCE(SUM(tx_bytes),0) AS tx
                       FROM configs WHERE subscription_id=? AND status='active'""",
                    (sub["id"],),
                )).fetchone()
                if row:
                    rx_total = int(row["rx"] or 0)
                    tx_total = int(row["tx"] or 0)
                # expire_at
                try:
                    exp = sub["expires_at"]
                    if exp:
                        # формат может быть "2026-05-28 21:00:58" или ISO
                        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                                    "%Y-%m-%d %H:%M:%S"):
                            try:
                                # expires_at is UTC by convention — attach tzinfo before
                                # .timestamp() so it isn't reinterpreted as local time
                                # on a MSK-tz bot server.
                                naive_dt = datetime.strptime(exp, fmt)
                                expire_unix = int(
                                    naive_dt.replace(tzinfo=timezone.utc).timestamp()
                                )
                                break
                            except ValueError:
                                continue
                except Exception:
                    pass
    except Exception as e:
        logger.warning("subscription header build failed: %s", e, exc_info=True)

    # Split download/upload so Happ shows them separately instead of
    # everything bundled under "download".
    sub_userinfo_parts = [f"download={rx_total}", f"upload={tx_total}"]
    if total_bytes > 0:
        sub_userinfo_parts.append(f"total={total_bytes}")
    # Always emit expire= — omitting it makes Happ treat sub as lifetime,
    # which is misleading if sub has no parseable expires_at. Use past
    # timestamp as «unknown / treat as expired» sentinel.
    sub_userinfo_parts.append(
        f"expire={expire_unix if expire_unix > 0 else int(_time.time()) - 1}"
    )
    sub_userinfo = "; ".join(sub_userinfo_parts)

    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Subscription-Userinfo": sub_userinfo,
        "Profile-Update-Interval": "12",
        "Profile-Title": _safe_header(f"🌐 MAX VPN · {plan_name}"),
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
        # Исключаем refunded sub'ы и trial — без фильтра LTV overstate'ил
        # доход на сумму всех refund'ов (юзер видел «спустил 5000⭐» хотя
        # половину вернули админом). trial всегда stars_paid=0, но на всякий.
        async with db.execute(
            "SELECT COALESCE(SUM(stars_paid),0) FROM subscriptions "
            "WHERE user_id=? AND refunded_at IS NULL AND plan != 'vpn_trial'",
            (uid,),
        ) as cur:
            row = await cur.fetchone()
            assert row is not None
            stars_spent = row[0]
        async with db.execute(
            "SELECT COALESCE(ref_bonus_days,0) FROM users WHERE id=?", (uid,)
        ) as cur:
            row = await cur.fetchone()
            bonus_days = row[0] if row else 0
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE referred_by=? AND COALESCE(is_banned, 0) = 0",
            (uid,),
        ) as cur:
            row = await cur.fetchone()
            assert row is not None
            invited = row[0]
        # converted — сколько из приглашённых реально оформили подписку.
        # Используется на Home banner: «3 пригласил · 1 уже оформил».
        # Фильтры дублируют database.get_referral_stats — refunded не считаются
        # converted (иначе админский refund «съедал» статистику реферера).
        async with db.execute(
            """SELECT COUNT(DISTINCT u.id) FROM users u
               JOIN subscriptions s ON s.user_id=u.id
               WHERE u.referred_by=? AND s.status IN ('active','expired','grace')
                 AND s.plan != 'vpn_trial'
                 AND s.refunded_at IS NULL""",
            (uid,),
        ) as cur:
            row = await cur.fetchone()
            assert row is not None
            converted = row[0]

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

    sub_before = await get_active_subscription(user["id"])
    was_grace = sub_before is not None and sub_before.get("status") == "grace"

    from services.database import redeem_referral_bonus
    result = await redeem_referral_bonus(user["id"])
    if result is None:
        # Узнаем причину для понятного error message
        from services.database import has_active_paid_sub
        if not await has_active_paid_sub(user["id"]):
            return web.json_response({"error": "no_active_sub"}, status=400)
        return web.json_response({"error": "no_bonus"}, status=400)

    # Grace → active transition: unthrottle VPN configs so the user regains
    # full speed immediately (redeem_referral_bonus flips status to 'active').
    if was_grace and sub_before is not None:
        from services.grace import unthrottle_sub_configs
        _spawn_bg(
            unthrottle_sub_configs(sub_before["id"], user["id"], sub_before["plan"]),
            name=f"unthrottle_referral_sub{sub_before['id']}",
        )

    # Notify юзеру в чат бота
    try:
        bot: Bot = request.app["bot"]
        new_date = result["new_expires_at"][:10]
        from services.database import get_user_lang as _gul_rb
        from services.i18n_bot import t as _i18n_t_rb, day_word as _dw_rb
        _lang_rb = await _gul_rb(user["id"]) or "ru"
        _days_rb = result["days"]
        await bot.send_message(
            user["id"],
            _i18n_t_rb(
                _lang_rb, "bot_referral_bonus_activated",
                days=_days_rb,
                day_word=_dw_rb(_lang_rb, _days_rb),
                until=new_date,
            ),
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
        return await _user_err(user["id"], "ticket_empty", "bot_api_err_ticket_empty", 400)
    if len(message) > 2000:
        return await _user_err(user["id"], "ticket_too_long", "bot_api_err_ticket_too_long", 400)

    ticket_id = await create_support_ticket(user["id"], category, message)

    bot: Bot = request.app["bot"]
    cat_label = CATEGORY_LABELS.get(category, category)
    username  = f"@{user['username']}" if user.get("username") else f"id:{user['id']}"
    name      = html_escape(user.get("first_name") or "—")
    username_safe = html_escape(username)
    text = (
        f"🎫 <b>Тикет #{ticket_id}</b>\n"
        f"👤 {name} ({username_safe})\n"
        f"📂 {cat_label}\n\n"
        f"{html_escape(message)}"
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

def _check_admin_rate_limit(request: web.Request) -> bool:
    """Rate-limit gate for admin endpoints — per-IP.

    Returns False when the caller exceeded the window; caller should answer 429.
    Window bumped to 10s — admin panel issues several parallel requests on page
    load, and a 2s window was triggering false rejections.
    """
    import time as _t
    ip = _client_ip(request)
    return _rate_limit_check_evict(_admin_rate, ip, _t.monotonic(), window=10.0)


def _check_admin_secret(request: web.Request) -> bool:
    """Validate X-Admin-Secret header. Without it all admin endpoints answer 403.

    Note: rate-limit is now a separate gate (`_check_admin_rate_limit`) so callers
    can distinguish a 429 from a 403 — previously both collapsed into False and
    confused the admin panel during parallel page loads.
    """
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
    if not _check_admin_rate_limit(request):
        return web.json_response({"error": "rate_limited"}, status=429)
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
    quoted = html_escape((ticket.get('message') or '')[:300])
    reply_body = html_escape(text)
    msg_text = (
        f"💬 <b>Ответ от поддержки</b> (#{ticket_id})\n\n"
        f"<i>На твоё обращение:</i>\n"
        f"<blockquote>{quoted}</blockquote>\n\n"
        f"{reply_body}"
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
    if not _check_admin_rate_limit(request):
        return web.json_response({"error": "rate_limited"}, status=429)
    if not _check_admin_secret(request):
        return web.json_response({"error": "forbidden"}, status=403)

    from services.database import close_ticket, audit_log_record
    ticket_id_str = request.match_info.get("id", "")
    try:
        ticket_id = int(ticket_id_str)
    except ValueError:
        return web.json_response({"error": "bad id"}, status=400)

    closed = await close_ticket(ticket_id)
    if not closed:
        return web.json_response({"error": "ticket not found"}, status=404)

    await audit_log_record(
        admin_id=0, action="ticket_close",
        target=f"ticket:{ticket_id}",
        details="-",
    )
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
    if not _check_admin_rate_limit(request):
        return web.json_response({"error": "rate_limited"}, status=429)
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
    from services.grace import unthrottle_sub_configs
    updated = await extend_subscription(sub_id, days)
    if updated is None:
        return web.json_response({"error": "sub not found"}, status=404)

    was_grace = updated.pop("_was_grace", False)

    await audit_log_record(
        admin_id=0, action="sub_extend",
        target=f"sub:{sub_id}",
        details=f"+{days}d reason={reason or '-'} new_expiry={updated['expires_at']}",
    )

    # Если sub была в grace — снять throttle на агентах (AWG tc + VLESS inbound).
    # DB уже active; делаем в фоне чтобы не задерживать ответ admin-панели.
    if was_grace:
        _spawn_bg(
            unthrottle_sub_configs(updated["id"], updated["user_id"], updated["plan"]),
            name=f"unthrottle_admin_sub{updated['id']}",
        )

    return web.json_response({"ok": True, "subscription": updated})


async def handle_admin_sub_refund(request: web.Request) -> web.Response:
    """POST /api/admin/sub/{id}/refund
    Body: { "reason": "...", "stars_refund": true|false }
    Помечает подписку refunded.  Если stars_refund=true и платёж был Stars —
    дополнительно вызывает refund_star_payment у Telegram (необратимо).
    """
    if not _check_admin_rate_limit(request):
        return web.json_response({"error": "rate_limited"}, status=429)
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
    if sub["user_id"] in ADMIN_IDS or sub["user_id"] == ADMIN_ID:
        return web.json_response(
            {"error": "Cannot refund an admin's subscription"},
            status=400,
        )

    # Получаем payment_id отдельным запросом — get_subscription_by_id его не возвращает.
    # R8: для Stars refund приоритизируем ПОСЛЕДНИЙ Stars-charge для этой
    # подписки (могла быть doplata за upgrade), потом фолбэчим на оригинальный
    # subscriptions.payment_id. Если оба отличаются — это upgrade-сценарий,
    # автоматом возвращается только последний; админ видит warning.
    import aiosqlite
    from services.database import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT tx_id FROM payments WHERE subscription_id=? AND method='stars' "
            "AND COALESCE(refunded_at, '') = '' "
            "ORDER BY id DESC LIMIT 1",
            (sub_id,),
        ) as cur:
            row = await cur.fetchone()
            latest_stars_tx = row[0] if row else None
        async with db.execute(
            "SELECT payment_id FROM subscriptions WHERE id=?", (sub_id,),
        ) as cur:
            row = await cur.fetchone()
            sub_payment_id = row[0] if row else None
    payment_id = latest_stars_tx or sub_payment_id
    if latest_stars_tx and sub_payment_id and latest_stars_tx != sub_payment_id:
        logger.warning(
            "Refund sub=%d: refunding latest Stars charge=%s but sub has original=%s "
            "— upgrade scenario, additional charges may need manual refund",
            sub_id, latest_stars_tx, sub_payment_id,
        )

    # Определяем provider по префиксу payment_id.  Stars = всё что НЕ
    # crypto_/oxapay_/lavatop_/free_/admin_grant_/trial_ — не Stars-платёж,
    # нельзя делать Stars refund. `admin_grant_` (бесплатное продление от
    # админа) и `trial_` (триал) тоже не Stars: попытка refund_star_payment
    # упадёт CHARGE_ID_INVALID и заспамит лог.
    _NON_STARS_PREFIXES = (
        "crypto_", "oxapay_", "lavatop_", "free_", "admin_grant_", "trial_",
    )
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

    # R4: trial refund — это не «вернуть деньги» (денег не было), а
    # «откатить triаl чтобы юзер мог попробовать снова». mark_refunded
    # перевело бы sub в status='refunded' + установило refunded_at —
    # cooldown-фильтр триала смотрит на «есть ли expired-trial за последние
    # 30 дней», и refunded считался бы expired-like → юзер заблокирован на
    # месяц после провалившегося триала. mark_subscription_trial_rolled_back
    # ставит trial_rolled_back=1 чтобы cooldown НЕ применялся.
    if payment_id and payment_id.startswith("trial_"):
        from services.database import mark_subscription_trial_rolled_back
        await mark_subscription_trial_rolled_back(sub_id)
    else:
        await mark_subscription_refunded(sub_id)
    # Откат реф-бонуса если он был начислен на эту подписку.
    # Для trial это no-op (trial не даёт реф-бонус), но вызов безвреден.
    await rollback_referral_bonus(sub_id)

    # Lava-recurring: вырубаем auto_renew в нашей БД + дёргаем Lava cancel API.
    # Без этого Lava продолжает списывать с карты раз в месяц → webhook'и
    # приходят → бот создаёт новые sub'ы. Админ "вернул деньги", а они дальше
    # снимаются с юзера. Прод-инцидент: P1 round 8 audit.
    if sub.get("auto_renew") and sub.get("payment_provider") == "lavatop":
        from services.database import disable_auto_renew
        await disable_auto_renew(sub_id)
        if sub.get("parent_contract_id") and LAVATOP_API_KEY:
            from services.lavatop import cancel_subscription as _lava_cancel
            try:
                ok = await _lava_cancel(
                    api_key=LAVATOP_API_KEY,
                    contract_id=sub["parent_contract_id"],
                )
                if not ok:
                    logger.warning(
                        "admin refund sub=%d: Lava cancel API returned non-2xx; "
                        "auto_renew=0 set locally but contract may still charge",
                        sub_id,
                    )
            except Exception as e:
                logger.error(
                    "admin refund sub=%d: Lava cancel call failed: %s",
                    sub_id, e, exc_info=True,
                )

    # Revoke active configs: peer'ы на агенте + reset БД.  БЕЗ ЭТОГО ЮЗЕР
    # ПРОДОЛЖАЕТ ПОЛЬЗОВАТЬСЯ VPN после refund'а — handler раньше делал
    # только mark_refunded, а AWG-конфиги вообще никогда не подчищаются
    # sync'ом.  Прод-инцидент 20.05: user 594024866 sub#5 expired 15 мая,
    # 5 конфигов работали 6 дней без действующей подписки.  Audit #1.
    from services.revoke import revoke_subscription_configs
    revoked, failed = await revoke_subscription_configs(
        sub_id, sub["plan"], log_prefix=f"refund#{sub_id}"
    )
    logger.info("refund sub #%d: revoked %d config(s), failed %d",
                sub_id, revoked, failed)

    # Источник платежа — для audit log + UI чтобы админ видел корректный канал.
    # R5: добавлены admin_grant_ и trial_ — раньше они мис-лейблились как «stars»
    # и админ-UI пытался показать «Stars refund successful» для бесплатной выдачи.
    payment_source = "stars"
    if payment_id:
        if payment_id.startswith("oxapay_"): payment_source = "oxapay"
        elif payment_id.startswith("lavatop_"): payment_source = "lavatop"
        elif payment_id.startswith("crypto_"): payment_source = "cryptobot"
        elif payment_id.startswith("free_"): payment_source = "free"
        elif payment_id.startswith("admin_grant_"): payment_source = "admin_grant"
        elif payment_id.startswith("trial_"): payment_source = "trial"

    # R2/R3: Lava recurring — деньги НЕ возвращаются автоматически, только
    # cancel API. Собираем список всех Lava-charges чтобы UI показал админу
    # «вы должны вернуть N платежей вручную в Lava-кабинете».
    lava_recurring_charges: list[dict] = []
    if payment_source == "lavatop":
        import aiosqlite as _aiosqlite_local
        async with _aiosqlite_local.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT tx_id, amount_usd, created_at FROM payments "
                "WHERE subscription_id=? AND method='lavatop' ORDER BY id DESC",
                (sub_id,),
            ) as cur:
                rows = await cur.fetchall()
                for r in rows:
                    lava_recurring_charges.append({
                        "tx_id": r[0],
                        "amount": r[1],
                        "created_at": r[2],
                    })

    await audit_log_record(
        admin_id=0, action="sub_refund",
        target=f"sub:{sub_id}",
        details=(
            f"user={sub['user_id']} method={payment_source} "
            f"stars_refund={stars_refund_done} revoked={revoked} "
            f"revoke_failed={failed} reason={reason or '-'}"
        ),
    )
    return web.json_response({
        "ok": True,
        "stars_refund_done": stars_refund_done,
        "payment_source": payment_source,
        "configs_revoked": revoked,
        "configs_revoke_failed": failed,
        # backwards-compat: was_crypto был только CryptoBot. Если фронт
        # ориентируется на этот флаг — он по-прежнему получит ожидаемое.
        # NB: при payment_source ∈ {admin_grant, trial} этот флаг = False
        # как и раньше — фронту нечего возвращать, никаких денег нет.
        "was_crypto": bool(payment_id and payment_id.startswith("crypto_")),
        # R2/R3: Lava UI-context. Админ видит «надо вернуть N платежей вручную»
        # вместе со списком конкретных tx_id из Lava-кабинета.
        "lava_manual_refund_required": payment_source == "lavatop",
        "lava_recurring_charges": lava_recurring_charges,
        "lava_cancel_attempted": (
            payment_source == "lavatop" and bool(sub.get("parent_contract_id"))
        ),
        # R8: upgrade-warning — для Stars если refund'ится не оригинальный
        # charge (была doplata за upgrade). Админ видит оба tx_id и понимает
        # что один из них надо refund'нуть вручную.
        "stars_refunded_tx": latest_stars_tx if stars_refund_done else None,
        "stars_original_tx": sub_payment_id,
        "stars_multiple_charges": bool(
            latest_stars_tx and sub_payment_id and latest_stars_tx != sub_payment_id
        ),
    })


async def handle_admin_user_ban(request: web.Request) -> web.Response:
    """POST /api/admin/user/{id}/ban
    Body: { "reason": "..." }
    Ставит is_banned=1.  Существующие конфиги работают до естественного expiry —
    отдельной кнопкой можно сделать refund подписки если нужно отрезать сразу.
    """
    if not _check_admin_rate_limit(request):
        return web.json_response({"error": "rate_limited"}, status=429)
    if not _check_admin_secret(request):
        return web.json_response({"error": "forbidden"}, status=403)

    user_id = _parse_path_int(request, "id")
    if user_id is None:
        return web.json_response({"error": "bad id"}, status=400)
    if user_id in ADMIN_IDS or user_id == ADMIN_ID:
        return web.json_response(
            {"error": "Cannot ban an admin user"},
            status=400,
        )

    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = (body.get("reason") or "").strip()[:200] or None

    from services.database import set_user_banned, audit_log_record
    ok = await set_user_banned(user_id, banned=True, reason=reason)
    if not ok:
        return web.json_response({"error": "user not found"}, status=404)

    # Если у баненного юзера активная recurring-подписка — нужно остановить
    # будущие webhook-charges. Иначе банен, но Lava продолжит снимать с него
    # деньги, а наши webhook'и будут пытаться создавать sub'у баненному.
    # Конфиги при этом не трогаем — пусть дослужат свой период (админ может
    # отдельно сделать refund через /api/admin/sub/{id}/refund чтоб отрезать
    # сразу). Stars-recurring: у Telegram нет API отмены подписки от нашего
    # имени, поэтому сигналим админу что нужно сделать вручную refund.
    lava_cancel_attempted = False
    lava_cancel_ok = False
    stars_manual_required = False
    try:
        sub = await get_active_subscription(user_id)
    except Exception as e:
        logger.warning("ban: get_active_subscription failed user=%d: %s", user_id, e)
        sub = None
    if sub and sub.get("auto_renew"):
        provider = sub.get("payment_provider")
        if provider == "lavatop":
            from services.database import disable_auto_renew
            try:
                await disable_auto_renew(sub["id"])
            except Exception as e:
                logger.warning("ban: disable_auto_renew failed sub=%d: %s", sub["id"], e)
            if sub.get("parent_contract_id") and LAVATOP_API_KEY:
                from services.lavatop import cancel_subscription as _lava_cancel
                lava_cancel_attempted = True
                try:
                    lava_cancel_ok = await _lava_cancel(
                        api_key=LAVATOP_API_KEY,
                        contract_id=sub["parent_contract_id"],
                    )
                except Exception as e:
                    logger.warning(
                        "ban: Lava cancel failed sub=%d: %s",
                        sub["id"], e, exc_info=True,
                    )
        elif provider == "stars":
            # Telegram не даёт API для отмены подписки. Админ должен сам
            # refund'нуть последний charge через /api/admin/sub/{id}/refund —
            # это автоматически снимет stars-recurring у Telegram.
            stars_manual_required = True

    await audit_log_record(
        admin_id=0, action="user_ban",
        target=f"user:{user_id}",
        details=(
            f"reason={reason or '-'} "
            f"lava_cancel_attempted={lava_cancel_attempted} "
            f"lava_cancel_ok={lava_cancel_ok} "
            f"stars_manual_required={stars_manual_required}"
        ),
    )
    return web.json_response({
        "ok": True,
        "lava_cancel_attempted": lava_cancel_attempted,
        "lava_cancel_ok": lava_cancel_ok,
        # Если True — фронт админки покажет «не забудь сделать refund Stars
        # подписки чтобы остановить будущие списания».
        "stars_manual_required": stars_manual_required,
        "active_sub_id": sub["id"] if sub else None,
    })


async def handle_admin_grant_subscription(request: web.Request) -> web.Response:
    """POST /api/admin/grant_subscription
    Body: {
      "admin_id": <int>,           # telegram_id админа (whitelist через ADMIN_IDS)
      "target_telegram_id": <int>, # кому выдать
      "plan_key": "vpn_base"|"vpn_max"|...,
      "days": <int 1..365>,
      "reason": "<optional>",
      "target_username": "<optional, для пометки в логах>"
    }
    Выдаёт бесплатную подписку любому tg-юзеру по ID (даже не запускавшему бота).
    Если у юзера активная sub того же plan_key — продлевает на `days`.  Иначе
    создаёт новую sub + пустые config-слоты (юзер активирует их сам в Mini App).
    Опционально шлёт юзеру TG-уведомление о подарке если бот может ему написать.
    """
    if not _check_admin_rate_limit(request):
        return web.json_response({"error": "rate_limited"}, status=429)
    if not _check_admin_secret(request):
        return web.json_response({"error": "forbidden"}, status=403)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)

    admin_id = body.get("admin_id")
    target_id = body.get("target_telegram_id")
    plan_key = body.get("plan_key")
    days = body.get("days")
    reason = (body.get("reason") or "").strip()[:500] or None
    target_username = (body.get("target_username") or "").strip().lstrip("@")[:32] or None

    # Валидация: admin_id обязателен и должен быть в whitelist. Защита от
    # ситуации «утёк ADMIN_API_SECRET» — атакующий не подделает чужой ID.
    if not isinstance(admin_id, int) or admin_id <= 0:
        return web.json_response({"error": "admin_id required (int)"}, status=400)
    if ADMIN_IDS and admin_id not in ADMIN_IDS:
        return web.json_response({"error": "admin_id not in whitelist"}, status=403)

    if not isinstance(target_id, int) or target_id <= 0:
        return web.json_response({"error": "target_telegram_id required (positive int)"}, status=400)

    from services.plans import VPN_PLANS
    if not isinstance(plan_key, str) or plan_key not in VPN_PLANS:
        return web.json_response({"error": "unknown plan_key"}, status=400)
    if plan_key == "vpn_trial":
        return web.json_response(
            {"error": "Cannot grant a trial subscription; trial has its own cooldown logic"},
            status=400,
        )

    if not isinstance(days, int) or not (1 <= days <= 365):
        return web.json_response({"error": "days must be int in [1, 365]"}, status=400)

    from services.database import admin_grant_subscription, AdminGrantConflict
    try:
        result = await admin_grant_subscription(
            admin_id=admin_id,
            target_user_id=target_id,
            plan_key=plan_key,
            days=days,
            reason=reason,
            target_username=target_username,
        )
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    except AdminGrantConflict as e:
        # 409: юзер уже на другом активном плане. Админу нужно сначала
        # refund'ить существующую sub или сменить план через change-plan
        # endpoint, иначе создалась бы parallel sub с leak'ом конфигов.
        return web.json_response({
            "error": "active_sub_different_plan",
            "message": (
                f"У юзера уже активная подписка плана '{e.existing_sub.get('plan')}' "
                f"(sub id={e.existing_sub.get('id')}, status={e.existing_sub.get('status')}). "
                f"Сначала refund'и её, потом выдавай '{e.requested_plan}'."
            ),
            "existing_sub": {
                "id": e.existing_sub.get("id"),
                "plan": e.existing_sub.get("plan"),
                "status": e.existing_sub.get("status"),
                "expires_at": e.existing_sub.get("expires_at"),
            },
            "requested_plan": e.requested_plan,
        }, status=409)
    except Exception as e:
        logger.error("admin_grant failed: %s", e, exc_info=True)
        return web.json_response({"error": f"internal: {e}"}, status=500)

    # Если extend поднял sub из grace — снять throttle на агентах (AWG tc +
    # VLESS inbound). Без этого DB показывает active, а пиры остаются на
    # 256 кбит/с — зеркало того что делает handle_admin_sub_extend.
    if result.get("was_grace") and result.get("subscription_id"):
        from services.grace import unthrottle_sub_configs
        _spawn_bg(
            unthrottle_sub_configs(result["subscription_id"], target_id, plan_key),
            name=f"unthrottle_grant_sub{result['subscription_id']}",
        )

    # Best-effort notify юзера. Если он не /start'нул бота — get 403/400 и
    # игнорируем (юзер увидит подписку при первом /start).
    bot: Bot = request.app["bot"]
    plan_name = VPN_PLANS[plan_key].get("name", plan_key)
    notify_text = (
        f"🎁 <b>Вам подарили подписку!</b>\n\n"
        f"План: <b>{plan_name}</b>\n"
        f"Срок: <b>{days} дн.</b>\n\n"
        f"Открой Mini App «Мои конфиги» — там уже ждут пустые слоты, "
        f"активируй их и пользуйся."
    )
    if reason:
        notify_text += f"\n\n<i>Комментарий: {html_escape(reason[:200])}</i>"
    try:
        await bot.send_message(target_id, notify_text, parse_mode="HTML")
        result["notified"] = True
    except Exception as e:
        logger.info("grant notify failed user=%d: %s (юзер не /start'нул бота — норма)",
                    target_id, e)
        result["notified"] = False

    return web.json_response(result)


async def handle_admin_grants_list(request: web.Request) -> web.Response:
    """GET /api/admin/grants?limit=50
    Список последних N grant'ов из payments (is_free_grant=1). Сортировка по created_at DESC.
    """
    if not _check_admin_rate_limit(request):
        return web.json_response({"error": "rate_limited"}, status=429)
    if not _check_admin_secret(request):
        return web.json_response({"error": "forbidden"}, status=403)

    try:
        limit = int(request.query.get("limit", "50"))
    except ValueError:
        limit = 50
    limit = max(1, min(limit, 500))

    from services.database import list_admin_grants
    grants = await list_admin_grants(limit)
    return web.json_response({"grants": grants})


async def handle_admin_vless_backfill(request: web.Request) -> web.Response:
    """POST /api/admin/servers/{id}/backfill-vless
    Multi-location backfill: для нового VLESS-сервера провижит пиры всех
    активных слотов (которые сейчас только на других серверах). Slot UUID
    переиспользуется, юзер видит новую локацию в Happ-дропдауне без
    переимпорта подписки. Идемпотентна — повторный запуск пропустит уже-
    реплицированные слоты.
    """
    if not _check_admin_rate_limit(request):
        return web.json_response({"error": "rate_limited"}, status=429)
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

    # Marker: «backfill пройден — сервер можно отдавать в /sub/ URL'ах».
    # active_vless_servers() фильтрует по backfilled=1. Делаем UPDATE
    # безусловно: даже если часть слотов failed, оставшиеся клиенты
    # доступны (failed-логика на стороне admin'а — он повторит запуск).
    import aiosqlite as _aiosqlite_local
    from services.database import DB_PATH as _DB_PATH_local
    async with _aiosqlite_local.connect(_DB_PATH_local) as db:
        await db.execute("UPDATE servers SET backfilled=1 WHERE id=?", (server_id,))
        await db.commit()

    return web.json_response({
        "ok": True,
        "scanned": scanned,
        "created": created,
        "failed": failed,
        "failures": failures,
    })


async def handle_admin_migrate_configs(request: web.Request) -> web.Response:
    """POST /api/admin/servers/{id}/migrate-configs
    AWG/WG: re-provision на лучшем доступном сервере + уведомление юзеру скачать конфиг.
    VLESS: сбрасывает dead-server записи (multi-location копии на других серверах живы).
    """
    if not _check_admin_rate_limit(request):
        return web.json_response({"error": "rate_limited"}, status=429)
    if not _check_admin_secret(request):
        return web.json_response({"error": "forbidden"}, status=403)

    server_id = _parse_path_int(request, "id")
    if server_id is None:
        return web.json_response({"error": "bad id"}, status=400)

    from services.database import (
        get_server_by_id, get_active_configs_for_migration,
        get_best_server, activate_config_slot, reset_config_slot,
        update_server_peer_count, audit_log_record, DB_PATH,
    )
    from services.vpnctl_client import provision_peer, revoke_peer, VpnctlError
    import aiosqlite

    dead_server = await get_server_by_id(server_id)
    if not dead_server:
        return web.json_response({"error": "server not found"}, status=404)
    if dead_server.get("is_active"):
        return web.json_response({"error": "drain server first (is_active=1)"}, status=400)

    # Pre-check + acquire per-server lock. lock.locked() возвращает 409
    # моментально без блокировки в очереди — миграция длится минутами,
    # admin не должен фризиться в ожидании первого вызова.
    lock = _migrate_lock_for(server_id)
    if lock.locked():
        return web.json_response(
            {"error": "Migration already in progress for this server"},
            status=409,
        )

    async with lock:
        configs = await get_active_configs_for_migration(server_id)
        bot: Bot = request.app["bot"]

        migrated = 0
        reset_vless = 0
        skipped = 0  # configs уже мигрированные параллельным вызовом
        failed = 0
        failures: list[dict] = []
        notified: set[int] = set()

        for cfg in configs:
            config_id = cfg["id"]
            user_id   = cfg["user_id"]
            protocol  = cfg["protocol"]

            # Idempotency check: re-fetch server_id для config'а ДО любых
            # внешних вызовов. Параллельный migrate-run (или admin/refund)
            # мог уже переместить config на другой сервер; повторный
            # provision_peer создаст duplicate peer + activate_config_slot
            # overwrite'ит результат первого run'а → orphan peer навсегда.
            try:
                async with aiosqlite.connect(DB_PATH) as _db:
                    async with _db.execute(
                        "SELECT server_id, status FROM configs WHERE id=?",
                        (config_id,),
                    ) as _cur:
                        _row = await _cur.fetchone()
                current_server_id = _row[0] if _row else None
                current_status = _row[1] if _row else None
            except Exception as _e:
                logger.warning("migrate idempotency-check failed cfg=%d: %s", config_id, _e)
                current_server_id = None
                current_status = None

            if current_server_id != server_id or current_status != "active":
                logger.info(
                    "migrate skip cfg=%d: уже мигрирован (server=%s status=%s, ожидали server=%d active)",
                    config_id, current_server_id, current_status, server_id,
                )
                skipped += 1
                continue

            try:
                if protocol == "vless":
                    # Multi-location: drop this dead-server record; other-server copies are intact.
                    await reset_config_slot(config_id)
                    await update_server_peer_count(server_id, -1)
                    reset_vless += 1
                    # Notify user: their VLESS location was removed, but multi-location means
                    # other servers still work for them. They should re-fetch sub URL in Happ.
                    try:
                        if user_id not in notified:
                            notified.add(user_id)
                            from services.database import get_user_lang as _gul_sd
                            from services.i18n_bot import t as _i18n_t_sd
                            _lang_sd = await _gul_sd(user_id) or "ru"
                            await bot.send_message(
                                user_id,
                                _i18n_t_sd(_lang_sd, "bot_server_decom"),
                                parse_mode="HTML",
                            )
                    except Exception as e:
                        logger.warning("VLESS migrate notify user=%d: %s", user_id, e)
                    continue

                # AWG / plain WG — single-server, must migrate
                target = await get_best_server(protocol)
                if not target or target["id"] == server_id:
                    failures.append({"config_id": config_id, "error": "no available server"})
                    failed += 1
                    continue

                label = f"user_{user_id}_{protocol}_{config_id}"
                peer  = await provision_peer(target, label, protocol)
                peer_ip = (peer.extra or {}).get("assigned_ip", "")

                # Best-effort: remove old peer from dead server
                old_peer_id = cfg.get("wg_pubkey") or ""
                if old_peer_id:
                    await revoke_peer(dead_server, old_peer_id, protocol)

                await activate_config_slot(
                    config_id, label, peer.config,
                    server_id=target["id"],
                    wg_pubkey=peer.id,
                    assigned_ip=peer_ip,
                )
                await update_server_peer_count(target["id"], +1)
                await update_server_peer_count(server_id, -1)
                migrated += 1

                if user_id not in notified:
                    notified.add(user_id)
                    srv_name = " ".join(filter(None, [
                        (target.get("flag") or "").strip(),
                        (target.get("name") or "").strip(),
                    ])) or f"Server {target['id']}"
                    try:
                        from services.database import get_user_lang as _gul_sm
                        from services.i18n_bot import t as _i18n_t_sm
                        _lang_sm = await _gul_sm(user_id) or "ru"
                        await bot.send_message(
                            user_id,
                            _i18n_t_sm(
                                _lang_sm, "bot_server_migration",
                                server=srv_name, url=WEBAPP_URL,
                            ),
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                        )
                    except Exception as notify_err:
                        logger.warning("migrate notify user=%d: %s", user_id, notify_err)

            except VpnctlError as e:
                logger.warning("migrate config=%d: %s", config_id, e)
                failed += 1
                if len(failures) < 10:
                    failures.append({"config_id": config_id, "error": str(e)[:200]})
            except Exception as e:
                logger.error("migrate config=%d: %s", config_id, e, exc_info=True)
                failed += 1
                if len(failures) < 10:
                    failures.append({"config_id": config_id, "error": str(e)[:200]})

        await audit_log_record(
            admin_id=0, action="server_migrate",
            target=f"server:{server_id}",
            details=f"migrated={migrated} reset_vless={reset_vless} skipped={skipped} failed={failed}",
        )

    return web.json_response({
        "ok": True,
        "migrated": migrated,
        "reset_vless": reset_vless,
        "skipped": skipped,
        "failed": failed,
        "failures": failures,
    })


async def handle_admin_user_unban(request: web.Request) -> web.Response:
    """POST /api/admin/user/{id}/unban — снимает бан."""
    if not _check_admin_rate_limit(request):
        return web.json_response({"error": "rate_limited"}, status=429)
    if not _check_admin_secret(request):
        return web.json_response({"error": "forbidden"}, status=403)

    user_id = _parse_path_int(request, "id")
    if user_id is None:
        return web.json_response({"error": "bad id"}, status=400)
    if user_id in ADMIN_IDS or user_id == ADMIN_ID:
        return web.json_response(
            {"error": "Cannot ban an admin user"},
            status=400,
        )

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
            "oxapay":    OXAPAY_ENABLED,
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
    app.router.add_post("/api/admin/grant_subscription",   handle_admin_grant_subscription)
    app.router.add_get ("/api/admin/grants",               handle_admin_grants_list)
    app.router.add_post("/api/admin/servers/{id}/backfill-vless",   handle_admin_vless_backfill)
    app.router.add_post("/api/admin/servers/{id}/migrate-configs",  handle_admin_migrate_configs)
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

    # OxaPay — крипто-шлюз, HMAC-SHA512. То же правило: endpoint'ы всегда
    # зарегистрированы, без OXAPAY_ENABLED отдают 503.
    app.router.add_post("/api/vpn/invoice/oxapay",         handle_oxapay_invoice)
    app.router.add_post("/api/oxapay/webhook",             handle_oxapay_webhook)

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

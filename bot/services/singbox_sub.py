"""
Sing-box subscription generator: превращает список vless:// URLs в sing-box
JSON-config с встроенным RU split-tunneling. Happ читает этот формат
напрямую — sing-box внутри Happ применяет route.rules и не тоннелит трафик
к Сбер/Кинопоиск/Госуслуги.

Используется `/sub/{token}` (default). При `?mode=full` бот отдаёт plain
base64 vless список (как раньше) — escape-hatch для клиентов которые
sing-box не парсят (Streisand, V2Box, AmneziaVPN).
"""
import json
import logging
from urllib.parse import parse_qs, unquote, urlsplit

from services.xray_rules import GEOIP_RU_FILE, GEOSITE_RU_INSIDE_FILE

logger = logging.getLogger(__name__)


def _parse_vless_url(url: str) -> dict | None:
    """Парсит `vless://uuid@host:port?params#fragment` → sing-box outbound.
    Возвращает None если строка не похожа на VLESS (мы и не пытаемся обработать
    другие протоколы — у нас только VLESS на серверах)."""
    if not url.startswith("vless://"):
        return None
    try:
        parts = urlsplit(url)
        uuid_ = parts.username or ""
        host = parts.hostname or ""
        port = parts.port or 443
        if not uuid_ or not host:
            return None

        q = parse_qs(parts.query)
        # parse_qs возвращает list[str]; берём первый.
        def g(k: str, default: str = "") -> str:
            v = q.get(k)
            return v[0] if v else default

        tag = unquote(parts.fragment) if parts.fragment else f"{host}:{port}"
        flow = g("flow")
        network = g("type", "tcp")
        security = g("security", "none")

        ob: dict = {
            "type": "vless",
            "tag": tag,
            "server": host,
            "server_port": port,
            "uuid": uuid_,
            # packet_encoding xudp — стандарт для VLESS+Vision/Reality, sing-box
            # ожидает этот ключ; без него UDP-сёрфинг работает криво в некоторых
            # сценариях. Inherently safe для всех вариантов VLESS.
            "packet_encoding": "xudp",
        }
        if flow:
            ob["flow"] = flow

        # TLS / Reality / WebSocket / gRPC — раскладываем по веткам, как Happ
        # ожидает (sing-box 1.8+ schema).
        if security in ("tls", "reality"):
            tls: dict = {"enabled": True}
            sni = g("sni")
            if sni:
                tls["server_name"] = sni
            fp = g("fp")
            if fp:
                tls["utls"] = {"enabled": True, "fingerprint": fp}
            alpn = g("alpn")
            if alpn:
                # Comma-separated в URL → list в sing-box.
                tls["alpn"] = [a.strip() for a in alpn.split(",") if a.strip()]
            if security == "reality":
                pbk = g("pbk")
                sid = g("sid")
                reality: dict = {"enabled": True}
                if pbk:
                    reality["public_key"] = pbk
                if sid:
                    reality["short_id"] = sid
                tls["reality"] = reality
            ob["tls"] = tls

        if network == "ws":
            transport: dict = {"type": "ws"}
            path = g("path")
            if path:
                transport["path"] = path
            host_hdr = g("host")
            if host_hdr:
                transport["headers"] = {"Host": host_hdr}
            ob["transport"] = transport
        elif network == "grpc":
            service = g("serviceName")
            ob["transport"] = {"type": "grpc"}
            if service:
                ob["transport"]["service_name"] = service

        return ob
    except Exception as e:
        logger.warning("vless parse failed for %r: %s", url[:80], e)
        return None


def build_singbox_config(
    vless_urls: list[str],
    *,
    rules_base_url: str,
    profile_title: str = "MAX VPN",
) -> dict:
    """Собирает sing-box config из списка vless:// URL'ов.

    `rules_base_url` — origin под которым раздаются .srs файлы
    (например, `https://maxvpnesim.com/static/xray-rules`). Sing-box скачает
    их один раз и кеширует локально, обновляя по auto-update интервалу.

    Routing:
        - `geosite:ru-available-only-inside` → direct (Сбер, Кинопоиск, etc.)
        - `geoip:ru`                         → direct (вся RU IP-вселенная)
        - Всё остальное                      → выбранный VLESS-сервер
    """
    outbounds: list[dict] = []
    vless_tags: list[str] = []

    for url in vless_urls:
        ob = _parse_vless_url(url)
        if ob:
            outbounds.append(ob)
            vless_tags.append(ob["tag"])

    # Селектор поверх всех VLESS — в Happ выглядит как «выбери локацию».
    # Без него Happ возьмёт первый outbound и не покажет UI выбора.
    selector_tag = f"🌐 {profile_title}"
    outbounds.insert(0, {
        "type": "selector",
        "tag": selector_tag,
        "outbounds": vless_tags or ["direct"],
        "default": vless_tags[0] if vless_tags else "direct",
        "interrupt_exist_connections": False,
    })

    # Системные outbounds: direct (для bypass), block (на случай rule deny),
    # dns (для DNS-routing). Без direct routing.rules не сработают.
    outbounds.extend([
        {"type": "direct", "tag": "direct"},
        {"type": "block",  "tag": "block"},
        {"type": "dns",    "tag": "dns-out"},
    ])

    base = rules_base_url.rstrip("/")
    config = {
        "log": {"level": "warn", "timestamp": True},
        "dns": {
            "servers": [
                {"tag": "remote", "address": "tls://1.1.1.1",
                 "detour": selector_tag},
                {"tag": "direct", "address": "tls://77.88.8.8",
                 "detour": "direct"},
                {"tag": "block",  "address": "rcode://success"},
            ],
            "rules": [
                # DNS для RU-доменов резолвим через Яндекс DNS (direct) —
                # иначе резолв уходит в туннель, что а) медленнее,
                # б) показывает VPN-провайдеру что юзер ходит на банк.
                {"rule_set": ["geosite-ru-inside"], "server": "direct"},
            ],
            "final": "remote",
            "strategy": "prefer_ipv4",
            "independent_cache": True,
        },
        "inbounds": [
            # Happ сам подкладывает inbound (mixed/tun) — нам тут не нужно.
        ],
        "outbounds": outbounds,
        "route": {
            "rules": [
                {"protocol": "dns", "outbound": "dns-out"},
                # Локальные / private диапазоны — direct (LAN, loopback).
                {"ip_is_private": True, "outbound": "direct"},
                # RU services bypass: домены сначала (быстрее), потом IP (catch-all).
                {"rule_set": ["geosite-ru-inside"], "outbound": "direct"},
                {"rule_set": ["geoip-ru"],          "outbound": "direct"},
            ],
            "rule_set": [
                {
                    "tag": "geoip-ru",
                    "type": "remote",
                    "format": "binary",
                    "url": f"{base}/{GEOIP_RU_FILE}",
                    "download_detour": "direct",
                    "update_interval": "24h",
                },
                {
                    "tag": "geosite-ru-inside",
                    "type": "remote",
                    "format": "binary",
                    "url": f"{base}/{GEOSITE_RU_INSIDE_FILE}",
                    "download_detour": "direct",
                    "update_interval": "24h",
                },
            ],
            "final": selector_tag,
            "auto_detect_interface": True,
        },
    }
    return config


def serialize_config(config: dict) -> str:
    """Сериализует sing-box config в pretty JSON (Happ парсит и pretty, и compact;
    pretty помогает дебагу когда юзер шлёт скриншот «вот что у меня»)."""
    return json.dumps(config, ensure_ascii=False, indent=2)

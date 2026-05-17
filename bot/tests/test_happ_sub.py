"""
Happ subscription generator (xray-core JSON-array).

Замена sing-box формата (отвергался Happ'ом «Неверный формат JSON
конфигурации» 17.05).  Happ ест xray-core: `routing.rules` с inline
`geosite:` / `geoip:`, без `route.rule_set` remote URL'ов.
"""
import json

import pytest

from services.happ_sub import (
    _parse_vless_url,
    _build_stream_settings,
    _build_vless_outbound,
    build_happ_subscription,
    serialize,
)


# ── URL parsing ──────────────────────────────────────────────────────────────

def test_parse_basic_reality():
    url = (
        "vless://uuid-1@example.com:443"
        "?security=reality&sni=cf.com&fp=chrome&pbk=KEY&sid=01"
        "&flow=xtls-rprx-vision&type=tcp"
        "#%F0%9F%87%B3%F0%9F%87%B1%20Amsterdam"
    )
    p = _parse_vless_url(url)
    assert p is not None
    assert p["server"] == "example.com"
    assert p["port"] == 443
    assert p["uuid"] == "uuid-1"
    assert p["tag"].endswith("Amsterdam")
    assert p["params"]["security"] == "reality"
    assert p["params"]["pbk"] == "KEY"
    assert p["params"]["flow"] == "xtls-rprx-vision"


def test_parse_no_fragment():
    p = _parse_vless_url("vless://u@e.com:443?security=tls")
    assert p is not None
    assert p["tag"] == "e.com:443"


def test_parse_non_vless():
    assert _parse_vless_url("ss://garbage") is None
    assert _parse_vless_url("") is None


def test_parse_missing_uuid():
    assert _parse_vless_url("vless://@host.com:443") is None


# ── streamSettings (xray-core schema) ────────────────────────────────────────

def test_stream_reality():
    ss = _build_stream_settings({
        "type": "tcp", "security": "reality",
        "sni": "cf.com", "fp": "chrome", "pbk": "KEY", "sid": "01",
    })
    assert ss["network"] == "tcp"
    assert ss["security"] == "reality"
    rs = ss["realitySettings"]
    assert rs["serverName"] == "cf.com"
    assert rs["fingerprint"] == "chrome"
    assert rs["publicKey"] == "KEY"
    assert rs["shortId"] == "01"
    # Никаких sing-box-эквивалентов
    assert "utls" not in ss


def test_stream_tls():
    ss = _build_stream_settings({
        "type": "tcp", "security": "tls",
        "sni": "cf.com", "fp": "chrome", "alpn": "h2,http/1.1",
    })
    assert ss["security"] == "tls"
    assert ss["tlsSettings"]["serverName"] == "cf.com"
    assert ss["tlsSettings"]["alpn"] == ["h2", "http/1.1"]


def test_stream_ws():
    ss = _build_stream_settings({
        "type": "ws", "security": "none",
        "path": "/v2", "host": "cdn.example.com",
    })
    assert ss["network"] == "ws"
    assert ss["wsSettings"]["path"] == "/v2"
    assert ss["wsSettings"]["headers"]["Host"] == "cdn.example.com"


def test_stream_grpc():
    ss = _build_stream_settings({
        "type": "grpc", "security": "tls", "serviceName": "mysvc",
    })
    assert ss["network"] == "grpc"
    assert ss["grpcSettings"]["serviceName"] == "mysvc"


def test_stream_plain_tcp_no_security():
    ss = _build_stream_settings({"type": "tcp"})
    assert ss["security"] == "none"
    assert "realitySettings" not in ss
    assert "tlsSettings" not in ss


# ── VLESS outbound structure ─────────────────────────────────────────────────

def test_vless_outbound_xray_schema():
    """xray-core требует encryption:'none' в settings.vnext[].users[]
    + flow внутри users (не на outbound level как у sing-box).
    """
    p = _parse_vless_url(
        "vless://uuid-1@e.com:443?security=reality&pbk=K&sid=00"
        "&flow=xtls-rprx-vision#NL"
    )
    ob = _build_vless_outbound(p)
    assert ob["protocol"] == "vless"
    assert ob["tag"] == "proxy"
    vnext = ob["settings"]["vnext"][0]
    assert vnext["address"] == "e.com"
    assert vnext["port"] == 443
    user = vnext["users"][0]
    assert user["id"] == "uuid-1"
    assert user["encryption"] == "none"  # xray требует явно
    assert user["flow"] == "xtls-rprx-vision"
    assert user["level"] == 0


# ── full config build ────────────────────────────────────────────────────────

def test_build_returns_json_array():
    """Happ ожидает JSON-массив для multi-server subscription."""
    urls = [
        "vless://u1@a.com:443?security=reality&pbk=K1&sid=01#NL",
        "vless://u2@b.com:443?security=reality&pbk=K2&sid=02#DE",
    ]
    configs = build_happ_subscription(urls)
    assert isinstance(configs, list)
    assert len(configs) == 2
    assert configs[0]["remarks"] == "NL"
    assert configs[1]["remarks"] == "DE"


def test_each_config_has_three_outbounds():
    """proxy + direct + block — обязательная троица для bypass routing."""
    configs = build_happ_subscription(
        ["vless://u@a.com:443?security=reality&pbk=K&sid=01#A"],
    )
    obs = configs[0]["outbounds"]
    tags = [o["tag"] for o in obs]
    assert tags == ["proxy", "direct", "block"]
    assert obs[1]["protocol"] == "freedom"
    assert obs[2]["protocol"] == "blackhole"


def test_routing_has_ru_bypass_rules():
    """`routing.rules` должны иметь explicit domain list → direct,
    inline RU CIDRs → direct, остальное → proxy.  17.05: geosite/geoip
    references заменены на inline т.к. Happ не bundled .dat файлов."""
    configs = build_happ_subscription(
        ["vless://u@a.com:443?security=reality&pbk=K&sid=01#A"],
    )
    rules = configs[0]["routing"]["rules"]
    # Первое правило — explicit domain list для Yandex/Сбер/etc.
    assert any("yandex.ru" in d for d in rules[0]["domain"])
    assert rules[0]["outboundTag"] == "direct"
    # Второе — inline RU CIDR + private LAN
    assert any("77.88" in ip for ip in rules[1]["ip"])
    assert any("10.0.0.0" in ip for ip in rules[1]["ip"])  # private LAN
    assert rules[1]["outboundTag"] == "direct"
    # Последнее — всё остальное в proxy
    assert rules[-1]["outboundTag"] == "proxy"


def test_routing_has_domain_strategy():
    """`IPIfNonMatch` — резолвим IP только если domain не сматчился,
    избегаем лишних DNS-запросов."""
    configs = build_happ_subscription(
        ["vless://u@a.com:443?security=reality&pbk=K&sid=01#A"],
    )
    assert configs[0]["routing"]["domainStrategy"] == "IPIfNonMatch"


def test_no_sing_box_keys():
    """Регрессия: в конфиге не должно быть sing-box-only ключей.
    Если они появятся — Happ снова откажет.
    """
    configs = build_happ_subscription(
        ["vless://u@a.com:443?security=reality&pbk=K&sid=01#A"],
    )
    cfg = configs[0]
    # sing-box использует "route" (не "routing")
    assert "route" not in cfg
    # sing-box использует "rule_set" вложенно
    raw = json.dumps(cfg)
    assert "rule_set" not in raw
    # sing-box outbound tag — selector / urltest, не xray
    obs = cfg["outbounds"]
    for ob in obs:
        assert ob["protocol"] in ("vless", "freedom", "blackhole")
        # xray не имеет packet_encoding на outbound level
        assert "packet_encoding" not in ob


def test_serialize_returns_valid_json():
    configs = build_happ_subscription(
        ["vless://u@a.com:443?security=reality&pbk=K&sid=01#NL"],
    )
    raw = serialize(configs)
    parsed = json.loads(raw)
    assert isinstance(parsed, list)
    assert parsed[0]["remarks"] == "NL"


def test_serialize_keeps_emoji_unescaped():
    configs = build_happ_subscription(
        ["vless://u@a.com:443?security=reality&pbk=K&sid=01#🇳🇱 Амстердам"],
    )
    raw = serialize(configs)
    assert "🇳🇱" in raw
    assert "Амстердам" in raw


def test_malformed_urls_skipped():
    urls = [
        "vless://u@a.com:443?security=reality&pbk=K&sid=01#OK",
        "garbage://",
        "vless://@host.com",  # missing uuid
    ]
    configs = build_happ_subscription(urls)
    assert len(configs) == 1
    assert configs[0]["remarks"] == "OK"


def test_empty_input_returns_empty_array():
    """Caller должен fallback'нуться на plain base64 если все URL'ы битые."""
    assert build_happ_subscription([]) == []
    assert build_happ_subscription(["garbage"]) == []

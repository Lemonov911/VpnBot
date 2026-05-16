"""
Sing-box subscription generator: парсинг vless:// URL + сборка config.

Гарантии:
- VLESS Reality URLs парсятся в правильный sing-box outbound JSON
- Multi-location vless:// набор → selector со всеми локациями
- Route rules ссылаются на runetfreedom .srs через `rules_base_url`
- Битые URL пропускаются (не валим всю генерацию из-за одного)
"""
import json

import pytest

from services.singbox_sub import (
    _parse_vless_url,
    build_singbox_config,
    serialize_config,
)


# ── parser unit tests ────────────────────────────────────────────────────────

def test_parse_basic_reality_url():
    url = (
        "vless://uuid-1@example.com:443"
        "?security=reality&sni=cloudflare.com&fp=chrome&pbk=KEY&sid=01"
        "&flow=xtls-rprx-vision&type=tcp"
        "#%F0%9F%87%B3%F0%9F%87%B1%20Amsterdam"
    )
    ob = _parse_vless_url(url)
    assert ob is not None
    assert ob["type"] == "vless"
    assert ob["server"] == "example.com"
    assert ob["server_port"] == 443
    assert ob["uuid"] == "uuid-1"
    assert ob["flow"] == "xtls-rprx-vision"
    assert ob["tag"].endswith("Amsterdam")
    tls = ob["tls"]
    assert tls["enabled"] is True
    assert tls["server_name"] == "cloudflare.com"
    assert tls["utls"]["fingerprint"] == "chrome"
    reality = tls["reality"]
    assert reality["public_key"] == "KEY"
    assert reality["short_id"] == "01"


def test_parse_url_without_fragment_uses_host_port_tag():
    url = "vless://uuid-1@example.com:9443?security=reality&pbk=K&sid=00"
    ob = _parse_vless_url(url)
    assert ob is not None
    assert ob["tag"] == "example.com:9443"


def test_parse_url_with_ws_transport():
    url = "vless://uuid-1@example.com:443?security=tls&type=ws&path=%2Fws&host=cdn.example.com#WS"
    ob = _parse_vless_url(url)
    assert ob is not None
    assert ob["transport"]["type"] == "ws"
    assert ob["transport"]["path"] == "/ws"
    assert ob["transport"]["headers"]["Host"] == "cdn.example.com"


def test_parse_url_with_grpc_transport():
    url = "vless://uuid-1@example.com:443?security=tls&type=grpc&serviceName=grpcsvc#grpc"
    ob = _parse_vless_url(url)
    assert ob is not None
    assert ob["transport"]["type"] == "grpc"
    assert ob["transport"]["service_name"] == "grpcsvc"


def test_parse_url_without_security_no_tls_block():
    url = "vless://uuid-1@example.com:80?type=tcp#plain"
    ob = _parse_vless_url(url)
    assert ob is not None
    assert "tls" not in ob


def test_parse_non_vless_returns_none():
    assert _parse_vless_url("ss://something") is None
    assert _parse_vless_url("") is None
    assert _parse_vless_url("not a url") is None


def test_parse_malformed_returns_none_without_crash():
    # username missing, host present — should fail validation gracefully
    assert _parse_vless_url("vless://@example.com:443") is None


# ── config-level integration ─────────────────────────────────────────────────

def test_build_config_with_multiple_servers_creates_selector():
    urls = [
        "vless://u1@a.example.com:443?security=reality&pbk=K1&sid=01#NL",
        "vless://u2@b.example.com:443?security=reality&pbk=K2&sid=02#DE",
    ]
    cfg = build_singbox_config(
        urls, rules_base_url="https://x.test/static/xray-rules",
    )

    selector = next(o for o in cfg["outbounds"] if o["type"] == "selector")
    assert "NL" in selector["outbounds"]
    assert "DE" in selector["outbounds"]
    # selector default = первый сервер из списка
    assert selector["default"] == "NL"


def test_build_config_drops_malformed_urls_silently():
    urls = [
        "vless://u1@a.example.com:443?security=reality&pbk=K&sid=01#OK",
        "ss://garbage",
        "vless://@malformed",
    ]
    cfg = build_singbox_config(urls, rules_base_url="https://x.test/r")
    # Только OK-сервер
    vless_obs = [o for o in cfg["outbounds"] if o["type"] == "vless"]
    assert len(vless_obs) == 1
    assert vless_obs[0]["tag"] == "OK"


def test_build_config_route_rules_have_ru_bypass():
    cfg = build_singbox_config(
        ["vless://u@a.example.com:443?security=reality&pbk=K&sid=01#NL"],
        rules_base_url="https://x.test/r",
    )
    rules = cfg["route"]["rules"]
    rule_outbounds = [r.get("outbound") for r in rules]
    # DNS, private — стандартные защитные, затем geosite-ru + geoip-ru → direct
    assert "dns-out" in rule_outbounds
    assert rule_outbounds.count("direct") >= 3  # private + geosite + geoip


def test_build_config_rule_set_points_to_runetfreedom_files():
    cfg = build_singbox_config(
        ["vless://u@a.example.com:443?security=reality&pbk=K&sid=01#NL"],
        rules_base_url="https://x.test/r",
    )
    rs = cfg["route"]["rule_set"]
    urls = {r["tag"]: r["url"] for r in rs}
    assert urls["geoip-ru"].endswith("/geoip-ru.srs")
    assert urls["geosite-ru-inside"].endswith(
        "/geosite-ru-available-only-inside.srs",
    )
    # rules_base_url применился без trailing slash дубля
    assert "//geoip-ru" not in urls["geoip-ru"]


def test_build_config_includes_direct_block_dns_outbounds():
    """Без direct outbound правила «→ direct» молча сломаются."""
    cfg = build_singbox_config(
        ["vless://u@a.example.com:443?security=reality&pbk=K&sid=01#NL"],
        rules_base_url="https://x.test/r",
    )
    tags = {o["tag"] for o in cfg["outbounds"]}
    assert "direct" in tags
    assert "block" in tags
    assert "dns-out" in tags


def test_serialize_is_valid_json():
    cfg = build_singbox_config(
        ["vless://u@a.example.com:443?security=reality&pbk=K&sid=01#NL"],
        rules_base_url="https://x.test/r",
    )
    raw = serialize_config(cfg)
    # Round-trip → JSON валиден
    parsed = json.loads(raw)
    assert parsed["route"]["final"].startswith("🌐 ")


def test_serialize_keeps_emojis_unescaped():
    # Profile-Title с эмодзи и кириллицей — sing-box умеет UTF-8 без escape.
    cfg = build_singbox_config(
        ["vless://u@a.example.com:443?security=reality&pbk=K&sid=01#🇳🇱 Амстердам"],
        rules_base_url="https://x.test/r",
        profile_title="База",
    )
    raw = serialize_config(cfg)
    assert "🇳🇱" in raw
    assert "Амстердам" in raw
    assert "База" in raw

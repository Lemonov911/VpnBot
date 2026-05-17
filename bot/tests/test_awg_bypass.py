"""
AWG bypass: компьютит «0.0.0.0/0 минус RU CIDRs» через set subtraction,
переписывает AllowedIPs в .conf при download.

Тесты — pure unit (без сетевых запросов): подаём фейковый RU список,
проверяем что результат покрывает всё кроме него.
"""
import pytest

from services.awg_bypass import (
    _compute_bypass_cidrs,
    _format_allowedips,
    rewrite_allowedips,
)


def _net_to_range(net):
    return int(net.network_address), int(net.broadcast_address)


def _union_covers_universe_minus_blocked(bypass_nets, blocked_ints):
    """Проверяет: объединение bypass_nets + blocked_ints = 0..2^32-1."""
    covered = sum(net.num_addresses for net in bypass_nets)
    return covered + len(blocked_ints) == 2**32


def _union_disjoint(bypass_nets, ru_nets):
    """Проверяет: bypass и RU не пересекаются нигде."""
    for b in bypass_nets:
        for r in ru_nets:
            if b.overlaps(r):
                return False
    return True


# ── basic correctness ────────────────────────────────────────────────────────

def test_empty_ru_list_gives_full_universe():
    """Если нет RU CIDR — bypass = весь IPv4."""
    bypass = _compute_bypass_cidrs([])
    total = sum(n.num_addresses for n in bypass)
    assert total == 2**32


def test_single_block_excluded():
    """RU = 10.0.0.0/8 → bypass должен покрыть всё кроме него."""
    from ipaddress import IPv4Network
    ru = [IPv4Network("10.0.0.0/8")]
    bypass = _compute_bypass_cidrs(["10.0.0.0/8"])

    # bypass + ru = весь IPv4
    bypass_total = sum(n.num_addresses for n in bypass)
    ru_total = sum(n.num_addresses for n in ru)
    assert bypass_total + ru_total == 2**32

    # bypass и ru не пересекаются
    assert _union_disjoint(bypass, ru)


def test_multiple_disjoint_blocks_excluded():
    """RU = 2 непересекающихся блока — оба исключены."""
    from ipaddress import IPv4Network
    ru_cidrs = ["10.0.0.0/8", "192.168.0.0/16"]
    ru_nets = [IPv4Network(c) for c in ru_cidrs]
    bypass = _compute_bypass_cidrs(ru_cidrs)

    assert _union_disjoint(bypass, ru_nets)
    bypass_total = sum(n.num_addresses for n in bypass)
    ru_total = sum(n.num_addresses for n in ru_nets)
    assert bypass_total + ru_total == 2**32


def test_overlapping_ru_blocks_merged():
    """Если RU список содержит 10.0.0.0/8 и 10.1.0.0/16 — bypass = ~/8 (merged)."""
    bypass1 = _compute_bypass_cidrs(["10.0.0.0/8"])
    bypass2 = _compute_bypass_cidrs(["10.0.0.0/8", "10.1.0.0/16"])  # вложенный
    # Результат идентичен — внутренний blob поглощён 10.0.0.0/8
    assert _format_allowedips(bypass1) == _format_allowedips(bypass2)


def test_adjacent_ru_blocks_merged():
    """10.0.0.0/9 + 10.128.0.0/9 = 10.0.0.0/8 → bypass учитывает это."""
    bypass_split = _compute_bypass_cidrs(["10.0.0.0/9", "10.128.0.0/9"])
    bypass_merged = _compute_bypass_cidrs(["10.0.0.0/8"])
    assert _format_allowedips(bypass_split) == _format_allowedips(bypass_merged)


def test_malformed_cidrs_skipped():
    """Битые строки игнорируются, остальное парсится."""
    bypass = _compute_bypass_cidrs(["10.0.0.0/8", "garbage", "", "not.an.ip/24"])
    # Должно быть как будто только 10.0.0.0/8 в списке
    bypass_clean = _compute_bypass_cidrs(["10.0.0.0/8"])
    assert _format_allowedips(bypass) == _format_allowedips(bypass_clean)


def test_comments_skipped():
    """Строки начинающиеся с # — комментарии."""
    bypass = _compute_bypass_cidrs(["# header", "10.0.0.0/8", "  ", "# foo"])
    bypass_clean = _compute_bypass_cidrs(["10.0.0.0/8"])
    assert _format_allowedips(bypass) == _format_allowedips(bypass_clean)


def test_realistic_ru_list_size():
    """С 11k RU CIDR результат должен быть не > 30k bypass (sanity).
    Линейный рост быстро бы сделал .conf неюзабельным."""
    # Синтетический список 100 разреженных /24
    ru = [f"10.{i}.{j}.0/24" for i in range(10) for j in range(10)]
    bypass = _compute_bypass_cidrs(ru)
    assert len(bypass) < 1000, "bypass blew up — слишком много CIDR"


# ── rewrite_allowedips ──────────────────────────────────────────────────────

SAMPLE_AWG_CONF = """[Interface]
PrivateKey = abc123
Address = 10.0.0.2/32
DNS = 1.1.1.1
Jc = 5
Jmin = 50
Jmax = 1000

[Peer]
PublicKey = xyz789
AllowedIPs = 0.0.0.0/0
Endpoint = vpn.example.com:51820
PersistentKeepalive = 25
"""


def test_rewrite_replaces_full_tunnel_with_bypass():
    bypass = "1.0.0.0/8, 2.0.0.0/8, 3.0.0.0/8"
    result = rewrite_allowedips(SAMPLE_AWG_CONF, bypass)
    # bypass для IPv4 + ::/0 чтобы IPv6 не утекал
    assert "AllowedIPs = 1.0.0.0/8, 2.0.0.0/8, 3.0.0.0/8, ::/0" in result
    assert "AllowedIPs = 0.0.0.0/0" not in result


def test_rewrite_appends_ipv6_catch_all():
    """Защита от IPv6 leak: bypass — только IPv4, но AllowedIPs должен включать
    ::/0 чтобы IPv6 шёл в туннель а не мимо."""
    result = rewrite_allowedips(SAMPLE_AWG_CONF, "1.0.0.0/8")
    assert ", ::/0" in result


def test_rewrite_keeps_other_lines_intact():
    """Только AllowedIPs меняется, остальные поля — нет."""
    bypass = "1.0.0.0/8"
    result = rewrite_allowedips(SAMPLE_AWG_CONF, bypass)
    # AmneziaWG-специфичные поля
    assert "Jc = 5" in result
    assert "Jmin = 50" in result
    assert "Jmax = 1000" in result
    # Peer fields
    assert "Endpoint = vpn.example.com:51820" in result
    assert "PublicKey = xyz789" in result
    assert "PersistentKeepalive = 25" in result


def test_rewrite_idempotent():
    """Повторный rewrite с тем же bypass — no-op."""
    bypass = "1.0.0.0/8, 2.0.0.0/8"
    once = rewrite_allowedips(SAMPLE_AWG_CONF, bypass)
    twice = rewrite_allowedips(once, bypass)
    assert once == twice


def test_rewrite_no_op_when_no_allowedips_line():
    """Если .conf без AllowedIPs (например VLESS-фейк) — оригинал возвращается."""
    conf = "[Some]\nfoo = bar\n"
    result = rewrite_allowedips(conf, "1.0.0.0/8")
    assert result == conf


def test_rewrite_no_op_when_bypass_empty():
    result = rewrite_allowedips(SAMPLE_AWG_CONF, "")
    assert result == SAMPLE_AWG_CONF


@pytest.mark.asyncio
async def test_fetch_rejects_suspiciously_short_response(tmp_path, monkeypatch):
    """GitHub maintenance page / captcha / broken redirect → ответ 200 OK с
    HTML внутри. ru_lines = 0-2 строки. Без sanity-check'а помещали бы это
    в кеш → bypass становится 0.0.0.0/0 (full-tunnel) тихо. Защита."""
    from unittest.mock import patch
    from services import awg_bypass

    monkeypatch.setattr(awg_bypass, "RULES_DIR", tmp_path)

    class FakeResp:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        def raise_for_status(self): pass
        async def text(self): return "<html><body>maintenance</body></html>"

    class FakeSess:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        def get(self, url): return FakeResp()

    with patch("aiohttp.ClientSession", return_value=FakeSess()):
        stats = await awg_bypass.refresh_bypass(force=True)

    assert stats["error"] is not None
    assert "suspicious" in stats["error"].lower()
    # bypass файл НЕ записан
    assert not (tmp_path / awg_bypass.BYPASS_CACHE_FILE).exists()


def test_rewrite_handles_extra_whitespace():
    """AllowedIPs с разным форматом — кейсы из реальных .conf."""
    conf_with_spaces = SAMPLE_AWG_CONF.replace(
        "AllowedIPs = 0.0.0.0/0", "AllowedIPs    =  0.0.0.0/0, ::/0",
    )
    result = rewrite_allowedips(conf_with_spaces, "1.0.0.0/8")
    assert "AllowedIPs = 1.0.0.0/8" in result
    # Старый 0.0.0.0/0 удалился, ::/0 переставлен в конец append'ом
    assert "0.0.0.0/0" not in result
    assert ", ::/0" in result  # IPv6 catch-all всегда в конце

"""
xray-rules fetcher: качает runetfreedom sing-box.zip раз в 6ч, распаковывает
2 нужных .srs файла. Здесь — unit-тесты с MOCK HTTP (без сетевых запросов).

Не покрываем: реальный download с GitHub (это integration, не unit).
"""
import io
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


def _make_fake_zip() -> bytes:
    """Собирает minimal sing-box.zip с двумя нужными файлами + одним лишним."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("rule-set-geoip/geoip-ru.srs", b"FAKE_GEOIP_RU_BYTES")
        zf.writestr(
            "rule-set-geosite/geosite-ru-available-only-inside.srs",
            b"FAKE_GEOSITE_RU_INSIDE_BYTES",
        )
        # Лишний файл — fetcher должен его игнорить
        zf.writestr("rule-set-geoip/geoip-ir.srs", b"unrelated")
    return buf.getvalue()


@pytest.fixture
def isolated_rules_dir(tmp_path, monkeypatch):
    """Изолированный RULES_DIR на per-test основе."""
    from services import xray_rules
    monkeypatch.setattr(xray_rules, "RULES_DIR", tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_fetch_extracts_only_needed_files(isolated_rules_dir):
    """Из zip'а с 3 файлами достаются только 2 нужных, остальные игнорятся."""
    from services import xray_rules

    fake_zip = _make_fake_zip()

    class FakeResp:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        def raise_for_status(self): pass
        async def read(self): return fake_zip

    class FakeSess:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        def get(self, url): return FakeResp()

    with patch("aiohttp.ClientSession", return_value=FakeSess()):
        stats = await xray_rules.fetch_rules(force=True)

    assert stats["error"] is None
    assert set(stats["extracted"]) == {
        "geoip-ru.srs",
        "geosite-ru-available-only-inside.srs",
    }
    assert (isolated_rules_dir / "geoip-ru.srs").read_bytes() == b"FAKE_GEOIP_RU_BYTES"
    assert (isolated_rules_dir / "geosite-ru-available-only-inside.srs").read_bytes() \
        == b"FAKE_GEOSITE_RU_INSIDE_BYTES"
    # Лишний файл не записан
    assert not (isolated_rules_dir / "geoip-ir.srs").exists()


@pytest.mark.asyncio
async def test_fetch_skips_when_files_are_fresh(isolated_rules_dir):
    """Если оба файла существуют и младше STALE_AGE_SEC — не качаем."""
    from services import xray_rules

    (isolated_rules_dir / "geoip-ru.srs").write_bytes(b"cached")
    (isolated_rules_dir / "geosite-ru-available-only-inside.srs").write_bytes(b"cached")

    # Без force и без мока сети — если бы сеть звалась, тест бы упал на timeout.
    stats = await xray_rules.fetch_rules(force=False)
    assert stats["skipped"] is True
    assert stats["downloaded_bytes"] == 0


@pytest.mark.asyncio
async def test_fetch_force_redownloads_even_if_fresh(isolated_rules_dir):
    """force=True игнорит свежесть и качает заново."""
    from services import xray_rules

    (isolated_rules_dir / "geoip-ru.srs").write_bytes(b"old")
    (isolated_rules_dir / "geosite-ru-available-only-inside.srs").write_bytes(b"old")

    fake_zip = _make_fake_zip()

    class FakeResp:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        def raise_for_status(self): pass
        async def read(self): return fake_zip

    class FakeSess:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        def get(self, url): return FakeResp()

    with patch("aiohttp.ClientSession", return_value=FakeSess()):
        stats = await xray_rules.fetch_rules(force=True)

    assert stats["skipped"] is False
    assert (isolated_rules_dir / "geoip-ru.srs").read_bytes() == b"FAKE_GEOIP_RU_BYTES"


@pytest.mark.asyncio
async def test_fetch_network_error_doesnt_raise(isolated_rules_dir):
    """Сеть упала → stats.error выставлен, исключение не пробрасывается
    (cron в scheduler.py не должен падать из-за GitHub outage)."""
    from services import xray_rules

    class FakeSess:
        async def __aenter__(self): raise ConnectionError("network down")
        async def __aexit__(self, *a): return None

    with patch("aiohttp.ClientSession", return_value=FakeSess()):
        stats = await xray_rules.fetch_rules(force=True)

    assert stats["error"] is not None
    assert "network" in stats["error"].lower()


def test_rule_file_path_blocks_unknown_names(isolated_rules_dir):
    """Защита от path-traversal — даже если файл существует, нельзя выдать
    что не в _NEEDED_FILES whitelist."""
    from services import xray_rules

    (isolated_rules_dir / "../secret.txt").write_text("nope")  # путь относительный
    assert xray_rules.rule_file_path("../secret.txt") is None
    assert xray_rules.rule_file_path("random.srs") is None
    assert xray_rules.rule_file_path("") is None


def test_rule_file_path_returns_existing(isolated_rules_dir):
    from services import xray_rules
    (isolated_rules_dir / "geoip-ru.srs").write_bytes(b"data")
    p = xray_rules.rule_file_path("geoip-ru.srs")
    assert p is not None
    assert p.read_bytes() == b"data"


def test_rule_file_sha256(isolated_rules_dir):
    from services import xray_rules
    (isolated_rules_dir / "geoip-ru.srs").write_bytes(b"hello")
    h = xray_rules.rule_file_sha256("geoip-ru.srs")
    import hashlib
    assert h == hashlib.sha256(b"hello").hexdigest()

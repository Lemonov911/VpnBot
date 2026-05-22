"""
Regression: _parse_expires_at_utc (services/webapp_api.py) — устойчивый
парсер `expires_at` для /sub/{token} HTTP-header `expire=...`.

Раньше inline в handler с 3 format'ами + `except: pass`. Любой новый
формат БД (миграция, ISO с offset) → silent return 0 → Happ показывает
«expired» для живой подписки. Helper расширил format-список и логирует
warning через caller если не распарсилось.

DB-инвариант: expires_at хранится как UTC. Helper трактует все парсенные
naive datetime как UTC. ISO-offset suffix (`+00:00`, `+03:00`) отрезаем —
посторонний клиент мог записать time-zone-aware, но мы храним naive UTC.
"""
from datetime import datetime, timezone

import pytest

from services.webapp_api import _parse_expires_at_utc


def _expected_unix(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


@pytest.mark.parametrize("raw,expected_dt", [
    # SQLite default format (CURRENT_TIMESTAMP + datetime('now'))
    ("2026-05-22 19:30:45", datetime(2026, 5, 22, 19, 30, 45)),
    # ISO без microseconds (isoformat() для exact-second)
    ("2026-05-22T19:30:45", datetime(2026, 5, 22, 19, 30, 45)),
    # ISO с microseconds (Python datetime.isoformat() default)
    ("2026-05-22T19:30:45.123456",
     datetime(2026, 5, 22, 19, 30, 45, 123456)),
    # Date-only (legacy миграции)
    ("2026-05-22", datetime(2026, 5, 22)),
    # ISO с tz-offset — отрезаем (БД-инвариант = naive UTC)
    ("2026-05-22T19:30:45+00:00", datetime(2026, 5, 22, 19, 30, 45)),
    ("2026-05-22T19:30:45.500+03:00",
     datetime(2026, 5, 22, 19, 30, 45, 500000)),
    # Trailing 'Z' (UTC marker)
    ("2026-05-22T19:30:45Z", datetime(2026, 5, 22, 19, 30, 45)),
])
def test_parses_supported_formats(raw, expected_dt):
    assert _parse_expires_at_utc(raw) == _expected_unix(expected_dt), (
        f"format {raw!r} should parse to {expected_dt}"
    )


@pytest.mark.parametrize("garbage", [
    None,
    "",
    "not-a-date",
    "2026/05/22",
    "May 22 2026",
])
def test_unparseable_returns_zero(garbage):
    """None/empty/garbage → 0. Caller (handle_user_subscription) проверяет
    `if raw and unix == 0 → logger.warning` — это не silent."""
    assert _parse_expires_at_utc(garbage) == 0


def test_returns_positive_int_not_float():
    """Caller юзает `int(_time.time()) - 1` как fallback — типы должны match.
    expire= в HTTP header требует int (Happ не любит float)."""
    unix = _parse_expires_at_utc("2026-05-22 19:30:45")
    assert isinstance(unix, int)
    assert unix > 0

"""
Tests for bot/services/auth.py:verify_init_data.

Baseline (pre-fix) behavior. Tests A4 and A5 freeze the CURRENT
permissive behavior; after the planned 24h auth_date check lands,
they will need to flip to assert rejection.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import quote

import pytest

from services.auth import verify_init_data


def _build_init_data(bot_token: str, user: dict, *, auth_date: int | None,
                     extra: dict | None = None, tamper_hash: bool = False,
                     drop_hash: bool = False) -> str:
    """Crafts a valid (or tampered) initData string for testing."""
    pairs: dict[str, str] = {}
    if auth_date is not None:
        pairs["auth_date"] = str(auth_date)
    pairs["query_id"] = "AAH_test_query_id"
    pairs["user"] = json.dumps(user, separators=(",", ":"))
    if extra:
        pairs.update(extra)

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    sig = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if tamper_hash:
        sig = "0" * len(sig)

    parts = [f"{k}={quote(v, safe='')}" for k, v in pairs.items()]
    if not drop_hash:
        parts.append(f"hash={sig}")
    return "&".join(parts)


def test_A1_valid_initdata_returns_user(test_bot_token):
    """A1. Fresh valid initData → returns dict with id."""
    user = {"id": 12345, "first_name": "Alice", "username": "alice"}
    init_data = _build_init_data(test_bot_token, user, auth_date=int(time.time()))
    result = verify_init_data(init_data, test_bot_token)
    assert result is not None
    assert result["id"] == 12345
    assert result["first_name"] == "Alice"


def test_A2_tampered_hash_returns_none(test_bot_token):
    """A2. Tampered hash → None."""
    user = {"id": 12345}
    init_data = _build_init_data(test_bot_token, user,
                                  auth_date=int(time.time()), tamper_hash=True)
    assert verify_init_data(init_data, test_bot_token) is None


def test_A3_missing_hash_returns_none(test_bot_token):
    """A3. No hash field at all → None."""
    user = {"id": 12345}
    init_data = _build_init_data(test_bot_token, user,
                                  auth_date=int(time.time()), drop_hash=True)
    assert verify_init_data(init_data, test_bot_token) is None


def test_A4_old_authdate_currently_passes(test_bot_token):
    """A4. Valid HMAC but auth_date 2 days ago → CURRENT behavior: still passes.

    BASELINE: this records the bug that the upcoming fix will close.
    After the fix lands, flip this assertion to `assert result is None`
    and rename to test_A4_old_authdate_is_rejected.
    """
    two_days_ago = int(time.time()) - 2 * 24 * 3600
    user = {"id": 12345}
    init_data = _build_init_data(test_bot_token, user, auth_date=two_days_ago)
    result = verify_init_data(init_data, test_bot_token)
    # CURRENT: passes signature check, no auth_date age check.
    assert result is not None
    assert result["id"] == 12345


def test_A5_missing_authdate_currently_passes(test_bot_token):
    """A5. Valid HMAC, no auth_date field at all → CURRENT behavior: passes.

    BASELINE: missing auth_date should also be rejected by the fix.
    """
    user = {"id": 12345}
    init_data = _build_init_data(test_bot_token, user, auth_date=None)
    result = verify_init_data(init_data, test_bot_token)
    # CURRENT: no auth_date required.
    assert result is not None
    assert result["id"] == 12345


def test_empty_init_data_returns_none(test_bot_token):
    """Sanity: empty string → None."""
    assert verify_init_data("", test_bot_token) is None


def test_empty_token_returns_none():
    """Sanity: empty token → None."""
    assert verify_init_data("foo=bar&hash=abc", "") is None


def test_user_without_id_returns_none(test_bot_token):
    """Sanity: user JSON without id → None."""
    user = {"first_name": "no-id"}
    init_data = _build_init_data(test_bot_token, user, auth_date=int(time.time()))
    assert verify_init_data(init_data, test_bot_token) is None

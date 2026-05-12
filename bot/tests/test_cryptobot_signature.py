"""
Tests for bot/services/cryptobot.py:verify_signature.

Algorithm (CryptoBot docs):
  secret = sha256(token)
  expected = hmac_sha256(secret, body).hexdigest()
  compare against header `crypto-pay-api-signature`
"""
import hashlib
import hmac

from services.cryptobot import verify_signature


def _sign(body: bytes, token: str) -> str:
    secret = hashlib.sha256(token.encode()).digest()
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def test_B1_valid_signature():
    token = "TEST_CRYPTOBOT_TOKEN"
    body = b'{"update_type":"invoice_paid","payload":{}}'
    sig = _sign(body, token)
    assert verify_signature(body, sig, token) is True


def test_B2_wrong_signature():
    token = "TEST_CRYPTOBOT_TOKEN"
    body = b'{"update_type":"invoice_paid"}'
    # Same length, wrong content
    assert verify_signature(body, "a" * 64, token) is False


def test_B3_empty_signature():
    token = "TEST_CRYPTOBOT_TOKEN"
    body = b'{"x":1}'
    assert verify_signature(body, "", token) is False


def test_uppercase_signature_still_matches():
    """verify_signature lowercases the incoming signature — sanity check."""
    token = "TEST_CRYPTOBOT_TOKEN"
    body = b'{"a":1}'
    sig = _sign(body, token).upper()
    assert verify_signature(body, sig, token) is True

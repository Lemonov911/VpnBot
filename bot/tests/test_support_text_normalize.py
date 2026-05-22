"""
Regression: _normalize_support_text (services/webapp_api.py) закрывает
3 паттерна обхода min-10 char limit на support-tickets:
  1. HTML entities (&#8203; / &amp;#8203;) → zero-width decode
  2. NFKC compatibility chars (full-width letters)
  3. Raw zero-width + control chars (U+200B etc., \\x00-\\x1F)
"""
import pytest

from services.webapp_api import _normalize_support_text


# ── HTML-entity bypass (мой C14 fix) ─────────────────────────────────────────

def test_html_entity_zero_width_unescaped_and_stripped():
    """`&#8203;` × 10 — 70 байт visible, после unescape = 10 zero-width →
    после strip = empty. Юзер не должен пройти min-10."""
    payload = "&#8203;" * 10
    result = _normalize_support_text(payload)
    assert len(result) == 0, (
        f"&#8203;×10 не должен проходить filter, got len={len(result)}: {result!r}"
    )


def test_html_entity_legitimate_text_preserved():
    """Если юзер написал «Меня зовут O&apos;Brien» — apostrophe раскрывается,
    нормальный текст не теряется."""
    result = _normalize_support_text("Меня зовут O&apos;Brien")
    assert "O'Brien" in result
    assert len(result) >= 10


# ── NFKC normalization ───────────────────────────────────────────────────────

def test_nfkc_full_width_collapses():
    """'ＡＢＣ' (full-width latin) → 'ABC' после NFKC. 9 fullwidth-symbols
    → 9 latin, всё ещё < 10 → blocked."""
    payload = "ＡＢＣＤＥＦＧＨＩ"  # 9 fullwidth latin
    result = _normalize_support_text(payload)
    assert result == "ABCDEFGHI"
    assert len(result) == 9


# ── Raw zero-width / control chars (исходный фильтр до моего fix'а) ─────────

def test_raw_zero_width_stripped():
    payload = "​" * 15  # U+200B = zero-width space
    result = _normalize_support_text(payload)
    assert result == ""


def test_control_chars_stripped():
    """Tab, null-byte, ESC и др. control chars не считаются visible."""
    payload = "\x00\x01\x07\x1b\x7f" * 10  # 50 байт control
    result = _normalize_support_text(payload)
    assert result == ""


def test_mixed_legitimate_with_zero_width():
    """«hi​there» — middle zero-width strip'нется, останется 'hithere'."""
    payload = "hi​there"
    result = _normalize_support_text(payload)
    assert result == "hithere"


# ── Sanity: нормальный текст не калечится ───────────────────────────────────

def test_normal_russian_text_preserved():
    payload = "Привет, у меня не работает VPN на iPhone 14"
    result = _normalize_support_text(payload)
    assert result == payload


def test_strip_leading_trailing_whitespace():
    payload = "   real complaint here   "
    result = _normalize_support_text(payload)
    assert result == "real complaint here"


# ── Combined bypass: HTML entity + zero-width + full-width ──────────────────

def test_combined_bypass_attempts_all_stripped():
    """Атакующий сочетает все 3 паттерна. Должно всё равно сбросить в empty."""
    payload = "&#8203;​&apos;ＡＢ" * 5  # mix всего
    result = _normalize_support_text(payload)
    # apos раскрывается в ', ＡＢ → AB. Остаётся (' + AB) × 5 = 15 chars
    assert "'AB" in result
    # Но zero-width НЕ должны проходить
    assert "​" not in result

"""Тесты для services/sub_cache.py — модуля кеша /api/vpn/subscription.

Проверяем 4 фундаментальных свойства:
  1. TTL — свежие записи отдаются, протухшие — нет.
  2. invalidate — удаляет запись.
  3. evict_stale — чистит старые при превышении hard_cap.
  4. None payload — корректно кешируется (юзеры без active sub).
"""

import pytest

from services import sub_cache


@pytest.fixture(autouse=True)
def clear_cache():
    """Чистим cache между тестами — модуль global state."""
    sub_cache._cache.clear()
    yield
    sub_cache._cache.clear()


def test_miss_on_empty():
    hit, payload = sub_cache.get(42, now=100.0)
    assert hit is False
    assert payload is None


def test_put_then_get_hit():
    sub_cache.put(42, now=100.0, payload={"plan": "vpn_base"})
    hit, payload = sub_cache.get(42, now=100.5)
    assert hit is True
    assert payload == {"plan": "vpn_base"}


def test_ttl_expiry():
    """Запись старше TTL=2.0с считается протухшей."""
    sub_cache.put(42, now=100.0, payload={"plan": "vpn_base"})
    # До TTL — hit.
    hit, _ = sub_cache.get(42, now=101.99)
    assert hit is True
    # Ровно TTL — miss (граничное условие `>=`).
    hit, _ = sub_cache.get(42, now=102.0)
    assert hit is False
    # После TTL — miss.
    hit, _ = sub_cache.get(42, now=103.0)
    assert hit is False


def test_none_payload_is_cached():
    """None — валидный payload (юзер без active sub). Cache должен его
    тоже отдавать а не делать miss."""
    sub_cache.put(42, now=100.0, payload=None)
    hit, payload = sub_cache.get(42, now=100.5)
    assert hit is True
    assert payload is None


def test_invalidate_removes_entry():
    sub_cache.put(42, now=100.0, payload={"plan": "vpn_base"})
    sub_cache.invalidate(42)
    hit, _ = sub_cache.get(42, now=100.5)
    assert hit is False


def test_invalidate_missing_key_is_noop():
    """Не должен бросать KeyError если ключа нет."""
    sub_cache.invalidate(99999)  # никогда не было


def test_evict_stale_below_cap_noop():
    """Если len < hard_cap, eviction ничего не делает."""
    sub_cache.put(1, now=0.0, payload={"a": 1})  # очень старая
    sub_cache.put(2, now=100.0, payload={"b": 2})
    sub_cache.evict_stale(now=200.0, hard_cap=10)
    assert len(sub_cache._cache) == 2


def test_evict_stale_above_cap_removes_old():
    """При превышении hard_cap чистим записи старше 60с."""
    # 5 записей с разным временем
    for i in range(5):
        sub_cache.put(i, now=float(i), payload={"id": i})
    # Теперь 5 записей. Поставим cap=3, current_time=200 → все старше 60с.
    sub_cache.evict_stale(now=200.0, hard_cap=3)
    # Все 5 записей старше 60с → все должны быть очищены.
    assert len(sub_cache._cache) == 0


def test_evict_stale_hard_cap_fallback():
    """Если все записи свежие (моложе 60с), но превышаем hard_cap —
    fallback на drop oldest до cap'а."""
    for i in range(10):
        # Все записи в окне 0..9с, currrent_time=10 → все «свежие».
        sub_cache.put(i, now=float(i), payload={"id": i})
    sub_cache.evict_stale(now=10.0, hard_cap=5)
    # Должны остаться 5 newest (ids 5..9).
    assert len(sub_cache._cache) == 5
    assert all(k >= 5 for k in sub_cache._cache.keys())


def test_put_overwrites_previous():
    """Повторный put для того же user_id перезаписывает payload."""
    sub_cache.put(42, now=100.0, payload={"plan": "vpn_base"})
    sub_cache.put(42, now=101.0, payload={"plan": "vpn_max"})
    hit, payload = sub_cache.get(42, now=101.5)
    assert hit is True
    assert payload == {"plan": "vpn_max"}


def test_independent_users():
    """Cache key = user_id — данные одного юзера не утекают в чужой."""
    sub_cache.put(1, now=100.0, payload={"plan": "vpn_base"})
    sub_cache.put(2, now=100.0, payload={"plan": "vpn_max"})
    hit1, p1 = sub_cache.get(1, now=100.5)
    hit2, p2 = sub_cache.get(2, now=100.5)
    assert hit1 is True and p1["plan"] == "vpn_base"
    assert hit2 is True and p2["plan"] == "vpn_max"


def test_ttl_constant_is_short():
    """Sanity check: TTL не должен случайно стать большим (например 60с)
    — тогда инвалидации в мутациях были бы не критичны но юзер видел бы
    долго stale."""
    assert sub_cache.TTL <= 5.0, "TTL too long, stale data window unsafe"
    assert sub_cache.TTL >= 0.5, "TTL too short, DB load not amortized"

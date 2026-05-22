"""In-memory TTL cache для /api/vpn/subscription payload.

Вынесен в отдельный модуль чтобы webapp_api.py, handlers/vpn.py, scheduler.py
и grace.py могли инвалидировать одну shared instance без circular imports.

Хранит готовый dict payload (либо None для юзеров без active sub) на 2 секунды.
Bot single-replica → достаточно процесс-локального dict, без Redis/memcached.

Любая мутация состояния подписки/конфигов юзера ОБЯЗАНА вызвать
invalidate(user_id) — иначе фронт после payment-webhook/grant/grace-transition
до 2с видит stale «нет подписки» / старый статус (audit F4-F7+F10).
"""

# Каждая запись: (monotonic_ts, payload_dict | None).
_cache: dict[int, tuple[float, dict | None]] = {}
TTL = 2.0  # seconds — short enough that even forgotten invalidate-call залечится


def get(user_id: int, now: float) -> tuple[bool, dict | None]:
    """(hit, payload) — hit=True если запись свежая (now-ts < TTL)."""
    entry = _cache.get(user_id)
    if entry is None:
        return False, None
    ts, payload = entry
    if now - ts >= TTL:
        return False, None
    return True, payload


def put(user_id: int, now: float, payload: dict | None) -> None:
    _cache[user_id] = (now, payload)


def invalidate(user_id: int) -> None:
    """Сбросить кеш для одного юзера. Безопасно звать когда записи нет."""
    _cache.pop(user_id, None)


def evict_stale(now: float, hard_cap: int = 5000) -> None:
    """Lazy eviction. Звать после put() когда len() > hard_cap.

    Сначала чистим всё старше 60с. Если этого мало (peak hour, 5000 свежих
    ключей) — выкидываем oldest по timestamp пока не уложимся в hard_cap.
    """
    if len(_cache) <= hard_cap:
        return
    cutoff = now - 60.0
    stale = [k for k, (ts, _) in _cache.items() if ts < cutoff]
    for k in stale:
        del _cache[k]
    if len(_cache) > hard_cap:
        # Hard cap fallback: drop oldest до hard_cap (audit F9).
        sorted_keys = sorted(_cache.items(), key=lambda kv: kv[1][0])
        drop_n = len(_cache) - hard_cap
        for k, _ in sorted_keys[:drop_n]:
            del _cache[k]

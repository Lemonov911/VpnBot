"""
RU split-tunneling rule-set: качаем sing-box.zip от runetfreedom раз в 6ч,
извлекаем `geoip-ru.srs` + `geosite-ru-available-only-inside.srs` (~105 KB
вместе), раздаём через `/static/xray-rules/{name}.srs`. Sing-box внутри
Happ их подтягивает по rule_set remote URLs и применяет на клиенте.

Зачем:  без bypass'а юзеры из RU не могут открыть Сбер/Кинопоиск/Госуслуги
через VPN — те геоблочат не-RU IP.  Эти 2 файла = «куда НЕ тоннелить».
"""
import asyncio
import hashlib
import io
import logging
import os
import time
import zipfile
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)

# Где живут .srs на диске. Раздаётся через /static/xray-rules/ aiohttp-роутом.
# /opt/vpnbot/data/xray-rules/ на проде — `data/` ничем не занят, в .gitignore.
RULES_DIR = Path(os.environ.get(
    "XRAY_RULES_DIR",
    str(Path(__file__).resolve().parent.parent / "data" / "xray-rules"),
))

# Какие файлы извлекаем из zip'а. Имена приходят как в archive — не меняем
# при сохранении, чтобы клиентский URL был стабилен.
_NEEDED_FILES = {
    "rule-set-geoip/geoip-ru.srs": "geoip-ru.srs",
    "rule-set-geosite/geosite-ru-available-only-inside.srs":
        "geosite-ru-available-only-inside.srs",
}

# GitHub Releases — `latest` редирект всегда указывает на свежее окно
# (~6ч cadence у runetfreedom).
_ZIP_URL = (
    "https://github.com/runetfreedom/russia-v2ray-rules-dat/"
    "releases/latest/download/sing-box.zip"
)

# Если по сети не вышло — оставляем что есть. Файл считаем «свежим» если
# моложе 24ч, иначе логируем warning (не критично — Happ работает по cached).
STALE_AGE_SEC = 24 * 3600

_DOWNLOAD_TIMEOUT = 60  # обычно 1-2 МБ, 60с с большим запасом


async def fetch_rules(force: bool = False) -> dict:
    """Качает sing-box.zip и распаковывает 2 нужных файла. Atomic write через
    .tmp + os.replace. Возвращает dict со статистикой для логов / audit.

    `force=False` — пропускает скачивание если файлы свежее STALE_AGE_SEC,
    чтобы при рестарте бота не дёргать GitHub лишний раз.
    """
    RULES_DIR.mkdir(parents=True, exist_ok=True)
    stats = {
        "skipped": False,
        "downloaded_bytes": 0,
        "extracted": [],
        "error": None,
        "took_ms": 0,
    }
    t0 = time.monotonic()

    # Skip если все нужные файлы свежие (есть и младше STALE_AGE_SEC).
    if not force:
        all_fresh = all(
            (RULES_DIR / dst).exists()
            and (time.time() - (RULES_DIR / dst).stat().st_mtime) < STALE_AGE_SEC
            for dst in _NEEDED_FILES.values()
        )
        if all_fresh:
            stats["skipped"] = True
            stats["took_ms"] = int((time.monotonic() - t0) * 1000)
            return stats

    try:
        timeout = aiohttp.ClientTimeout(total=_DOWNLOAD_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(_ZIP_URL) as resp:
                resp.raise_for_status()
                data = await resp.read()
        stats["downloaded_bytes"] = len(data)

        # zipfile синхронен — выполняем в executor чтобы не блочить event loop
        # (распаковка большого geoip-ru.srs ~100ms).
        def _extract() -> list[str]:
            out: list[str] = []
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = set(zf.namelist())
                for src, dst in _NEEDED_FILES.items():
                    if src not in names:
                        logger.warning("xray-rules: %s отсутствует в zip", src)
                        continue
                    content = zf.read(src)
                    tmp = RULES_DIR / f".{dst}.tmp"
                    final = RULES_DIR / dst
                    tmp.write_bytes(content)
                    os.replace(tmp, final)
                    out.append(dst)
            return out

        stats["extracted"] = await asyncio.get_event_loop().run_in_executor(
            None, _extract,
        )
    except Exception as e:
        stats["error"] = str(e)[:200]
        logger.warning("xray-rules fetch failed: %s", e, exc_info=True)

    stats["took_ms"] = int((time.monotonic() - t0) * 1000)
    return stats


def rule_file_path(name: str) -> Path | None:
    """Возвращает путь к .srs файлу если он есть, иначе None. Используется
    HTTP-хендлером `/static/xray-rules/{name}` для безопасной выдачи."""
    if name not in _NEEDED_FILES.values():
        return None
    p = RULES_DIR / name
    return p if p.exists() else None


def rule_file_age_sec(name: str) -> float | None:
    """Возраст файла в секундах, или None если файла нет. Для health-чека."""
    p = RULES_DIR / name
    if not p.exists():
        return None
    return time.time() - p.stat().st_mtime


def rule_file_sha256(name: str) -> str | None:
    """SHA256 файла (hex). Используется в Cache-Control / ETag."""
    p = RULES_DIR / name
    if not p.exists():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# Имена файлов как они доступны через HTTP (для генерации rule_set URL'ов).
GEOIP_RU_FILE = "geoip-ru.srs"
GEOSITE_RU_INSIDE_FILE = "geosite-ru-available-only-inside.srs"

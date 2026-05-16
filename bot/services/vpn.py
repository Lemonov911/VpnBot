"""
Управление VPN-пользователями через SSH (AmneziaWG).

Скрипты на сервере (/opt/amnezia/):
  add_user.sh <username>    → выводит .conf в stdout
  remove_user.sh <username> → удаляет пир
"""

import logging
import os

logger = logging.getLogger(__name__)

ADD_SCRIPT    = "/opt/amnezia/add_user.sh"
REMOVE_SCRIPT = "/opt/amnezia/remove_user.sh"


def _ssh_kwargs_from_env() -> dict:
    """Параметры SSH из переменных окружения (env-based сервер)."""
    from config import VPN_SERVER_HOST, VPN_SERVER_USER, VPN_SERVER_KEY, VPN_SERVER_PASS
    kwargs: dict = dict(
        host=VPN_SERVER_HOST,
        username=VPN_SERVER_USER,
        known_hosts=None,
    )
    # Пароль приоритетнее ключа — отключаем key auth чтобы не спросил passphrase
    if VPN_SERVER_PASS:
        kwargs["password"] = VPN_SERVER_PASS
        kwargs["client_keys"] = []
    else:
        key_path = os.path.expanduser(VPN_SERVER_KEY)
        if os.path.exists(key_path):
            kwargs["client_keys"] = [key_path]
    return kwargs


def _ssh_kwargs_from_server(host: str, user: str = "root",
                             password: str | None = None,
                             key_path: str | None = None) -> dict:
    """Параметры SSH из явно переданных данных сервера (для таблицы servers)."""
    kwargs: dict = dict(host=host, username=user, known_hosts=None)
    if password:
        kwargs["password"] = password
        kwargs["client_keys"] = []
    elif key_path:
        expanded = os.path.expanduser(key_path)
        if os.path.exists(expanded):
            kwargs["client_keys"] = [expanded]
    return kwargs


def _is_configured() -> bool:
    from config import VPN_SERVER_HOST
    return bool(VPN_SERVER_HOST)


# ── Старый API (backward compat) ───────────────────────────────────────────────

async def create_vpn_user(username: str) -> bytes | None:
    """
    SSH на VPN-сервер, запускает add_user.sh, возвращает .conf как bytes.
    Возвращает None если сервер не настроен (dev-режим).
    Бросает RuntimeError при ошибке SSH/скрипта.
    """
    if not _is_configured():
        logger.warning("VPN_SERVER_HOST не задан — возвращаем mock-конфиг")
        return None

    try:
        import asyncssh
    except ImportError:
        logger.error("asyncssh не установлен")
        return None

    try:
        async with asyncssh.connect(**_ssh_kwargs_from_env()) as conn:
            result = await conn.run(f"bash {ADD_SCRIPT} {username}", check=True)
            config = result.stdout.strip()
            logger.info("VPN user создан: %s (%d байт)", username, len(config))
            return config.encode()
    except Exception as e:
        logger.error("SSH add_user ошибка для %s: %s", username, e, exc_info=True)
        raise RuntimeError(str(e)) from e


async def remove_vpn_user(username: str) -> bool:
    """Удаляет VPN-пользователя с сервера. Возвращает True при успехе."""
    if not _is_configured():
        logger.warning("VPN_SERVER_HOST не задан — пропускаем удаление %s", username)
        return False

    try:
        import asyncssh
    except ImportError:
        return False

    try:
        async with asyncssh.connect(**_ssh_kwargs_from_env()) as conn:
            await conn.run(f"bash {REMOVE_SCRIPT} {username}", check=True)
            logger.info("VPN user удалён: %s", username)
            return True
    except Exception as e:
        logger.error("SSH remove_user ошибка для %s: %s", username, e, exc_info=True)
        return False


# ── Новый API (работает с таблицей configs) ────────────────────────────────────

async def create_config(
    config_id: int,
    user_id: int,
    server_ip: str,
    server_user: str = "root",
    server_password: str | None = None,
    server_key_path: str | None = None,
) -> bytes | None:
    """
    Создаёт AWG-конфиг на конкретном сервере.

    Peer называется tg{user_id}_{config_id} — уникально в рамках одного сервера.
    Возвращает содержимое .conf как bytes или None при ошибке.

    Args:
        config_id:       id записи в таблице configs
        user_id:         Telegram user_id
        server_ip:       IP сервера
        server_user:     SSH-пользователь (default: root)
        server_password: SSH-пароль (приоритетнее ключа)
        server_key_path: путь к SSH-ключу (fallback)
    """
    peer_name = f"tg{user_id}_{config_id}"

    try:
        import asyncssh
    except ImportError:
        logger.error("asyncssh не установлен")
        return None

    kwargs = _ssh_kwargs_from_server(server_ip, server_user, server_password, server_key_path)

    try:
        async with asyncssh.connect(**kwargs) as conn:
            result = await conn.run(f"bash {ADD_SCRIPT} {peer_name}", check=True)
            config = result.stdout.strip()
            logger.info("Конфиг создан: %s на %s (%d байт)", peer_name, server_ip, len(config))
            return config.encode()
    except Exception as e:
        logger.error("SSH create_config ошибка: peer=%s server=%s: %s", peer_name, server_ip, e, exc_info=True)
        return None


async def remove_config(
    config_id: int,
    user_id: int,
    server_ip: str,
    server_user: str = "root",
    server_password: str | None = None,
    server_key_path: str | None = None,
) -> bool:
    """
    Удаляет AWG-пир с конкретного сервера.

    Args аналогичны create_config. Возвращает True при успехе.
    """
    peer_name = f"tg{user_id}_{config_id}"

    try:
        import asyncssh
    except ImportError:
        return False

    kwargs = _ssh_kwargs_from_server(server_ip, server_user, server_password, server_key_path)

    try:
        async with asyncssh.connect(**kwargs) as conn:
            await conn.run(f"bash {REMOVE_SCRIPT} {peer_name}", check=True)
            logger.info("Пир удалён: %s с %s", peer_name, server_ip)
            return True
    except Exception as e:
        logger.error("SSH remove_config ошибка: peer=%s server=%s: %s", peer_name, server_ip, e, exc_info=True)
        return False

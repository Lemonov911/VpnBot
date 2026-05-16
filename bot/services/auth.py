"""
Верификация Telegram Mini App initData через HMAC-SHA256.

Алгоритм (по документации Telegram):
  1. Извлечь hash из строки initData
  2. Собрать оставшиеся пары key=value, отсортировать по ключу
  3. Объединить через \n
  4. secret_key = HMAC-SHA256("WebAppData", bot_token)
  5. computed   = HMAC-SHA256(secret_key, data_check_string)
  6. Сравнить computed с hash через compare_digest (защита от timing-атак)
"""

import hashlib
import hmac
import json
import logging
import time
from urllib.parse import parse_qsl, unquote

logger = logging.getLogger(__name__)

# Telegram recommends rejecting initData older than this. Without the check,
# a captured initData (devtools leak, shared screen, log) works forever and
# lets an attacker impersonate the user against every authenticated endpoint.
INIT_DATA_MAX_AGE_SEC = 24 * 60 * 60


def verify_init_data(init_data: str, bot_token: str) -> dict | None:
    """
    Проверяет подпись Telegram initData.

    Возвращает dict с данными пользователя (поле user) если подпись валидна,
    иначе None.

    Args:
        init_data: строка initData из window.Telegram.WebApp.initData
        bot_token: токен бота из @BotFather

    Returns:
        dict с полями пользователя (id, first_name, ...) или None
    """
    if not init_data or not bot_token:
        logger.warning("verify_init_data: пустой init_data или bot_token")
        return None

    try:
        # Разбираем строку в словарь
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))

        # Извлекаем hash — он не участвует в проверочной строке
        received_hash = pairs.pop("hash", None)
        if not received_hash:
            logger.warning("verify_init_data: hash отсутствует в initData")
            return None

        # Строим data_check_string: key=value\nkey=value (отсортировано по ключу)
        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(pairs.items())
        )

        # Вычисляем секретный ключ: HMAC-SHA256("WebAppData", bot_token)
        secret_key = hmac.new(
            b"WebAppData",
            bot_token.encode("utf-8"),
            hashlib.sha256,
        ).digest()

        # Вычисляем ожидаемый hash
        computed_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Сравниваем через compare_digest (защита от timing-атак)
        if not hmac.compare_digest(computed_hash, received_hash):
            logger.warning("verify_init_data: подпись невалидна")
            return None

        # Replay-window: HMAC валиден, но initData может быть украденный
        # и сколь угодно старый. Telegram рекомендует ≤24ч.
        auth_date_raw = pairs.get("auth_date")
        if not auth_date_raw:
            logger.warning("verify_init_data: auth_date отсутствует")
            return None
        try:
            auth_date = int(auth_date_raw)
        except ValueError:
            logger.warning("verify_init_data: auth_date не int: %r", auth_date_raw)
            return None
        age = int(time.time()) - auth_date
        if age > INIT_DATA_MAX_AGE_SEC or age < -300:
            logger.warning("verify_init_data: auth_date просрочен (age=%ds)", age)
            return None

        # Извлекаем данные пользователя из поля user (JSON, URL-encoded)
        user_raw = pairs.get("user", "{}")
        user = json.loads(unquote(user_raw))

        if not isinstance(user, dict) or "id" not in user:
            logger.warning("verify_init_data: поле user пустое или без id")
            return None

        return user

    except Exception as exc:
        logger.warning("verify_init_data: ошибка разбора: %s", exc, exc_info=True)
        return None

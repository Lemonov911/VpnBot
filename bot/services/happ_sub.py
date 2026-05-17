"""
Happ-compatible subscription JSON: **xray-core** формат (НЕ sing-box).

Прошлая попытка через sing-box (`services/singbox_sub.py`) — отвергнута
Happ'ом «Неверный формат JSON конфигурации» (2026-05-17). Happ внутри
использует xray-core, ему нужен xray-формат с `routing.rules` +
inline `geosite:` / `geoip:` ссылками на bundled geo-files (Happ их сам
носит, наши `.srs` от runetfreedom не нужны).

Формат подписки = JSON-array, каждый элемент = отдельный конфиг с одним
VLESS outbound + routing. Happ показывает каждый элемент как отдельный
сервер в списке.
"""
import json
import logging
from urllib.parse import parse_qs, unquote, urlsplit

logger = logging.getLogger(__name__)

# Inline domain list для xray-core `routing.rules`.  Юзер 17.05 подтвердил
# что `geosite:category-ru` молча не сработал в Happ (Yandex через VLESS
# тоже геоблокировался) — скорее всего Happ не bundled geosite.dat файл,
# xray-core игнорит правило без ошибки.  Используем explicit `domain:`
# match'и — это работает БЕЗ внешних .dat файлов.
#
# Покрывает Yandex (все домены), VK, Mail.ru, банки, госуслуги, медиа.
_RU_BYPASS_DOMAINS = [
    # Yandex universe — yandex.ru, ya.ru, yandex.com, *.yandex.net, и т.д.
    "domain:yandex.ru", "domain:yandex.net", "domain:yandex.com",
    "domain:ya.ru", "domain:yandex.com.tr", "domain:yandex.com.am",
    "domain:yandex.com.ge", "domain:yandex.kz", "domain:yandex.by",
    "domain:yandex.fr", "domain:yandex.eu",
    "domain:kinopoisk.ru", "domain:kp.ru", "domain:dzen.ru",
    # VK / Mail.ru group
    "domain:vk.com", "domain:vk.ru", "domain:mail.ru", "domain:ok.ru",
    "domain:my.mail.ru", "domain:list.ru", "domain:bk.ru", "domain:inbox.ru",
    "domain:vkadre.ru", "domain:vkontakte.ru", "domain:vkuse.ru",
    # Bookmate
    "domain:bookmate.com", "domain:bookmate.ru",
    # Банки
    "domain:sberbank.ru", "domain:sber.ru", "domain:online.sberbank.ru",
    "domain:tinkoff.ru", "domain:tbank.ru", "domain:t-bank.ru",
    "domain:alfabank.ru", "domain:vtb.ru", "domain:raiffeisen.ru",
    "domain:gazprombank.ru", "domain:rshb.ru", "domain:rsb.ru",
    "domain:psbank.ru", "domain:mkb.ru", "domain:rosbank.ru",
    "domain:open.ru", "domain:sovcombank.ru", "domain:gazprom.ru",
    # СБП / НСПК / Mir card
    "domain:nspk.ru", "domain:sbp.nspk.ru", "domain:mironline.ru",
    # Госуслуги, ФНС
    "domain:gosuslugi.ru", "domain:nalog.gov.ru", "domain:nalog.ru",
    "domain:mos.ru", "domain:rosreestr.gov.ru", "domain:gibdd.ru",
    "domain:roskazna.ru", "domain:pfr.gov.ru", "domain:fns.ru",
    # Маркетплейсы
    "domain:wildberries.ru", "domain:wb.ru", "domain:ozon.ru",
    "domain:avito.ru", "domain:dns-shop.ru", "domain:mvideo.ru",
    "domain:eldorado.ru", "domain:lamoda.ru", "domain:citilink.ru",
    # Доставка / такси
    "domain:samokat.ru", "domain:vkusvill.ru", "domain:perekrestok.ru",
    "domain:5ka.ru", "domain:lavka.yandex.ru", "domain:eda.yandex.ru",
    # Стриминги
    "domain:okko.tv", "domain:ivi.ru", "domain:premier.one",
    "domain:start.ru", "domain:wink.ru", "domain:more.tv",
    # Связь / провайдеры
    "domain:mts.ru", "domain:beeline.ru", "domain:megafon.ru",
    "domain:tele2.ru", "domain:rt.ru", "domain:rostelecom.ru",
    # .ru tld catch-all — последний rule, всё .ru через direct.
    # Это широко но в худшем случае ломает редкие случаи когда юзер
    # хочет .ru-домен через VPN (например russian-political сайты).
    "regexp:.+\\.ru$",
]


def _parse_vless_url(url: str) -> dict | None:
    """Парсит `vless://uuid@host:port?params#fragment` → 4-tuple
    (server_address, port, uuid, params, tag) для последующей сборки
    xray-outbound'а. Возвращает None если URL не VLESS / битый."""
    if not url.startswith("vless://"):
        return None
    try:
        parts = urlsplit(url)
        uuid_ = parts.username or ""
        host = parts.hostname or ""
        port = parts.port or 443
        if not uuid_ or not host:
            return None
        q = parse_qs(parts.query)
        params = {k: v[0] if v else "" for k, v in q.items()}
        tag = unquote(parts.fragment) if parts.fragment else f"{host}:{port}"
        return {
            "server": host, "port": port, "uuid": uuid_,
            "params": params, "tag": tag,
        }
    except Exception as e:
        logger.warning("vless parse failed for %r: %s", url[:80], e)
        return None


def _build_stream_settings(params: dict) -> dict:
    """xray-core `streamSettings` блок из vless:// query params."""
    network = params.get("type", "tcp")
    security = params.get("security", "none")

    ss: dict = {"network": network, "security": security}

    if security == "reality":
        reality_settings: dict = {}
        if params.get("sni"):
            reality_settings["serverName"] = params["sni"]
        if params.get("fp"):
            reality_settings["fingerprint"] = params["fp"]
        if params.get("pbk"):
            reality_settings["publicKey"] = params["pbk"]
        if params.get("sid"):
            reality_settings["shortId"] = params["sid"]
        if params.get("spx"):
            reality_settings["spiderX"] = params["spx"]
        ss["realitySettings"] = reality_settings
    elif security == "tls":
        tls_settings: dict = {}
        if params.get("sni"):
            tls_settings["serverName"] = params["sni"]
        if params.get("fp"):
            tls_settings["fingerprint"] = params["fp"]
        if params.get("alpn"):
            tls_settings["alpn"] = [
                a.strip() for a in params["alpn"].split(",") if a.strip()
            ]
        ss["tlsSettings"] = tls_settings

    if network == "ws":
        ws: dict = {}
        if params.get("path"):
            ws["path"] = params["path"]
        if params.get("host"):
            ws["headers"] = {"Host": params["host"]}
        ss["wsSettings"] = ws
    elif network == "grpc":
        grpc: dict = {}
        if params.get("serviceName"):
            grpc["serviceName"] = params["serviceName"]
        ss["grpcSettings"] = grpc

    return ss


def _build_vless_outbound(parsed: dict) -> dict:
    """xray-core VLESS outbound из распарсенного URL'а."""
    user: dict = {
        "id": parsed["uuid"],
        "encryption": "none",  # xray-core required, sing-box defaults — у нас явно
        "level": 0,
    }
    if parsed["params"].get("flow"):
        user["flow"] = parsed["params"]["flow"]

    return {
        "tag": "proxy",
        "protocol": "vless",
        "settings": {
            "vnext": [{
                "address": parsed["server"],
                "port": parsed["port"],
                "users": [user],
            }],
        },
        "streamSettings": _build_stream_settings(parsed["params"]),
    }


def _build_single_config(parsed: dict, profile_title: str) -> dict:
    """Один xray-core config для одного сервера: VLESS outbound + direct/block
    + RU bypass routing.  Возвращаем dict (caller сериализует)."""
    proxy_ob = _build_vless_outbound(parsed)
    direct_ob = {"tag": "direct", "protocol": "freedom", "settings": {}}
    block_ob  = {"tag": "block",  "protocol": "blackhole", "settings": {}}

    # IP-rules: inline RU CIDR блоки (Yandex AS, VK, Mail.ru, банки, МТС).
    # Импортируем из awg_bypass чтобы один источник правды для обеих сторон.
    from services.awg_bypass import _EXTRA_RU_CIDRS
    ru_ips = list(_EXTRA_RU_CIDRS) + [
        # Private + reserved — для loopback и LAN.
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
    ]

    return {
        "remarks": parsed["tag"],
        "log": {"loglevel": "warning"},
        "outbounds": [proxy_ob, direct_ob, block_ob],
        "routing": {
            # IPIfNonMatch: сначала по домену (быстро), если не сматчилось —
            # резолвим в IP и сверяемся с inline RU-блоками.
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                # RU-домены → direct.  Explicit list, БЕЗ geosite:
                # ссылок (Happ не bundled geosite.dat, geosite: refs молча
                # игнорятся xray-core'ом).
                {
                    "type": "field",
                    "domain": _RU_BYPASS_DOMAINS,
                    "outboundTag": "direct",
                },
                # RU IPs + private (LAN, loopback) → direct.
                # inline CIDR'ы вместо geoip:ru (та же причина).
                {
                    "type": "field",
                    "ip": ru_ips,
                    "outboundTag": "direct",
                },
                # Всё остальное — в VLESS-туннель.
                {
                    "type": "field",
                    "outboundTag": "proxy",
                    "network": "tcp,udp",
                },
            ],
        },
    }


def build_happ_subscription(
    vless_urls: list[str], *, profile_title: str = "MAX VPN",
) -> list[dict]:
    """Возвращает JSON-массив xray-core конфигов — по одному на каждый
    валидный VLESS URL.  Happ показывает каждый как отдельный сервер в UI.

    Если все URL'ы битые → пустой массив (caller должен отдать `[]` или
    fallback на plain base64).
    """
    configs: list[dict] = []
    for url in vless_urls:
        parsed = _parse_vless_url(url)
        if parsed:
            configs.append(_build_single_config(parsed, profile_title))
    return configs


def serialize(configs: list[dict]) -> str:
    """JSON-array → string.  ensure_ascii=False — эмодзи в `remarks`
    рендерятся как есть, а не \\uXXXX."""
    return json.dumps(configs, ensure_ascii=False, indent=2)

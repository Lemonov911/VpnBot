# VLESS через Cloudflare: обход DPI/ТСПУ

Эксперименты с проксированием VLESS через Cloudflare CDN на FR-сервере `207.154.214.108`.

## Проблема

Reality на `207.154.214.108` перестала работать из России — ТСПУ режет TLS ClientHello напрямую на все порты (4443, 43100–43109). Логи Xray: `failed to read client hello from 92.39.221.55`. IP сервера попал в блок.

## Решение: VLESS+WS+TLS и VLESS+gRPC+TLS через Cloudflare

Cloudflare выступает как reverse proxy — клиент подключается к CF IP (`104.21.x.x`, `172.67.x.x`), ТСПУ их не блокирует. CF проксирует трафик на наш origin.

Домен: `vpn.maxvpnesim.shop` → DNS A-запись на `207.154.214.108`, `proxied: true` (оранжевое облако).

### Итоговые inbound'ы на сервере

| Протокол | Порт | SNI | CF-совместимый порт |
|---|---|---|---|
| VLESS+WS+TLS | 443 | vpn.maxvpnesim.shop | ✅ 443 |
| VLESS+gRPC+TLS | 2053 | vpn.maxvpnesim.shop | ✅ 2053 (CF HTTPS порт) |

Конфиг: `/usr/local/etc/xray/config.json` на `207.154.214.108`.

### VLESS+WS+TLS (inbound `vless-ws-tls-cf`, порт 443)

```json
{
  "tag": "vless-ws-tls-cf",
  "listen": "0.0.0.0",
  "port": 443,
  "protocol": "vless",
  "settings": {
    "clients": [{"id": "<UUID>", "level": 0, "email": "user@vpn", "flow": ""}],
    "decryption": "none"
  },
  "streamSettings": {
    "network": "ws",
    "security": "tls",
    "tlsSettings": {
      "certificates": [{"certificateFile": "/etc/xray/certs/cert.pem", "keyFile": "/etc/xray/certs/key.pem"}],
      "alpn": ["http/1.1"]
    },
    "wsSettings": {
      "path": "/vless-ws",
      "host": "vpn.maxvpnesim.shop",
      "heartbeatPeriod": 30
    }
  }
}
```

**Важно**: `alpn: ["http/1.1"]` — только http/1.1, без h2. WS upgrade не работает через CF с h2.

### VLESS+gRPC+TLS (inbound `vless-grpc-cf`, порт 2053)

```json
{
  "tag": "vless-grpc-cf",
  "listen": "0.0.0.0",
  "port": 2053,
  "protocol": "vless",
  "streamSettings": {
    "network": "grpc",
    "security": "tls",
    "tlsSettings": {
      "alpn": ["h2"]
    },
    "grpcSettings": {
      "serviceName": "vpnservice",
      "multiMode": true,
      "idle_timeout": 60,
      "health_check_timeout": 20,
      "permit_without_stream": true
    }
  }
}
```

**Важно**: `alpn: ["h2"]` — gRPC требует HTTP/2.

### URL для Happ (подписка)

```
vless://<UUID>@vpn.maxvpnesim.shop:443?type=ws&security=tls&sni=vpn.maxvpnesim.shop&host=vpn.maxvpnesim.shop&path=%2Fvless-ws#Frankfurt-CF-WS-443

vless://<UUID>@vpn.maxvpnesim.shop:2053?type=grpc&security=tls&sni=vpn.maxvpnesim.shop&serviceName=vpnservice#Frankfurt-CF-gRPC-2053
```

---

## Cloudflare: настройка

### Bypass-правило WAF (обязательно!)

Без этого CF возвращает 403 (Managed Challenge) на все запросы от VPN-клиентов.

```bash
CF_TOKEN="<zone_settings+firewall_token>"
ZONE_ID="d2e997fc6301009962e295fd57483277"   # maxvpnesim.shop

# Создать bypass rule
curl -X POST "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/firewall/rules" \
  -H "Authorization: Bearer $CF_TOKEN" \
  -H "Content-Type: application/json" \
  -d '[{
    "action": "bypass",
    "products": ["bic","hot","rateLimit","securityLevel","uaBlock","waf","zoneLockdown"],
    "filter": {
      "expression": "(http.request.uri.path contains \"/vless-ws\") or (http.request.uri.path contains \"/vpnservice\")",
      "description": "VPN paths filter"
    },
    "description": "VPN bypass"
  }]'

# Установить Security Level = essentially_off
curl -X PATCH "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/settings/security_level" \
  -H "Authorization: Bearer $CF_TOKEN" \
  -d '{"value":"essentially_off"}'
```

Действующее правило (zone `maxvpnesim.shop`):
- Filter ID: `fec63f6f68ef451d952e9d63b45b5a81`
- Rule ID: `ce46145ba6594a17bb2ade7edad64f2c`

### CF SSL mode

Режим **Full** (не Full Strict) — CF подключается к origin по TLS, не проверяя cert. На origin лежит self-signed cert (`/etc/xray/certs/cert.pem`, valid to 2036).

---

## Проблемы и их решения

### 1. CF возвращал 403 "Under Attack"

CF Dashboard → Security Level был `I'm Under Attack`. Все VPN-клиенты (non-browser) получали 403.

**Решение**: Отключить "I'm Under Attack" в CF Dashboard, создать bypass rule через API, Security Level → `essentially_off`.

### 2. WS не работал через CF (ALPN h2)

WS inbound имел `alpn: ["h2", "http/1.1"]`. CF пытался поднять h2 — стандартный WebSocket Upgrade через h2 не работает.

**Решение**: `alpn: ["http/1.1"]` только для WS inbound.

### 3. Рилсы/видео умирало через ~10 секунд стриминга

Xray policy level 0 имел `downlinkOnly: 10` — Xray закрывал соединение через 10 секунд чистого скачивания без upload. Видео = чистый download.

**Решение**: `downlinkOnly: 120`, `uplinkOnly: 30`.

### 4. VPN отключался через ~75–100 секунд простоя

Cloudflare закрывает idle WebSocket без данных примерно за 75–100 секунд.

**Решение**: `heartbeatPeriod: 30` в `wsSettings` — сервер шлёт WS PING CF каждые 30 секунд. CF сбрасывает таймер.

### 5. gRPC inbound запускался без ALPN

gRPC требует HTTP/2, но ALPN не был указан → h2 не согласовывался.

**Решение**: добавить `tlsSettings.alpn: ["h2"]`.

---

## Итоговые настройки политики Xray

```json
"policy": {
  "levels": {
    "0": {
      "handshake": 8,
      "connIdle": 600,
      "uplinkOnly": 30,
      "downlinkOnly": 120
    }
  }
}
```

---

## Что работает сейчас (07.05.2026)

| Протокол | Статус |
|---|---|
| VLESS+WS+TLS через CF (:443) | ✅ Работает, подтверждено в access.log из 92.39.222.59 (RU) |
| VLESS+gRPC+TLS через CF (:2053) | ✅ Сконфигурирован, multiMode включён |
| VLESS+Reality (:43100) | ⛔ Блокируется ТСПУ — DPI режет ClientHello к 207.154.214.108 |

Access log подтверждал активный трафик (Facebook, TikTok, Apple, Cloudflare) через `vless-ws-tls-cf`. Reality оставлена в подписке как третий fallback (работает вне РФ).

---

## CF-совместимые HTTPS порты

CF принимает только определённые порты для proxied HTTPS:

```
443, 2053, 2083, 2087, 2096, 8443
```

Для gRPC рекомендуется 2053 (HTTP/2 с меньшей вероятностью переключения на HTTP/1.1).

# Журнал работы над FR-сервером 06–07.05.2026

Подробный лог всех действий и экспериментов в хронологическом порядке.
Чтобы не забыть кто что менял — фиксируем всё.

---

## Начальное состояние

**Сервер**: `207.154.214.108` (DigitalOcean Frankfurt), на нём Xray.

**Что было**:
- VLESS+Reality на порту 4443 (`vless-reality-4443`, dest=www.microsoft.com)
- VLESS+Reality на портах 43100–43109 (`vless-reality-max`, dest=www.yahoo.com, fp=ios) — активный пользователь `tg594024866_11@vpn` с UUID `e96153a1-bc1f-40b4-a5c3-33be76e3cf4d`
- Пустые Reality inbound'ы на 43000–43009, 43200–43209, 43300–43309
- Routing rule: блок UDP на порту 443 (мешал QUIC у Facebook/Instagram)
- Самоподписанный TLS cert: `/etc/xray/certs/cert.pem`, `/etc/xray/certs/key.pem` (валиден до 2036)

**Проблема**: с MTS (IP `92.39.222.59`) Reality перестала работать. В логах:
```
transport/internet/tcp: REALITY: processed invalid connection from 92.39.221.55: failed to read client hello
```
DPI/ТСПУ режет TLS ClientHello ещё до того как он доходит до Xray. IP `207.154.214.108` попал в блоклист по TCP-фингерпринту Reality.

**Конкурент StealthSurf** работает на том же Happ — значит они используют не Reality, а что-то через CF. Это и стало целью.

---

## Шаг 1. Подняли VLESS+WS+TLS через Cloudflare

Идея: Cloudflare как reverse proxy. Клиент подключается к CF IP (`104.21.x.x`, `172.67.x.x`), которые ТСПУ не блокирует. CF проксирует на наш origin.

**Что сделано**:
1. На домене `maxvpnesim.shop` уже была DNS-запись на CF (zone ID `d2e997fc6301009962e295fd57483277`)
2. Создали A-запись `vpn.maxvpnesim.shop → 207.154.214.108`, **оранжевое облако** (`proxied: true`)
3. Добавили в Xray `/usr/local/etc/xray/config.json` inbound:

```json
{
  "tag": "vless-ws-tls-cf",
  "listen": "0.0.0.0",
  "port": 443,
  "protocol": "vless",
  "settings": {
    "clients": [{"id": "e96153a1-bc1f-40b4-a5c3-33be76e3cf4d", "email": "tg594024866_11@vpn", "flow": ""}],
    "decryption": "none"
  },
  "streamSettings": {
    "network": "ws",
    "security": "tls",
    "tlsSettings": {
      "certificates": [{"certificateFile": "/etc/xray/certs/cert.pem", "keyFile": "/etc/xray/certs/key.pem"}],
      "alpn": ["h2", "http/1.1"]
    },
    "wsSettings": {"path": "/vless-ws", "host": "vpn.maxvpnesim.shop"}
  }
}
```

4. Скриптом через Python переписали config.json (heredoc с UUID не работал — single-quote shell блокирует подстановку, использовали Python для безопасной правки JSON)

5. Перезапустили: `systemctl restart xray`

**Тест**: WS не работал. CF возвращал 403 ("Cloudflare under attack mode").

---

## Шаг 2. Боролись с CF "Under Attack" + Managed Challenge

CF Dashboard у `maxvpnesim.shop`: режим **"I'm Under Attack"**. Все non-browser клиенты получали 403 с challenge cookie.

**Что сделано**:
1. Пользователь руками отключил "I'm Under Attack" в CF Dashboard
2. CF всё равно отдавал 403 с `cf-mitigated: challenge` — Security Level был на high
3. Bot Fight Mode уже был выключен — это не оно
4. Решили автоматизировать через CF API

**CF token #1** (хранится локально, в репо не пушим):
- Только DNS Edit права
- Получали 9109 Unauthorized для Security Level
- Получали 10000 Authentication error для Rulesets
- Не подошёл

**CF token #2** (хранится локально, в репо не пушим):
- Zone Settings Edit + Firewall Services Edit
- Этим всё дальше делалось

**Через API**:
1. Создали Filter:
   - ID: `fec63f6f68ef451d952e9d63b45b5a81`
   - Expression: `(http.request.uri.path contains "/vless-ws") or (http.request.uri.path contains "/vpnservice")`

2. Создали Firewall Rule:
   - ID: `ce46145ba6594a17bb2ade7edad64f2c`
   - Action: `bypass`
   - Products: `bic, hot, rateLimit, securityLevel, uaBlock, waf, zoneLockdown`

3. PATCH Security Level → `essentially_off`:
```bash
curl -X PATCH "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/settings/security_level" \
  -H "Authorization: Bearer $CF_TOKEN" \
  -d '{"value":"essentially_off"}'
```

После этого WS endpoint начал отвечать 200 OK на upgrade-запросы (тест из Frankfurt).

---

## Шаг 3. Починили ALPN для WS

Несмотря на bypass rule, реальный VPN всё равно не подключался.

**Найдено**: WS inbound имел `alpn: ["h2", "http/1.1"]`. CF при запросе пытался поднять HTTP/2, а HTTP/2 не поддерживает стандартный WebSocket Upgrade.

**Фикс**: ALPN → `["http/1.1"]` только.

```bash
ssh root@207.154.214.108 'python3 -c "
import json
with open(\"/usr/local/etc/xray/config.json\") as f: cfg = json.load(f)
for ib in cfg[\"inbounds\"]:
    if ib.get(\"tag\") == \"vless-ws-tls-cf\":
        ib[\"streamSettings\"][\"tlsSettings\"][\"alpn\"] = [\"http/1.1\"]
with open(\"/usr/local/etc/xray/config.json\",\"w\") as f: json.dump(cfg,f,indent=2)
"
systemctl restart xray'
```

После этого WS заработал. В access.log пошли соединения от MTS (`92.39.222.59`) на Facebook, TikTok, Apple, Cloudflare через `vless-ws-tls-cf`.

---

## Шаг 4. Подняли VLESS+gRPC+TLS как альтернативу

CF поддерживает HTTP/2 порты для proxied: 443, 2053, 2083, 2087, 2096, 8443. Выбрали 2053.

**Inbound**:
```json
{
  "tag": "vless-grpc-cf",
  "listen": "0.0.0.0",
  "port": 2053,
  "protocol": "vless",
  "streamSettings": {
    "network": "grpc",
    "security": "tls",
    "tlsSettings": {"certificates": [...], "alpn": ["h2"]},
    "grpcSettings": {"serviceName": "vpnservice", "multiMode": false}
  }
}
```

**Важно**: ALPN должен быть `["h2"]` — gRPC требует HTTP/2.

---

## Шаг 5. Удалили routing rule блокирующий UDP на 443

В config.json была:
```json
{"type":"field", "outboundTag":"block", "protocol":["udp"], "port":"443"}
```

Это резало QUIC у Facebook, YouTube, Instagram. Удалили.

---

## Шаг 6. Subscription URL

В bot DB (`/opt/vpnbot/bot/bot.db` на `151.243.113.31`), таблица `configs`, id=11, поле `config_data`:

```
vless://e96153a1-bc1f-40b4-a5c3-33be76e3cf4d@vpn.maxvpnesim.shop:443?type=ws&security=tls&sni=vpn.maxvpnesim.shop&host=vpn.maxvpnesim.shop&path=%2Fvless-ws#Frankfurt-CF-WS-443
vless://e96153a1-bc1f-40b4-a5c3-33be76e3cf4d@vpn.maxvpnesim.shop:2053?type=grpc&security=tls&sni=vpn.maxvpnesim.shop&serviceName=vpnservice#Frankfurt-CF-gRPC-2053
vless://e96153a1-bc1f-40b4-a5c3-33be76e3cf4d@207.154.214.108:43100?type=tcp&security=reality&pbk=XX6fN0BsR_wne921FvbzfMkdwB7A-3LWGGbX4OZZKyM&fp=chrome&sid=89f829dc6c4849d7&sni=www.yahoo.com&spx=%2F&flow=xtls-rprx-vision#Frankfurt-Reality-43100
```

Обслуживается ботом на `https://maxvpnesim.com/sub/TpzndC5OPNS6-IJrM_VQiPFkkmieBQoe`.

---

## Шаг 7. Жалоба "идёт но недолго" — 1006 WebSocket close

Пользователь сообщил что VPN работает кратковременно. В error.log куча:
```
proxy/vless/encoding: failed to read packet length > websocket: close 1006 (abnormal closure): unexpected EOF
```

**Нашли две причины**:

### Причина А: `downlinkOnly: 10` в Xray policy

В `/usr/local/etc/xray/config.json`:
```json
"policy": {
  "levels": {
    "0": {
      "handshake": 8,
      "connIdle": 600,
      "uplinkOnly": 5,
      "downlinkOnly": 10
    }
  }
}
```

`downlinkOnly: 10` означает: если соединение 10 секунд только скачивает (без отправки) — Xray закрывает. Видео-стрим = чистый downlink, и Xray резал каждое соединение через 10 секунд.

**Фикс**: `downlinkOnly: 120`, `uplinkOnly: 30`.

### Причина Б: CF idle WebSocket timeout

CF закрывает idle WebSocket-соединение через ~75–100 секунд. В сессии 17:54–17:55:55 (91 секунда трафика), потом через ~77 секунд пошли 1006 закрытия.

**Фикс**: добавили `heartbeatPeriod: 30` в `wsSettings`. Xray шлёт WS PING каждые 30 секунд → CF не таймаутит.

```python
ws["heartbeatPeriod"] = 30
```

---

## Шаг 8. gRPC keepalive

Добавили в gRPC inbound:
```json
"grpcSettings": {
  "serviceName": "vpnservice",
  "multiMode": true,
  "idle_timeout": 60,
  "health_check_timeout": 20,
  "permit_without_stream": true
}
```

`multiMode: true` — лучше мультиплексирование (несколько стримов в одном h2-соединении).

---

## Шаг 9. Запушили доку

Создал `VPN_CF_BYPASS.md` в корне репо, закоммитил и запушил в `main`. Хэш `34740a8`.

---

## Текущее состояние Xray (07.05)

### Inbound'ы

| Тэг | Порт | Транспорт | dest/SNI | fp | Статус |
|---|---|---|---|---|---|
| `api` | 10085 (lo) | dokodemo | — | — | внутренний |
| `vless-reality-4443` | 4443 | tcp/Reality | www.microsoft.com:443 | — | живой |
| `vless-reality-base` | 43000–43009 | tcp/Reality | www.yahoo.com:443 | — | пустой |
| `vless-reality-max` | 43100–43109 | tcp/Reality | www.yahoo.com:443 | ios | **работает не из MTS** |
| `vless-reality-base-slow` | 43200–43209 | tcp/Reality | www.yahoo.com:443 | — | пустой |
| `vless-reality-max-slow` | 43300–43309 | tcp/Reality | www.yahoo.com:443 | — | пустой |
| `trojan-fallback` | unix sock | trojan | — | — | fallback |
| `vless-ws-tls-cf` | 443 | ws/TLS+CF | vpn.maxvpnesim.shop | — | **работает** |
| `vless-grpc-cf` | 2053 | grpc/TLS+CF | vpn.maxvpnesim.shop | — | сконфигурирован |

### Policy
```json
"levels": {
  "0": {
    "handshake": 8,
    "connIdle": 600,
    "uplinkOnly": 30,
    "downlinkOnly": 120
  }
}
```

### Outbounds
- `direct` (freedom, UseIPv4)
- `block` (blackhole)

### Routing
- `api` → api outbound
- `dns_internal` → direct
- (UDP-блок на 443 удалён)

---

## Наблюдения по DPI

**MTS (`92.39.222.x`)** режет Reality на `207.154.214.108`. ClientHello не доходит до сервера.

**Другой ISP (`46.147.128.x`)** Reality пропускает — в access.log активно идут соединения через `vless-reality-max:43100` к Apple, Cloudflare, Telegram, Facebook.

**Все ISP** должны работать через `vless-ws-tls-cf` и `vless-grpc-cf` (трафик идёт через CF IPs, ТСПУ их не трогает).

---

## Cloudflare состояние

- Zone: `maxvpnesim.shop`, ID `d2e997fc6301009962e295fd57483277`
- DNS `vpn.maxvpnesim.shop`: A-запись на `207.154.214.108`, `proxied: true`
- Firewall Rule `ce46145ba6594a17bb2ade7edad64f2c`: bypass для `/vless-ws` и `/vpnservice`
- Filter `fec63f6f68ef451d952e9d63b45b5a81`
- Security Level: `essentially_off`
- I'm Under Attack: OFF
- Bot Fight Mode: OFF
- SSL mode: Full (CF→origin TLS, без проверки cert)
- API token (zone settings + firewall): хранится локально (см. историю чата с Claude от 06.05)

---

## Открытые вопросы / TODO

- [ ] Проверить что после фикса `downlinkOnly: 120` + `heartbeatPeriod: 30` рилсы Instagram больше не умирают через 10–20 видео
- [ ] Reality `43100` не работает из MTS — оставить как есть (CF-конфиги покрывают этот случай) или подобрать альтернативный SNI/dest?
- [ ] gRPC через CF не тестировался из России — нужна проверка что `vless-grpc-cf:2053` живой
- [ ] В access.log ещё видны connections через `vless-reality-max` — это IP-адреса вне MTS работают, остальные через CF
- [ ] Можно убрать из подписки `Frankfurt-Reality-43100` если он не работает у большинства, или оставить как третий fallback
- [ ] Следить за CF bandwidth — при тяжёлом видео-трафике может прилетать throttling от CF (Free plan)

---

## Ключевые файлы и места

- Xray config: `/usr/local/etc/xray/config.json` на `207.154.214.108`
- Xray access log: `/var/log/xray-access.log`
- Xray error log: `/var/log/xray-error.log`
- TLS cert: `/etc/xray/certs/cert.pem`, `key.pem` (self-signed)
- Bot DB: `/opt/vpnbot/bot/bot.db` на `151.243.113.31`, таблица `configs` id=11
- Subscription endpoint: `https://maxvpnesim.com/sub/TpzndC5OPNS6-IJrM_VQiPFkkmieBQoe`
- VPN UUID для тестового пользователя: `e96153a1-bc1f-40b4-a5c3-33be76e3cf4d`
- Reality public key: `XX6fN0BsR_wne921FvbzfMkdwB7A-3LWGGbX4OZZKyM`
- Reality private key: `6Ct68CRWcK6KBKGPOkD8KF8Vb1QJC6HzCyDZ7RCM-lw`
- Reality shortId: `89f829dc6c4849d7`

---

## Команды-памятки

### Проверить что работает на сервере
```bash
ssh root@207.154.214.108 'systemctl status xray --no-pager | head -10'
ssh root@207.154.214.108 'tail -50 /var/log/xray-access.log'
ssh root@207.154.214.108 'tail -30 /var/log/xray-error.log'
```

### Проверить subscription
```bash
curl -s 'https://maxvpnesim.com/sub/TpzndC5OPNS6-IJrM_VQiPFkkmieBQoe' | base64 -d
```

### Проверить CF firewall rules
```bash
export CF_TOKEN="<token-with-firewall-services-edit>"
export ZONE_ID="d2e997fc6301009962e295fd57483277"
curl -s "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/firewall/rules" \
  -H "Authorization: Bearer $CF_TOKEN" | python3 -m json.tool
```

### Перезагрузить Xray
```bash
ssh root@207.154.214.108 'systemctl restart xray && sleep 2 && systemctl is-active xray'
```

# Рабочая схема: VLESS + Reality на FR-сервере

Зафиксировано после длительного перебора протоколов (см. историю в нижней секции). Эта конфигурация — **первая, которая дала нормальную скорость и стабильную работу с iPhone в РФ**, без вмешательства Cloudflare.

## Текущее состояние (рабочее)

| Параметр | Значение |
|---|---|
| Сервер | `207.154.214.108` (DigitalOcean Frankfurt) |
| Порт | `8443/TCP` |
| Inbound tag | `vless-reality-fr` |
| Протокол | VLESS + Reality (XTLS) |
| Маскировка под | `www.yahoo.com:443` |
| Reality privateKey | `6Ct68CRWcK6KBKGPOkD8KF8Vb1QJC6HzCyDZ7RCM-lw` |
| Reality publicKey | `XX6fN0BsR_wne921FvbzfMkdwB7A-3LWGGbX4OZZKyM` |
| Reality shortId | `89f829dc6c4849d7` (есть и пустой `""`) |
| Подтверждённый клиент | **Happ** на iOS (всё работает) |
| Известная проблема | Instagram частично виснет — лезет в QUIC/UDP, Reality TCP-only |

### Файл конфига Xray
`/usr/local/etc/xray/config.json` на `207.154.214.108`. Inbound `vless-reality-fr` — это секция, добавленная рядом с существующими (изначально на сервере был AWG/WG, мы их не трогали).

### URL для Happ (с flow=xtls-rprx-vision)
```
vless://2d2570ab-c830-44d1-929e-9b70c5628dc5@207.154.214.108:8443?encryption=none&flow=xtls-rprx-vision&security=reality&sni=www.yahoo.com&fp=chrome&pbk=XX6fN0BsR_wne921FvbzfMkdwB7A-3LWGGbX4OZZKyM&sid=89f829dc6c4849d7&type=tcp&headerType=none#VpnBot-Reality-FR
```
Email/тег в логах: `fr-reality@maxvpn`

### URL для V2Box (без flow — тестовый, может фейлится)
```
vless://0d0c24ff-28c1-43af-92dc-c2b25338201f@207.154.214.108:8443?encryption=none&security=reality&sni=www.yahoo.com&fp=chrome&pbk=XX6fN0BsR_wne921FvbzfMkdwB7A-3LWGGbX4OZZKyM&sid=89f829dc6c4849d7&type=tcp&headerType=none#VpnBot-Reality-FR-V2BOX
```
Email/тег в логах: `v2box-test@maxvpn`

QR-коды: `/root/vpn_test/fr_reality.png` и `/root/vpn_test/fr_reality_v2box.png` на сервере. Локально дублируются в `/tmp/vpn/` на Mac.

## Как воссоздать с нуля

Если Xray на сервере был сброшен, поднять inbound заново:

```bash
ssh root@207.154.214.108
```

```json
{
  "tag": "vless-reality-fr",
  "listen": "0.0.0.0",
  "port": 8443,
  "protocol": "vless",
  "settings": {
    "clients": [
      {"id": "<UUID-1>", "flow": "xtls-rprx-vision", "email": "fr-reality@maxvpn"}
    ],
    "decryption": "none"
  },
  "streamSettings": {
    "network": "tcp",
    "security": "reality",
    "realitySettings": {
      "show": false,
      "dest": "www.yahoo.com:443",
      "xver": 0,
      "serverNames": ["www.yahoo.com"],
      "privateKey": "6Ct68CRWcK6KBKGPOkD8KF8Vb1QJC6HzCyDZ7RCM-lw",
      "shortIds": ["89f829dc6c4849d7", ""]
    }
  },
  "sniffing": {"enabled": true, "destOverride": ["http", "tls"]}
}
```

```bash
iptables -A INPUT -p tcp --dport 8443 -j ACCEPT
systemctl restart xray
ss -ltnp | grep 8443   # должен слушать xray
```

Публичный ключ из приватного:
```bash
xray x25519 -i 6Ct68CRWcK6KBKGPOkD8KF8Vb1QJC6HzCyDZ7RCM-lw
```

## Сетевые оптимизации на сервере (151.243.113.31)

В sysctl применены: TCP BBR + увеличенные буферы. Сохранено в `/etc/sysctl.d/99-bbr.conf`. На FR-сервере (`207.154.214.108`) аналогично пока **не применялось** — если скорость на Reality просядет, повторить:

```
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
net.core.rmem_max=67108864
net.core.wmem_max=67108864
net.ipv4.tcp_rmem=4096 87380 67108864
net.ipv4.tcp_wmem=4096 65536 67108864
net.ipv4.tcp_fastopen=3
net.ipv4.tcp_mtu_probing=1
```

## Что пробовали и почему не подошло (для контекста)

Все эксперименты — с реального российского IP `85.140.68.14` (домашний Wi-Fi, провайдер AS31133).

| Протокол | Где | Результат |
|---|---|---|
| WireGuard на 51820/UDP | DO `207.154.214.108` | Handshake проходит, payload режется ТСПУ — клиент висит |
| WG на 443/UDP (тот же сервер) | то же | Чуть лучше handshake'ов, payload всё равно режется |
| AmneziaWG c дефолтными пресетами (Jc=4 и т.д.) | то же | Не помогло — РКН детектит дефолтный AWG |
| AmneziaWG агрессивные пресеты (Jc=10, Jmin=50, Jmax=1000) | то же | ~20 кбит/с, фактически нерабочее |
| VLESS+Reality на DO :443 | то же | "failed to read client hello" — ТСПУ режет TLS-ClientHello от iOS |
| VLESS+WS+TLS на `cdn.maxvpnesim.com` через **Cloudflare** | прод `151.243.113.31` через CF | Работало, но CF free-tier шейпит WS до 1–4 Mbps. Также периодически "умирало" из-за CF idle-timeout |
| VLESS+gRPC через Cloudflare на `:2096` | прод | Ещё медленнее WS — CF тротлит gRPC сильнее |
| VLESS+WS без CF (DNS only / grey) на `151.243.113.31` | прод | Полный timeout — ТСПУ режет любой TCP к этому RU-сайту, нужен CF как маска |
| VLESS+WS на `fr.maxvpnesim.com` (DO 207.154.x) **без CF** | DO Frankfurt | Caddy + Let's Encrypt отлично, TLS+WS upgrade пробивается, но iPhone после WS-upgrade не отправляет VLESS payload — TCP открыт, данных 0. Не работает с Happ, который зависает |
| **VLESS+Reality :8443 на DO Frankfurt** | DO Frankfurt | ✅ Работает в Happ, скорость нормальная. V2Box чудит на этом же URL |

### Ключевой вывод
- **151.243.113.31 (RU-зона)** — для прямых TCP/UDP заблокирован ТСПУ на любом порту. Работает только через Cloudflare proxy (но шейпинг).
- **207.154.214.108 (DigitalOcean Frankfurt)** — TCP проходит, ТСПУ его не режет на уровне маршрута. UDP/WG-fingerprint РКН умеет детектировать. **VLESS Reality TCP** маскируется под TLS-handshake к yahoo.com — проходит.

## Архитектура для прода (рекомендация)

Под клиентский бизнес — Reality на DO Frankfurt-серверах, выпускать peers через `vpnctl` Go-агент. Существующий код в `agent/xray/xray.go` уже умеет работать с Xray API. Добавить method:
- `xray.AddRealityUser(uuid, flow="xtls-rprx-vision", email)` — добавляет клиента в `vless-reality-fr` inbound через Xray API на `127.0.0.1:10085`
- `xray.RemoveUser(uuid)` — удаление

Бот при покупке VPN-плана:
1. Генерит UUID для клиента
2. Через `vpnctl` зовёт `AddRealityUser`
3. Возвращает `vless://...` URL клиенту с QR

Использовать **Happ** как рекомендованный клиент для iOS — он стабильно работает с Reality. V2Box можно поддержать опционально (профиль без `flow=xtls-rprx-vision`).

## Открытые задачи

- [ ] Instagram через Reality виснет (QUIC/UDP). Решения: либо настроить routing rules в клиенте на блокировку UDP/443, либо принять как known limitation
- [ ] Перенести существующий Reality-конфиг с прод-сервера 151.243.113.31:8443 на DO Frankfurt (текущий прод-Reality на RU-зоне ТСПУ полностью режет)
- [ ] Проверить почему V2Box не подключается к тому же серверу (возможно — vision flow или fingerprint)
- [ ] Добавить TCP BBR на FR-сервер
- [ ] Интегрировать Reality в `vpnctl` Go-агент (см. `agent/xray/`)

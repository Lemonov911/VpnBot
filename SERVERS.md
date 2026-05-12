# Инфраструктура серверов VpnBot

## Основной сервер (Web + Bot)

| Параметр | Значение |
|----------|----------|
| IP | 151.243.113.31 |
| ОС | Ubuntu 24.04 |
| Ресурсы | 1 vCPU / 2GB RAM / 80GB SSD |
| Домен | maxvpnesim.com |
| SSL | `/etc/ssl/certs/maxvpn.crt` |
| Nginx | `/etc/nginx/sites-enabled/maxvpn` |
| Бот | порт 8080 |
| Проект | `/opt/vpnbot/` |
| Git | `git@github.com:Lemonov911/VpnBot.git` |

### Nginx конфиг
- Статика: `webapp/dist/` отдаётся напрямую
- API: `/api/*` проксируется на `localhost:8080`

### Деплой
```bash
# Локально:
git push

# На сервере:
cd /opt/vpnbot && git pull
cd webapp && npm ci && npm run build
nginx -s reload
```

---

## VPN сервер (WireGuard)

| Параметр | Значение |
|----------|----------|
| IP | 207.154.214.108 |
| Провайдер | DigitalOcean |
| ОС | Ubuntu 24.04 |
| Интерфейс | wg0 |
| Подсеть | 10.8.0.1/24 |
| Порт | 51820/UDP |
| NAT | через eth0 |

---

## Установка WireGuard

```bash
# Установка
apt update && apt install -y wireguard

# Генерация ключей сервера
wg genkey | tee /etc/wireguard/server_private.key | wg pubkey > /etc/wireguard/server_public.key
```

---

## Конфиг сервера `/etc/wireguard/wg0.conf`

```ini
[Interface]
Address = 10.8.0.1/24
ListenPort = 51820
PrivateKey = <содержимое /etc/wireguard/server_private.key>

# NAT и форвардинг
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT
PostUp = iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT
PostDown = iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

# Клиенты добавляются сюда:
# [Peer]
# PublicKey = <публичный ключ клиента>
# AllowedIPs = 10.8.0.2/32
```

```bash
# Включение IP forwarding
echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
sysctl -p

# Запуск
wg-quick up wg0
systemctl enable wg-quick@wg0
```

---

## Пример конфига клиента `client.conf`

```ini
[Interface]
PrivateKey = <приватный ключ клиента>
Address = 10.8.0.2/24
DNS = 8.8.8.8

[Peer]
PublicKey = <публичный ключ сервера>
Endpoint = 207.154.214.108:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
```

---

## iptables правила (NAT)

```bash
# Разрешить форвардинг
iptables -A FORWARD -i wg0 -o eth0 -j ACCEPT
iptables -A FORWARD -i eth0 -o wg0 -j ACCEPT

# NAT (маскарадинг)
iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE

# Разрешить порт WireGuard
iptables -A INPUT -p udp --dport 51820 -j ACCEPT

# Сохранить правила
apt install -y iptables-persistent
netfilter-persistent save
```

---

## Добавление нового клиента

```bash
# На сервере:
wg genkey | tee client_private.key | wg pubkey > client_public.key

# Добавить [Peer] в /etc/wireguard/wg0.conf с AllowedIPs 10.8.0.X/32
# Перезапустить: wg syncconf wg0 /etc/wireguard/wg0.conf

# Сгенерировать client.conf с публичным ключом сервера и приватным ключом клиента
```

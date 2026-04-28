# Тестовый сервер — 151.243.113.31

## Что установлено для тестирования

### WireGuard
- Пакет: `wireguard`, `wireguard-tools`
- Интерфейс: `wg0` (10.8.0.1/24)
- Порт: `51820/udp`
- Конфиг: `/etc/wireguard/wg0.conf`
- Ключи: `/etc/wireguard/server_private.key`, `/etc/wireguard/server_public.key`
- Публичный ключ: `W0yfb+2PjdqdEtUOf/ZcQcyOSZE8vrngaJ7YC9TYw2o=`
- Сервис: `wg-quick@wg0`

### XRay (VLess)
- Версия: `26.3.27`
- Бинарник: `/usr/local/bin/xray`
- Конфиг: `/usr/local/etc/xray/config.json`
- Сертификаты: `/usr/local/etc/xray/server.crt`, `/usr/local/etc/xray/server.key`
- Порт: `8443/tcp` (TLS)
- API порт: `10085` (только localhost, для vpnctl)
- Сервис: `xray`
- Тестовый UUID: `2b1a7d8a-a6ac-485e-9c5e-2371ff9cd69f`

### vpnctl (агент управления)
- Бинарник: `/opt/vpnbot/agent/vpnctl`
- Конфиг: `/opt/vpnbot/agent/.env`
- Порт: `9000` (только localhost)
- Сервис: `vpnctl`

## Как почистить (снести всё тестовое)

```bash
# Остановить и удалить сервисы
systemctl stop vpnctl xray wg-quick@wg0
systemctl disable vpnctl xray wg-quick@wg0
rm /etc/systemd/system/vpnctl.service

# Удалить WireGuard
apt-get remove -y wireguard wireguard-tools
rm -rf /etc/wireguard/

# Удалить XRay
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ remove
rm -rf /usr/local/etc/xray/

# Удалить агента
rm -rf /opt/vpnbot/agent/

# Удалить правила iptables (добавленные WG)
iptables -D FORWARD -i wg0 -j ACCEPT 2>/dev/null
iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE 2>/dev/null
```

## Порты открытые для теста

| Порт | Протокол | Сервис |
|------|----------|--------|
| 51820 | UDP | WireGuard |
| 8443 | TCP | XRay VLess (TLS) |
| 9000 | TCP | vpnctl API (только localhost) |

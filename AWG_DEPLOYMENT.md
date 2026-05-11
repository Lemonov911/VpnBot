# AmneziaWG Server Deployment

Как раскатать новый bulletproof AmneziaWG-сервер и подключить к боту.
Вся обфускация (Jc/H1-H4/S1-S4) генерируется случайно для каждого
сервера — DPI MTS не может построить общий fingerprint.

## Что нужно

- VPS на любом провайдере (предпочтительно с **низким VPN-rank**: Aeza, netcup, Tom Gewiese, или просто свежий DO/Hetzner)
- Минимум 1 GB RAM (с 512 MB добавь swap)
- Ubuntu 22.04+ или Debian 12+
- Root SSH доступ

## 1. Установка AmneziaWG на сервер

```bash
ssh root@<NEW_IP>

# Скачать установщик из репо
curl -O https://raw.githubusercontent.com/Lemonov911/VpnBot/main/agent/scripts/awg-install.sh
chmod +x awg-install.sh

# Опционально переопределить параметры через env:
# AWG_PORT=51820 AWG_SUBNET=10.7.7.0/24 ./awg-install.sh
./awg-install.sh
```

Что скрипт делает:
1. Устанавливает `amneziawg` + `amneziawg-tools` из PPA
2. Генерирует **случайные** Jc/H1-H4/S1-S4 (уникальные для каждого сервера!)
3. Применяет sysctl-тюнинг (BBR, fq, большие буферы, ip_forward)
4. Поднимает `awg0` интерфейс с MASQUERADE + **TCP MSS clamp 1200 в обе стороны** (см. ниже)
5. Сохраняет конфиг в `/etc/amnezia/amneziawg/server-params.json`

### Зачем MSS clamp 1200

Это **критично для Windows-клиентов** через Amnezia VPN-большое приложение. Без MSS clamp на FORWARD-цепочке awg0 происходит фрагментация ответов от крупных CDN (Google/YouTube/Cloudflare) внутри туннеля — userspace `wireguard-go` на Windows не делает корректный PMTU discovery, TLS-handshakes повисают, маленькие сайты грузятся, большие нет.

Точное значение **1200** (не 1240 и не PMTU) — оставляет запас под AmneziaWG-обфускацию (Jc + S1-S4 + advanced-security overhead). На iPhone клампе не требуется — kernel-нативный AWG сам разбирается.

В PostUp/PostDown awg0.conf:
```
iptables -t mangle -A FORWARD -i %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1200
iptables -t mangle -A FORWARD -o %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1200
```

После установки видно:
```
Endpoint: <IP>:<PORT>
Public key: <BASE64>
Params saved: /etc/amnezia/amneziawg/server-params.json
```

## 2. Установка агента vpnctl

Скомпилируй на маке:
```bash
cd agent
GOOS=linux GOARCH=amd64 go build -o /tmp/vpnctl_linux .
```

Деплой на сервер:
```bash
scp /tmp/vpnctl_linux root@<IP>:/usr/local/bin/vpnctl
```

На сервере создай env:
```bash
ssh root@<IP>
mkdir -p /opt/vpnctl

# Сгенерируй secure token (запомни — нужен боту)
TOKEN=$(openssl rand -hex 32)
echo "$TOKEN" > /opt/vpnctl/token.txt
chmod 600 /opt/vpnctl/token.txt

cat > /opt/vpnctl/.env <<EOF
LISTEN_ADDR=:9001
AGENT_TOKEN=$TOKEN
SERVICES=awg
AWG_PARAMS_FILE=/etc/amnezia/amneziawg/server-params.json
LOG_LEVEL=info
EOF
chmod 600 /opt/vpnctl/.env
```

Systemd unit:
```bash
cat > /etc/systemd/system/vpnctl.service <<EOF
[Unit]
Description=VPN agent
After=network.target awg-quick@awg0.service

[Service]
Type=simple
WorkingDirectory=/opt/vpnctl
EnvironmentFile=/opt/vpnctl/.env
ExecStart=/usr/local/bin/vpnctl
Restart=on-failure
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now vpnctl
systemctl status vpnctl
```

## 3. Firewall: ограничить порт 9001 (агент) только для бота

```bash
# Замени <BOT_IP> на IP бот-сервера
iptables -I INPUT -p tcp --dport 9001 -s <BOT_IP> -j ACCEPT
iptables -A INPUT -p tcp --dport 9001 -j DROP
```

Открыть AmneziaWG UDP-порт (он в `server-params.json`):
```bash
PORT=$(jq -r .port /etc/amnezia/amneziawg/server-params.json)
iptables -I INPUT -p udp --dport $PORT -j ACCEPT
ufw allow $PORT/udp
```

## 4. Регистрация в БД бота

На бот-сервере:
```bash
ssh root@<BOT_HOST>
TOKEN=$(ssh root@<NEW_IP> 'cat /opt/vpnctl/token.txt')

sqlite3 /opt/vpnbot/bot/bot.db <<EOF
INSERT INTO servers
  (name, location, host, protocol, is_active,
   agent_url, agent_token, capacity, active_peers, flag, city)
VALUES
  ('Amsterdam AWG', '🇳🇱 Netherlands', '<NEW_IP>', 'awg', 1,
   'http://<NEW_IP>:9001', '$TOKEN', 100, 0, '🇳🇱', 'Amsterdam');
EOF
```

После этого бот сам начнёт раздавать конфиги с этого сервера юзерам которые покупают планы с `awg_slots > 0`.

## 5. Тест

```bash
# С бот-сервера: agent должен ответить health
curl -sS http://<NEW_IP>:9001/health

# Создать тестового пира
TOKEN=<agent_token>
curl -X POST http://<NEW_IP>:9001/services/awg/peers \
  -H "X-Agent-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"label":"manual-test"}' | python3 -m json.tool
```

Получишь полный конфиг с обфускацией — можно скопировать в Amnezia VPN app
и протестировать на MTS.

## Замечания по операциям

- **Каждый сервер уникален** — не копируй `server-params.json` между серверами!
- **Watch capacity** — `capacity=100` это soft-лимит, можно поднимать; на 1 GB RAM реально держится ~50-100 пиров AWG
- **Если IP сгорит у DPI** (бывает при массивном трафике) — переустанови сервер с нуля. Скрипт сгенерит новые params и DPI снова "ослепнет"
- **Health-check на стороне бота** TBD: scheduled task проверять что health endpoint отвечает + что peer active. Если фейл — `is_active=0` и юзеры мигрируют на другой сервер автоматически (но это пока не реализовано в коде)

## Стоимость моделирования

| Конфигурация | Юзеров | Цена | Доход @ 200₽ × N | Маржа |
|---|---|---|---|---|
| DO 1GB ($6) | 30-50 | $6 | $60-100 | 90% |
| Hetzner CCX22 (€10) | 50-80 | €10 | €100-160 | 90% |
| Hetzner CCX23 (€27) | 100-200 | €27 | €200-400 | 85% |
| 3× DO 1GB ($18) | 100+ | $18 | $200+ | 90% |

При **3+ серверах с автоматической ротацией** (не реализовано) ферма
становится устойчивой к точечным DPI-флагам — если один IP "погорел",
бот мигрирует юзеров на следующий, новый IP поднимаешь параллельно.

## Что осталось сделать (TODO)

- [ ] Health-checker в боте — определять deflag/flag сервера
- [ ] Auto-rotation — миграция юзеров с погоревшего сервера на свежий
- [ ] Subscription URL для AWG — отдавать клиентский конфиг как файл, не как vless URI
- [ ] Поддержка нескольких AWG-серверов в подписке одновременно (для failover в клиенте)

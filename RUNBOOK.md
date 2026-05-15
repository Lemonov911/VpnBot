⚠️ Operations Runbook
======================

Что делать когда что-то сломалось. Парент: [[CLAUDE.md]].

## TL;DR — где что

| Компонент | Где живёт | Как смотреть логи |
|---|---|---|
| Bot (Python) | VPS `151.243.113.31`, `systemd: vpnbot.service` | `ssh root@151.243.113.31 'journalctl -u vpnbot -f'` |
| Agent (Go) | VPN-сервер `68.183.15.95`, `systemd: vpnctl-awg.service` | `ssh root@68.183.15.95 'journalctl -u vpnctl-awg -f'` |
| Mini App | GitHub Pages `lemonov911.github.io/VpnBot` | GH Actions logs |
| Admin panel | `151.243.113.31`, `systemd: vpnbot-admin.service` | `journalctl -u vpnbot-admin -f` |
| DB | `/opt/vpnbot/bot.db` (SQLite) | `sqlite3 /opt/vpnbot/bot.db` |

## 🚨 Бот лежит (юзеры пишут «не открывается»)

```bash
ssh root@151.243.113.31 'systemctl status vpnbot --no-pager'
```

Если `failed` или `inactive`:
```bash
ssh root@151.243.113.31 'journalctl -u vpnbot -n 50 --no-pager'
```

Типовые причины:
- **`Failed to load environment files`** — пропал `/opt/vpnbot/.env`. Восстанови из backup'а в TG-чате с админом.
- **`No module named X`** — пропал venv. Пересобери: `cd /opt/vpnbot && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt`.
- **`database is locked`** — кто-то держит писательский lock. `lsof /opt/vpnbot/bot.db` чтобы найти. После `_connect()` рефактора это маловероятно (busy_timeout=5s).

Рестарт: `systemctl restart vpnbot`.

## 🚨 VPN не работает (юзеры не подключаются)

1. Проверь агента (probe):
   ```bash
   ssh root@151.243.113.31 'cd /opt/vpnbot && ./venv/bin/python -m services.vpnctl_client probe 8'
   ```
   `OK: 2 / FAIL: 0` — агент жив. Если 401 — проверь `agent_token` в БД vs `/opt/vpnctl/.env` на сервере.

2. Проверь Xray (VLESS) и awg:
   ```bash
   ssh root@68.183.15.95 '
     systemctl status vpnctl-awg --no-pager | head -10
     systemctl status xray --no-pager | head -10
     wg show awg0 | head -20
   '
   ```

3. tc rules целы:
   ```bash
   ssh root@68.183.15.95 'tc qdisc show dev eth0; tc qdisc show dev awg0'
   ```
   Если `tc-vless-slow.service` не вернулся после рестарта VPS — сделай `systemctl restart tc-vless-slow`.

## 🚨 Health-alert «backup не было N дней»

Бот шлёт это в TG-чат админа. Лечится:
1. `ssh root@151.243.113.31 'df -h /'` — диск переполнен?
2. `journalctl -u vpnbot --since "3 days ago" | grep -i backup` — какая ошибка?
3. Ручной trigger: `rm /opt/vpnbot/.last_backup_date && systemctl restart vpnbot` — после следующего тика scheduler сделает backup.

## 🚨 Сервер VPN упал (health-checker отправил alert)

Health-probe бьёт каждые 60с. После 10 проб подряд down → `is_active=0` + alert в TG. После 5 up подряд → реактивация.

Если сервер не вернётся за 30 мин:
1. SSH на провайдера, чек статуса VPS
2. Если VPS живой но агент сломан: `ssh root@68.183.15.95 'systemctl restart vpnctl-awg xray'`
3. Если VPS мёртв — пока что нет fallback (SPoF, см. backlog). Юзерам пишем «работаем над восстановлением».

## 💀 Disaster recovery — потеря VPS

### Полное восстановление с нуля (~1 час)

1. **Поднять новый VPS** (Ubuntu 24.04, любой провайдер для бота — DO, Hetzner, российский dedik):
   ```bash
   apt update && apt install -y python3-venv git nginx sqlite3
   useradd -r -s /bin/false vpnbot   # или просто запускаем под root как сейчас
   ```

2. **Развернуть код**:
   ```bash
   git clone https://github.com/Lemonov911/VpnBot /opt/vpnbot
   cd /opt/vpnbot
   python3 -m venv venv
   ./venv/bin/pip install -r requirements.txt
   ```

3. **Восстановить `.env`** (главные секреты):
   - Скачать последний `bot-db-YYYY-MM-DD.gz` из TG-чата с админом — это backup БД (sub_tokens redacted)
   - `.env` секреты живут в **1Password vault `vpnbot-prod`** (либо в моём `.env.local` бэкапе). Содержит:
     - `BOT_TOKEN`, `ADMIN_ID`, `WEBAPP_URL`
     - `CRYPTOBOT_TOKEN`
     - `VPN_SERVER_PASSWORD` (legacy SSH, можно не восстанавливать)
     - `ESIM_ACCESS_API_KEY`
   - Закинуть в `/opt/vpnbot/.env`

4. **Восстановить БД**:
   ```bash
   gunzip /tmp/bot-db-YYYY-MM-DD.gz > /opt/vpnbot/bot.db
   # Проверка
   sqlite3 /opt/vpnbot/bot.db "SELECT COUNT(*) FROM users; SELECT COUNT(*) FROM subscriptions"
   ```

5. **Systemd unit**:
   ```bash
   cat > /etc/systemd/system/vpnbot.service <<'EOF'
   [Unit]
   Description=VPN Telegram Bot
   After=network.target

   [Service]
   Type=simple
   User=root
   WorkingDirectory=/opt/vpnbot
   ExecStart=/opt/vpnbot/venv/bin/python bot.py
   Restart=always
   RestartSec=5
   EnvironmentFile=/opt/vpnbot/.env

   [Install]
   WantedBy=multi-user.target
   EOF
   systemctl daemon-reload && systemctl enable --now vpnbot
   ```

6. **nginx для Mini App API** (если нужен webapp прокси):
   ```bash
   # /etc/nginx/sites-available/vpnbot
   server {
       listen 80;
       server_name <new-ip-or-domain>;
       location /api/ {
           proxy_pass http://127.0.0.1:8080;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
       }
   }
   ```

7. **DNS / GitHub secrets** — обнови:
   - DNS A-запись `maxvpnesim.com` → новый IP
   - GitHub Actions secret `VITE_API_URL` → `https://<new-host>`
   - Передеплой webapp: пуш в `webapp/**` или Re-run в GH Actions

8. **Восстановление потерянных подписок** (если backup старый):
   - Открой Telegram → Bot Manager → Payments → выгрузи список Stars-транзакций за период
   - Для каждой неупомянутой в БД транзакции вручную: `sqlite3 bot.db "INSERT INTO subscriptions ..."`
   - Или попроси юзеров написать в саппорт — они вышлют скрин чека

### VPN-сервер (Agent) — восстановление

1. Новый VPS Ubuntu, 1 CPU, 1 GB RAM хватит
2. `bash <(curl -sL https://raw.githubusercontent.com/Lemonov911/VpnBot/main/agent/scripts/awg-install.sh)` — поднимает AmneziaWG
3. `bash <(curl -sL .../xray-install.sh)` — поднимает Xray VLESS
4. Скопировать `vpnctl_awg` бинарь из локальной сборки (`GOOS=linux go build -o /tmp/vpnctl_linux .`)
5. `/opt/vpnctl/.env` — токен для HMAC аутентификации (произвольный 64-hex). Обновить **в БД бота** для соответствующего `servers.agent_token`.
6. Systemd:
   ```bash
   # /etc/systemd/system/vpnctl-awg.service
   [Service]
   ExecStart=/usr/local/bin/vpnctl_awg
   EnvironmentFile=/opt/vpnctl/.env
   ```

## 📦 Бекапы — где они

- **Daily** — в TG-чате с админом (бот шлёт сам). Sub_tokens redacted (нужны для подписочной ссылки).
- **Полный snapshot системы** — `/root/vpnbot_backup_*.tar.gz` на bot VPS. Делается **вручную перед рискованными изменениями**:
  ```bash
  ssh root@151.243.113.31 'cd / && tar czf /root/vpnbot_backup_$(date +%Y%m%d_%H%M%S).tar.gz opt/vpnbot'
  ```
- **Off-site второй канал** — пока нет (TODO в Технический долг).

## 🔧 Диагностические команды

```bash
# Сколько юзеров в проде
sqlite3 /opt/vpnbot/bot.db "SELECT COUNT(*) FROM users; SELECT COUNT(*) FROM subscriptions WHERE status IN ('active','grace')"

# Кто истекает в ближайшие 24 часа
sqlite3 /opt/vpnbot/bot.db "SELECT user_id, plan, expires_at FROM subscriptions WHERE status='active' AND expires_at < datetime('now', '+1 day')"

# Сколько пиров на агенте по протоколам
ssh root@68.183.15.95 'wg show awg0 dump | wc -l'   # AWG peers
ssh root@68.183.15.95 'curl -sS localhost:9001/health'   # quick liveness

# Сверить БД и агент (orphan check)
# Все vless UUID в БД:
sqlite3 /opt/vpnbot/bot.db "SELECT vless_uuid FROM configs WHERE protocol='vless' AND status='active'"
# Все vless UUID на агенте: probe → list_peers (см. vpnctl_client)
```

## 🆘 Эскалация

Если что-то непонятное — лог в [[Технический долг]] + ping в админ-чат.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Overview

VpnBot — Telegram Mini App для продажи VPN-подписок (Amnezia WireGuard + VLESS) и eSIM. Состоит из четырёх компонентов:

| Компонент | Стек | Назначение |
|---|---|---|
| `bot/` | Python, aiogram v3, aiohttp | Telegram-бот + REST API для Mini App |
| `webapp/` | React + TypeScript + Vite + Tailwind v4 | Telegram Mini App (фронтенд) |
| `agent/` | Go | HTTP-агент на VPN-сервере, управляет WireGuard/VLESS |
| `admin/` | Next.js | Внутренняя панель администратора |

---

## Commands

### Bot (Python)
```bash
cd bot
pip install -r requirements.txt   # установка зависимостей
python3 bot.py                    # запуск (читает bot/.env)
```

### Webapp (React)
```bash
cd webapp
npm install
npm run dev      # dev-сервер на :5173, proxy /api → localhost:8080
npm run build    # production-сборка в webapp/dist/
npm run preview  # превью production-сборки на :4173
```

### Agent (Go)
```bash
cd agent
go build -o vpnctl .   # сборка бинаря
./vpnctl               # запуск (читает agent/.env)
```

### Admin (Next.js)
```bash
cd admin
npm install
npm run dev    # dev-сервер на :3000
npm run build
```

---

## Architecture

### Деплой и топология

```
Telegram → бот polling (aiogram)
Telegram → Mini App (GitHub Pages) → nginx → aiohttp API (127.0.0.1:8080)
Admin Panel → SQLite (bot.db)
Bot → Go agent HTTP API → WireGuard/Xray на VPN-сервере
```

- **VPS бота**: `151.243.113.31` — systemd-сервис `/etc/systemd/system/vpnbot.service`, Python venv в `/opt/vpnbot/`
- **nginx** на VPS: проксирует `/api/` → `127.0.0.1:8080`
- **GitHub Pages**: `lemonov911.github.io/VpnBot` — собирается и деплоится через GitHub Actions при пуше в `webapp/**`
- **VPN-сервер**: `68.183.15.95` (DigitalOcean Amsterdam) — там крутится:
  - Go-агент `vpnctl-awg.service` (бинарь `/usr/local/bin/vpnctl_awg`, env `/opt/vpnctl/.env`) — обслуживает **awg + wg + vless** (SERVICES=awg,wg,vless-base,vless-max,vless-base-slow,vless-max-slow,vless-grace)
  - `xray.service` — VLESS Reality. Порты: **max-тир = 443** (мигрирован 28.05, см. ниже), base 8443, base-slow 9443, max-slow 9448, grace 9453.
  - SNI в проде = `www.microsoft.com` ВЕЗДЕ (28.05.2026: `servers.xray_sni` для всех 3 (id 11/14/17) выставлен в `www.microsoft.com`, 7 битых конфигов с `sni=yandex.ru` пропатчены). Предыстория: пробовали yandex.ru для shutdown-bypass — провал. yandex.ru с EU-серверов гео-резолвится на **турецкий** Яндекс (cert `*.yandex.tr`) → cert не совпадает с SNI `yandex.ru` → Reality handshake рвётся. **yandex.ru негоден как Reality-dest.** vk.com / ok.ru — годны (cert `*.vk.com`/`*.ok.ru`, TLS1.3+h2, и в whitelist операторов) — кандидаты если вернёмся к whitelist-bypass. pubkey/short_id в `/opt/vpnctl/.env`.
  - **xray config.json**: realitySettings.dest=`www.microsoft.com:443`, serverNames содержат `www.microsoft.com` во всех vless-reality-* inbounds. Per-server SNI лежит в БД `servers.xray_sni`.
  - 🔴 **ROOT CAUSE «VPN не работает на мобильном» (28.05.2026): нестандартные порты (8443/8448/9443/…) режутся РФ-мобильным DPI.** Подтверждено на тестовых пирах (Олег, Frankfurt): VLESS на 8448 — timeout на мобильном (всегда) + флап на WiFi; на **443 — работает на мобильном**. xray сам warn'ит «Listening on non-443 ports may get your IP blocked». AWG работает т.к. UDP на своём порту. **Launch-блокер №1.** Затык: на одном IP 443 = только 1 inbound, а тиров 5 (tc-by-port для скоростей).
    - ✅ **СДЕЛАНО 28.05: max-тир мигрирован на 443 на ВСЕХ 3 серверах** (xray inbound 8448→443 + `servers.xray_port_max=443` для id 11/14/17). Провижининг агента — по тегу, порт-независим, поэтому безопасно. max = full speed, tc не режет. max-юзеры (vpn_max) работают на мобильном.
    - ⚠️ **PENDING: base-тир (vpn_base) ещё на 8443** → на мобильном не работает. base+max оба на 443 нельзя (1 IP = 1 inbound) → нужна консолидация: base+max в один 443-inbound, разница скоростей через tc-by-peer (не порт). Трогает агент (теги сервисов) + bot (`vless_service_for_plan`/`vless_port_column`) + переезд base-юзеров (3). Отдельной сессией. См. [[obsidian: Технический долг]] секция 🔴🔴.
    - Агент env (`/opt/vpnctl/.env` max InboundPort=8448) слегка stale после миграции — для full-speed тира безвредно, подчистить при консолидации.
  - В БД бота зарегистрированы 3 записи: id=8 AWG, id=10 WG, id=11 VLESS (все указывают на этот же IP)

### Bot (`bot/`)

Точка входа: `bot.py` — запускает polling aiogram + aiohttp на одном event loop.

```
bot.py
├── config.py          ← читает bot/.env
├── handlers/
│   ├── start.py       ← /start, реферальные ссылки
│   ├── vpn.py         ← платёжные хендлеры (Stars, CryptoBot)
│   └── admin.py       ← команды администратора
└── services/
    ├── database.py    ← все SQL-запросы (aiosqlite, bot.db)
    ├── webapp_api.py  ← aiohttp REST API + CORS middleware
    ├── vpn.py         ← генерация WG-конфигов через SSH/агент
    ├── vpnctl_client.py ← HTTP-клиент к Go-агенту
    ├── esim_api.py    ← esimaccess.com API + in-memory кеш пакетов
    ├── payments.py    ← Telegram Stars инвойсы
    ├── cryptobot.py   ← CryptoBot (USDT/RUB оплата)
    ├── scheduler.py   ← фоновый планировщик (истечение подписок, напоминания)
    └── auth.py        ← валидация Telegram initData (HMAC)
```

**БД `bot.db`** — SQLite, лежит в `bot/bot.db`. Схема автомигрируется при старте.

Основные таблицы:
- `users` — telegram_id, username, referred_by, ref_bonus_days
- `subscriptions` — plan, status (active/expired), expires_at, pending_plan
- `configs` — слоты конфигов: status (empty/active), protocol (awg/vless), peer_name, config_data
- `servers` — VPN-серверы с agent_url/agent_token для Go-агента
- `support_tickets` — тикеты поддержки
- `payments` — лог платежей
- `orders` — устаревшая таблица заказов (используется рядом с subscriptions)

**Жизненный цикл конфига**: `empty` (слот куплен) → `active` (конфиг создан на сервере). Отзыв → снова `empty`, слот остаётся.

**CORS**: `webapp_api.py` содержит middleware, разрешающий запросы с `https://lemonov911.github.io` и `localhost:5173/4173`.

**Планы VPN** (`webapp_api.py`): `vpn_start` (1 AWG), `vpn_popular` (2 AWG + 1 VLESS), `vpn_pro` (3 AWG + 2 VLESS), `vpn_family` (5 AWG + 3 VLESS).

**eSIM**: цена из API `price / 10000 * 1.45 * 90` → рубли. Пакеты кешируются в памяти при старте.

### Webapp (`webapp/`)

React SPA, открывается внутри Telegram через `WebApp.openWebApp()`.

```
src/
├── App.tsx         ← BrowserRouter, GlobalHeader, маршруты
├── api/index.ts   ← fetch-клиент (VITE_API_URL + путь)
├── i18n.tsx       ← useT(), useLang(), usePlural() — RU/EN
├── components/
│   ├── BottomNav.tsx  ← навигация (адаптируется под light/dark тему Telegram)
│   └── GlobalHeader.tsx
└── pages/
    ├── Home.tsx        ← дашборд, карточки VPN/eSIM, баннер реферала
    ├── VPN.tsx         ← управление подпиской
    ├── Plans.tsx       ← выбор плана, оплата
    ├── Configs.tsx     ← список конфигов, скачать/QR/отозвать
    ├── ESim.tsx        ← список пакетов eSIM
    ├── ESimCountry.tsx ← пакеты по стране
    ├── ESimFAQ.tsx
    ├── Instructions.tsx
    ├── Support.tsx     ← форма обращения
    └── Referral.tsx    ← реферальная ссылка, статистика
```

**Ключевые паттерны**:
- `@twa-dev/sdk` → `WebApp` из Telegram для HapticFeedback, BackButton, HapticFeedback, цветовой схемы
- `useT(key)` для переводов — ключи определены в `i18n.tsx` в объектах `T.ru` / `T.en`
- Все API-запросы: `fetch(API_BASE + '/api/...')` где `API_BASE = import.meta.env.VITE_API_URL ?? ''`
- `BASE_URL` (из Vite) используется для статики: `import.meta.env.BASE_URL + 'logo.png'`
- Tailwind v4: `@import "tailwindcss"` в `index.css`, плагин `@tailwindcss/vite`
- Тема: CSS-переменные `--tg-theme-*` от Telegram + кастомные `--card-border`, `--color-primary`, etc.

**CI/CD**: `.github/workflows/deploy-webapp.yml` — триггер на `webapp/**`, secrets: `VITE_BOT_USERNAME`, `VITE_API_URL`.

> ⚠️ Используется `BrowserRouter`. Прямые URL (не через навигацию) ломаются на GitHub Pages без `404.html` redirect.

### Agent (`agent/`)

Go-сервис `vpnctl`, запускается на VPN-сервере. Bot обращается к нему через HTTP с токеном авторизации.

```
agent/
├── main.go          ← HTTP-сервер, инит WG + Xray менеджеров
├── api/handlers.go  ← REST-эндпоинты (add/remove peer, stats)
├── wg/manager.go    ← WireGuard: добавление/удаление пиров через wg-cli
├── xray/xray.go     ← VLESS: управление пользователями Xray
├── fairshare/       ← Fair-share планировщик bandwidth
├── watchdog/        ← watchdog для процесса агента
└── config/          ← конфигурация из env
```

Бот взаимодействует с агентом через `bot/services/vpnctl_client.py`.

### Admin (`admin/`)

Next.js панель. Читает тот же `bot.db` через `lib/db.ts` (better-sqlite3 или аналог). Страницы: дашборд со статистикой, серверы, тикеты, пользователи.

---

## Environment Variables

**`bot/.env`**:
```
BOT_TOKEN=             # Telegram Bot API токен
ADMIN_ID=              # Telegram ID администратора
WEBAPP_URL=            # URL Mini App (https://lemonov911.github.io/VpnBot)
API_PORT=8080
DEBUG=false
VPN_SERVER_HOST=       # IP VPN-сервера (для legacy SSH-генерации конфигов)
VPN_SERVER_PASSWORD=   # пароль root на VPN-сервере
CRYPTOBOT_TOKEN=       # токен CryptoBot для RUB-платежей
ESIM_ACCESS_API_KEY=   # ключ esimaccess.com (опционально)
```

**GitHub Actions secrets** (репозиторий `Lemonov911/VpnBot`):
- `VITE_BOT_USERNAME` — username бота без @
- `VITE_API_URL` — базовый URL API (`http://151.243.113.31`)

---

## VPS Operations

```bash
# Перезапуск бота вручную
ssh root@151.243.113.31 'systemctl restart vpnbot'

# Логи в реальном времени
ssh root@151.243.113.31 'journalctl -u vpnbot -f'

# Деплой бота — через CI (`.github/workflows/deploy-bot.yml`). На push в
# main с изменениями в `bot/**` CI ssh'ится на сервер и форсит
# `/opt/vpnbot/deploy-bot.sh` (forced-command в authorized_keys + sudoers),
# который делает `cd /opt/vpnbot && git pull --ff-only origin main` +
# systemctl restart + healthcheck.
#
# То есть достаточно `git push origin main` — CI сделает остальное.
#
# ⚠️ НЕ ИСПОЛЬЗОВАТЬ rsync напрямую в /opt/vpnbot/bot/ — путает git working
# tree (rsync'нутые файлы видны как «modified», следующий CI deploy падает
# с «Your local changes to the following files would be overwritten by
# merge»). Один раз уже выстрелили 2026-05-17 — час восстановления.
#
# Если действительно нужен hotfix без push: сделать commit и push, потом
# уже CI задеплоит. Если push временно невозможен — rsync в `/tmp/`, затем
# на сервере `git stash && mv tmp/* /opt/vpnbot/bot/` + restart, но потом
# обязательно `git stash drop` + reconcile.

### ⚠️ DESTRUCTIVE OPS ON PROD — ПРАВИЛА

**Перед любым `rm -rf`, `mv`, `chmod -R` на проде:**

1. **`ls -la <target>` ОБЯЗАТЕЛЬНО** — узнать что там реально лежит
2. **Если есть файлы с возрастом > 1 дня** → это НЕ временный мусор, делай tar snapshot:
   ```bash
   tar czf /root/safety_$(date +%s).tar.gz <target>
   ```
3. **Только потом** — `rm -rf`

**Production бот живёт в `/opt/vpnbot/`** (НЕ `/opt/vpnbot/bot/`!). Раньше был легаси-deploy в `bot/` подпапке с venv + .env. **Snapshot от 12.05** лежит в `/root/vpnbot_backup_20260512_102844.tar.gz` — последний known-good если что.

**Production БД:** `/opt/vpnbot/bot.db`. Daily backup уходит админу в TG. На случай факапа — gunzip из чата → положить как `bot.db` → restart.

**Урок 2026-05-15:** `rm -rf /opt/vpnbot/bot` потёр прод-БД + .env + venv. Восстановилось из 12.05 backup'а, но 3 дня данных пропало. Не повторять.

---

## Key Notes

- **Платежи Telegram Stars**: `currency="XTR"`, обработка в `handlers/vpn.py` через `pre_checkout_query` и `successful_payment`
- **Авторизация API**: каждый запрос от Mini App несёт `X-Telegram-Init-Data` заголовок; `auth.py` проверяет HMAC-SHA256 подпись с `BOT_TOKEN`
- **Протоколы VPN**: `awg` = Amnezia WireGuard, `vless` = Xray VLESS. Сервер выбирается по наименьшей загрузке (`active_peers / capacity`)
- **Реферальная система**: `/start ref_<user_id>` → записывает `referred_by`; при первой покупке рефереру +7 дней к подписке
- **Планировщик** (`scheduler.py`): каждые 30 мин проверяет истёкшие подписки, отзывает конфиги через агента, шлёт напоминания за 3 дня и 1 день до истечения

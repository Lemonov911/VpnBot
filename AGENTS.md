# VpnBot — Project Context

## Architecture
- **Frontend**: React + TypeScript (Vite) in `webapp/` — Telegram WebApp SPA
- **Backend**: Python (aiogram) bot in `bot/` + `bot.py`
- **Web server**: Nginx on the VPS, serves `webapp/dist/` and proxies `/api/` to bot on port 8080
- **Localization**: i18n via `webapp/src/i18n.tsx` — `useT()` hook, `usePlural()` for RU/EN plural forms
- **Deploy**: Push to `main` → pull on server + `npm run build` in `webapp/`

## Server
- **VPS**: 151.243.113.31 (Ubuntu 24.04, 1vCpu/2GB/80GB)
- **Project path on server**: `/opt/vpnbot/`
- **Nginx config**: `/etc/nginx/sites-enabled/maxvpn`
- **Domain**: `maxvpn.shop` (SSL via `/etc/ssl/certs/maxvpn.crt`)
- **Bot runs on**: port 8080

## Deploy Steps
```bash
# On local:
git push

# On server:
cd /opt/vpnbot && git pull
cd webapp && npm ci && npm run build
nginx -s reload
```

## Localization Pattern
- All text goes into `webapp/src/i18n.tsx` as `ru` and `en` keys
- Use `const t = useT()` then `t('key_name')`
- Plurals: `const p = usePlural()` then `p(count, { ru: ['1','2','5'], en: 'items' })`
- Never hardcode Russian text in components

## Git
- Remote: `git@github.com:Lemonov911/VpnBot.git`
- Branch: `main`
- `.env` files are in `.gitignore`
- Never commit passwords/tokens — use `.env` for secrets, `~/.config/vpnbot/` for server info

## Key Files
- `webapp/src/i18n.tsx` — all translations (ru/en)
- `webapp/src/pages/*.tsx` — all page components
- `webapp/src/components/PaymentSheet.tsx` — payment UI
- `webapp/src/components/BottomNav.tsx` — tab bar
- `webapp/src/components/LangSwitch.tsx` — language switcher
- `bot.py` / `bot/` — Telegram bot logic
- `database.py` — SQLite DB
- `config.py` — env vars loading

## Testing Locally
```bash
cd webapp && npm run dev     # Vite dev server
cd webapp && npm run build   # Production build
```
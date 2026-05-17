#!/bin/bash
# Pulls latest main, builds admin Next.js, restarts vpnbot-admin. Invoked
# from CI via ssh deploy@host (forced-command runs this under sudo).
# Owner: root, mode 750. Only deploy can sudo it (/etc/sudoers.d/deploy).
set -euo pipefail

# Shared lock — bot and admin deploys touch the same /opt/vpnbot/.git.
exec 9>/var/lock/vpnbot-deploy.lock
flock -x -w 300 9 || { echo "[deploy-admin] could not acquire deploy lock in 300s" >&2; exit 1; }

cd /opt/vpnbot

# Dirty-tree guard — см. deploy-bot.sh для контекста.
DIRTY=$(git -c safe.directory=/opt/vpnbot status --porcelain --untracked-files=no)
if [ -n "$DIRTY" ]; then
  echo "[deploy-admin] REFUSE — tracked files modified:" >&2
  echo "$DIRTY" >&2
  exit 1
fi

echo "[deploy-admin] $(date -Is) — pulling..."
git -c safe.directory=/opt/vpnbot pull --ff-only origin main
echo "[deploy-admin] HEAD now: $(git -c safe.directory=/opt/vpnbot log --oneline -1)"

cd /opt/vpnbot/admin
echo "[deploy-admin] npm ci..."
npm ci --no-audit --no-fund
echo "[deploy-admin] npm run build..."
npm run build

# Next.js standalone build НЕ копирует .next/static и public/ в
# .next/standalone/ — документированный gotcha. На проде server.js
# запускается из standalone и без копирования вернёт 404 на CSS/JS chunks.
# https://nextjs.org/docs/app/api-reference/next-config-js/output#automatically-copying-traced-files
echo "[deploy-admin] copying static assets into standalone..."
rm -rf .next/standalone/.next/static
cp -r .next/static .next/standalone/.next/static
if [ -d public ]; then
  rm -rf .next/standalone/public
  cp -r public .next/standalone/public
fi

systemctl restart vpnbot-admin

# Healthcheck — admin (Next.js standalone) listens on 127.0.0.1:3001.
# basePath=/admin → корень редиректит, проверяем /admin/login которая
# точно отдаёт 200 OK без auth.
echo "[deploy-admin] healthcheck..."
ok=0
for i in $(seq 1 15); do
  if curl -fsS -m 3 -o /dev/null http://127.0.0.1:3001/admin/login; then
    ok=1; break
  fi
  sleep 1
done
if [ "$ok" -eq 1 ] && systemctl is-active --quiet vpnbot-admin; then
  echo "[deploy-admin] vpnbot-admin healthy OK"
else
  echo "[deploy-admin] vpnbot-admin UNHEALTHY — last 30 log lines:" >&2
  journalctl -u vpnbot-admin -n 30 --no-pager >&2
  exit 1
fi

#!/bin/bash
# Pulls latest main and restarts vpnbot. Invoked from CI via ssh deploy@host
# (forced-command runs this script under sudo).
# Owner: root, mode 750. Only deploy can sudo it (/etc/sudoers.d/deploy).
set -euo pipefail

# Shared lock — bot and admin deploys touch the same /opt/vpnbot/.git.
exec 9>/var/lock/vpnbot-deploy.lock
flock -x -w 300 9 || { echo "[deploy-bot] could not acquire deploy lock in 300s" >&2; exit 1; }

cd /opt/vpnbot

# Dirty-tree guard. Если кто-то rsync'нул в /opt/vpnbot/bot/ (или ручно
# правил файлы) — git working tree расходится с HEAD. `git pull --ff-only`
# тут падает с конфузным «Your local changes would be overwritten», уже
# поймали этот сценарий 2026-05-17. Ловим явно — лучше понятная ошибка.
DIRTY=$(git -c safe.directory=/opt/vpnbot status --porcelain)
if [ -n "$DIRTY" ]; then
  echo "[deploy-bot] REFUSE — working tree dirty:" >&2
  echo "$DIRTY" >&2
  echo "[deploy-bot] manual recovery on prod:" >&2
  echo "  cd /opt/vpnbot && git -c safe.directory=/opt/vpnbot stash push -u" >&2
  echo "  # затем review stash, drop если duplicate, иначе pop & resolve" >&2
  exit 1
fi

echo "[deploy-bot] $(date -Is) — pulling..."
git -c safe.directory=/opt/vpnbot pull --ff-only origin main
echo "[deploy-bot] HEAD now: $(git -c safe.directory=/opt/vpnbot log --oneline -1)"
systemctl restart vpnbot

# Healthcheck phase 1 — bot listens on 127.0.0.1:8080 with /api/health.
# Ждём до 15с пока бот ответит.
echo "[deploy-bot] healthcheck phase 1 (HTTP)..."
ok=0
for i in $(seq 1 15); do
  if curl -fsS -m 3 -o /dev/null http://127.0.0.1:8080/api/health; then
    ok=1; break
  fi
  sleep 1
done
if [ "$ok" -ne 1 ] || ! systemctl is-active --quiet vpnbot; then
  echo "[deploy-bot] vpnbot UNHEALTHY (no /api/health response)" >&2
  journalctl -u vpnbot -n 50 --no-pager >&2
  exit 1
fi

# Healthcheck phase 2 — ждём 30 сек и проверяем что не crashloop'ит.
# Многие баги вылазят после init (handlers/scheduler/etc.).  Если бот
# рестартнулся >1 раза с момента deploy — что-то падает, не считаем OK.
echo "[deploy-bot] healthcheck phase 2 (post-init stability, 30s)..."
restarts_before=$(systemctl show vpnbot -p NRestarts --value 2>/dev/null || echo 0)
sleep 30
if ! systemctl is-active --quiet vpnbot; then
  echo "[deploy-bot] vpnbot crashed within 30s after deploy" >&2
  journalctl -u vpnbot -n 50 --no-pager >&2
  exit 1
fi
restarts_after=$(systemctl show vpnbot -p NRestarts --value 2>/dev/null || echo 0)
if [ "$restarts_after" -gt "$restarts_before" ]; then
  echo "[deploy-bot] vpnbot restarted during 30s window (NRestarts $restarts_before → $restarts_after) — crashloop suspected" >&2
  journalctl -u vpnbot -n 50 --no-pager >&2
  exit 1
fi

echo "[deploy-bot] vpnbot healthy OK"

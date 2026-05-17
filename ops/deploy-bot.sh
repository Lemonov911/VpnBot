#!/bin/bash
# Pulls latest main and restarts vpnbot. Invoked from CI via ssh deploy@host
# (forced-command runs this script under sudo).
# Owner: root, mode 750. Only deploy can sudo it (/etc/sudoers.d/deploy).
set -euo pipefail

# Shared lock — bot and admin deploys touch the same /opt/vpnbot/.git.
exec 9>/var/lock/vpnbot-deploy.lock
flock -x -w 300 9 || { echo "[deploy-bot] could not acquire deploy lock in 300s" >&2; exit 1; }

cd /opt/vpnbot
echo "[deploy-bot] $(date -Is) — pulling..."
git -c safe.directory=/opt/vpnbot pull --ff-only origin main
echo "[deploy-bot] HEAD now: $(git -c safe.directory=/opt/vpnbot log --oneline -1)"
systemctl restart vpnbot

# Healthcheck — bot listens on 127.0.0.1:8080 with /api/health.
echo "[deploy-bot] healthcheck..."
ok=0
for i in $(seq 1 10); do
  if curl -fsS -m 3 -o /dev/null http://127.0.0.1:8080/api/health; then
    ok=1; break
  fi
  sleep 1
done
if [ "$ok" -eq 1 ] && systemctl is-active --quiet vpnbot; then
  echo "[deploy-bot] vpnbot healthy OK"
else
  echo "[deploy-bot] vpnbot UNHEALTHY — last 30 log lines:" >&2
  journalctl -u vpnbot -n 30 --no-pager >&2
  exit 1
fi

#!/bin/bash
# /opt/vpnctl/deploy-agent.sh
#
# Запускается как forced-command SSH-ключа deploy-юзера из GitHub Actions.
# Заменяет бинарь и перезапускает vpnctl-awg.service.
#
# Установка (один раз на 68.183.15.95):
#   install -m 750 -o root -g root deploy-agent.sh /opt/vpnctl/deploy-agent.sh
#   # В /etc/sudoers.d/deploy-agent:
#   deploy ALL=(root) NOPASSWD: /opt/vpnctl/deploy-agent.sh
#   # В ~deploy/.ssh/authorized_keys:
#   command="sudo /opt/vpnctl/deploy-agent.sh",no-pty,no-port-forwarding ssh-ed25519 AAAA...

set -euo pipefail

STAGING="/opt/vpnctl/deploy/vpnctl_awg"
TARGET="/usr/local/bin/vpnctl_awg"
SERVICE="vpnctl-awg.service"

echo "==> Deploying vpnctl agent..."

if [ ! -f "$STAGING" ]; then
    echo "ERROR: staging binary not found at $STAGING" >&2
    exit 1
fi

# Атомарная замена: mv внутри одной ФС — нет окна, когда бинарь отсутствует.
install -m 755 -o root -g root "$STAGING" "${TARGET}.new"
mv "${TARGET}.new" "$TARGET"
rm -f "$STAGING"

echo "==> Restarting $SERVICE..."
systemctl restart "$SERVICE"
sleep 1

if systemctl is-active --quiet "$SERVICE"; then
    echo "==> OK: $SERVICE is running"
    systemctl status "$SERVICE" --no-pager -l | head -20
else
    echo "ERROR: $SERVICE failed to start" >&2
    journalctl -u "$SERVICE" -n 30 --no-pager >&2
    exit 1
fi

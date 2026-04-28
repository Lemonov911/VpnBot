#!/bin/bash
# Run on the VPN server as root to set up WireGuard + vpnctl

set -e

echo "==> Installing WireGuard..."
apt-get update -q
apt-get install -y wireguard wireguard-tools iproute2

echo "==> Generating server keys..."
mkdir -p /etc/wireguard
wg genkey | tee /etc/wireguard/server_private.key | wg pubkey > /etc/wireguard/server_public.key
chmod 600 /etc/wireguard/server_private.key

SERVER_PRIVATE=$(cat /etc/wireguard/server_private.key)
SERVER_PUBLIC=$(cat /etc/wireguard/server_public.key)

echo "==> Server public key: $SERVER_PUBLIC"

echo "==> Creating wg0 config..."
cat > /etc/wireguard/wg0.conf << EOF
[Interface]
PrivateKey = $SERVER_PRIVATE
Address = 10.8.0.1/24
ListenPort = 51820
PostUp   = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
EOF

echo "==> Enabling IP forwarding..."
echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
sysctl -p

echo "==> Starting WireGuard..."
systemctl enable wg-quick@wg0
systemctl start wg-quick@wg0

echo "==> Installing vpnctl..."
mkdir -p /opt/vpnbot/agent
cp vpnctl /opt/vpnbot/agent/
cp .env /opt/vpnbot/agent/.env

cat > /etc/systemd/system/vpnctl.service << 'EOF'
[Unit]
Description=vpnctl - VPN management agent
After=network.target wg-quick@wg0.service

[Service]
Type=simple
WorkingDirectory=/opt/vpnbot/agent
EnvironmentFile=/opt/vpnbot/agent/.env
ExecStart=/opt/vpnbot/agent/vpnctl
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable vpnctl
systemctl start vpnctl

echo ""
echo "==> Done!"
echo "Server pubkey: $SERVER_PUBLIC"
echo "vpnctl status: $(systemctl is-active vpnctl)"
echo ""
echo "Test: curl -H 'X-Agent-Token: \$AGENT_TOKEN' http://127.0.0.1:9000/health"

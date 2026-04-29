#!/bin/bash
# Run on the VPN server as root to set up AmneziaWG + vpnctl

set -e

# Check if Go is installed
if ! command -v go &> /dev/null; then
    echo "==> Installing Go..."
    wget -q https://go.dev/dl/go1.24.4.linux-amd64.tar.gz -O /tmp/go.tar.gz
    rm -rf /usr/local/go
    tar -C /usr/local -xzf /tmp/go.tar.gz
    rm /tmp/go.tar.gz
fi

echo "==> Go version: $(go version)"
export PATH=$PATH:/usr/local/go/bin

# Build AmneziaWG
AMNEZIA_DIR=/root/amneziawg-go
if [ ! -d "$AMNEZIA_DIR" ]; then
    echo "==> Cloning amneziawg-go..."
    git clone https://github.com/amnezia-vpn/amneziawg-go "$AMNEZIA_DIR"
fi

# Install WireGuard tools (for wg command)
apt-get update -q
apt-get install -y wireguard-tools iproute2

# Generate server keys
echo "==> Generating server keys..."
mkdir -p /etc/wireguard
if [ ! -f /etc/wireguard/server_private.key ]; then
    cd "$AMNEZIA_DIR"
    ./amneziawg-go genkey | tee /etc/wireguard/server_private.key | ./amneziawg-go pubkey > /etc/wireguard/server_public.key
    chmod 600 /etc/wireguard/server_private.key
fi

SERVER_PRIVATE=$(cat /etc/wireguard/server_private.key)
SERVER_PRIVATE_HEX=$(echo "$SERVER_PRIVATE" | base64 -d | xxd -p | tr -d '\n')
SERVER_PUBLIC=$(cat /etc/wireguard/server_public.key)

echo "==> Server public key: $SERVER_PUBLIC"

# Enable IP forwarding
echo "==> Enabling IP forwarding..."
echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
sysctl -p

# Kill existing amneziawg-go if running
echo "==> Starting AmneziaWG (userspace)..."
pkill -f 'amneziawg-go wg0' 2>/dev/null || true
sleep 1

# Remove old interface if exists
ip link del wg0 2>/dev/null || true

# Ensure directories for UAPI sockets exist
mkdir -p /run/amneziawg
mkdir -p /var/run/wireguard

# Start amneziawg-go in userspace mode
cd "$AMNEZIA_DIR"
nohup ./amneziawg-go wg0 </dev/null >/var/log/amneziawg.log 2>&1 &

# Wait for interface
echo "==> Waiting for wg0 interface..."
for i in {1..15}; do
    if ip link show wg0 &>/dev/null; then
        echo "==> wg0 interface ready"
        break
    fi
    sleep 1
done

# Configure interface
echo "==> Configuring wg0..."
ip addr add 10.8.0.1/24 dev wg0
ip link set wg0 up
sleep 1

# Set private key and listen port via UAPI
echo "set=1
private_key=$SERVER_PRIVATE_HEX
listen_port=51820

" | nc -w2 -U /run/amneziawg/wg0.sock

# Create symlink for wgctrl compatibility
ln -sf /run/amneziawg/wg0.sock /var/run/wireguard/wg0.sock

# NAT rules
iptables -A FORWARD -i wg0 -j ACCEPT
iptables -A FORWARD -o wg0 -j ACCEPT
iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE

# Firewall
if command -v ufw &>/dev/null; then
    ufw allow 51820/udp
fi

# Create systemd service for amneziawg-go
cat > /etc/systemd/system/amneziawg.service << 'EOF'
[Unit]
Description=AmneziaWG userspace daemon
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/amneziawg-go
ExecStartPre=/usr/bin/pkill -f 'amneziawg-go wg0' 2>/dev/null || true
ExecStart=/root/amneziawg-go/amneziawg-go wg0
Restart=on-failure
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Create vpnctl systemd service
echo "==> Installing vpnctl..."
mkdir -p /opt/vpnbot/agent
cp vpnctl /opt/vpnbot/agent/
cp .env /opt/vpnbot/agent/.env

cat > /etc/systemd/system/vpnctl.service << 'EOF'
[Unit]
Description=vpnctl - VPN management agent
After=network.target amneziawg.service

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
systemctl enable amneziawg
systemctl enable vpnctl
systemctl restart amneziawg
sleep 2
systemctl restart vpnctl
sleep 2

echo ""
echo "==> Done!"
echo "Server pubkey: $SERVER_PUBLIC"
echo "vpnctl status: $(systemctl is-active vpnctl)"
echo ""
echo "Test: curl -H 'X-Agent-Token: \$AGENT_TOKEN' http://127.0.0.1:9000/health"
echo ""

# Check status
echo "==> Checking AmneziaWG..."
echo -e "get=1\n\n" | nc -w2 -U /run/amneziawg/wg0.sock
ip link show wg0

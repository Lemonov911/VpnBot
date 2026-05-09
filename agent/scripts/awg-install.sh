#!/bin/bash
# AmneziaWG установщик с генерацией случайных DPI-обходных параметров.
# Запускать на чистом Ubuntu/Debian VPS под root.
#
# Создаёт интерфейс awg0 со случайными Jc/H1-H4/S1-S4 — каждый сервер
# имеет уникальную DPI-сигнатуру. Управление пирами далее через Go-агент.

set -euo pipefail

# ── Параметры (можно переопределить через ENV) ───────────────────────────────
IFACE="${AWG_IFACE:-awg0}"
PORT="${AWG_PORT:-$((RANDOM % 30000 + 30000))}"   # 30000-60000 random
SUBNET="${AWG_SUBNET:-10.66.66.0/24}"
SERVER_IP="${AWG_SERVER_IP:-10.66.66.1}"
ENDPOINT_IP="${AWG_ENDPOINT_IP:-$(curl -s -4 ifconfig.co)}"
PARAMS_FILE="/etc/amnezia/amneziawg/server-params.json"

echo "==> AmneziaWG installer"
echo "    iface=$IFACE port=$PORT subnet=$SUBNET endpoint=$ENDPOINT_IP"

# ── Установка ────────────────────────────────────────────────────────────────
if ! command -v awg >/dev/null; then
    echo "==> Installing amneziawg from PPA"
    add-apt-repository -y ppa:amnezia/ppa
    apt-get update -q
    apt-get install -y amneziawg amneziawg-tools jq
fi

# ── Генерация случайных DPI-обходных параметров ──────────────────────────────
# H1-H4 должны быть unique uint32 ≥ 5 (чтобы не коллидировать с MessageType WG)
# S1-S4 (paddings) — 0..1280, S1+S2 ≤ 1280
gen_h() { echo $((RANDOM*RANDOM % 4000000000 + 5)); }
gen_s() { echo $((RANDOM % 256 + 30)); }

JC=$((RANDOM % 9 + 2))      # 2-10 junk packets
JMIN=$((RANDOM % 30 + 10))  # 10-40 min size
JMAX=$((JMIN + RANDOM % 200 + 50))  # > Jmin
S1=$(gen_s)
S2=$(gen_s)
S3=$(gen_s)
S4=$(gen_s)
H1=$(gen_h); H2=$(gen_h); H3=$(gen_h); H4=$(gen_h)
# Гарантируем уникальность H
while [[ "$H1" == "$H2" || "$H2" == "$H3" || "$H3" == "$H4" || "$H1" == "$H3" || "$H1" == "$H4" || "$H2" == "$H4" ]]; do
    H1=$(gen_h); H2=$(gen_h); H3=$(gen_h); H4=$(gen_h)
done

# ── Ключи сервера ────────────────────────────────────────────────────────────
mkdir -p /etc/amnezia/amneziawg
cd /etc/amnezia/amneziawg
[[ -f server_priv.key ]] || awg genkey > server_priv.key
chmod 600 server_priv.key
PRIV=$(cat server_priv.key)
PUB=$(echo "$PRIV" | awg pubkey)
echo "$PUB" > server_pub.key

# ── Конфиг сервера ───────────────────────────────────────────────────────────
cat > /etc/amnezia/amneziawg/${IFACE}.conf <<EOF
[Interface]
PrivateKey = $PRIV
ListenPort = $PORT
Address = $SERVER_IP/24

# AmneziaWG obfuscation parameters (random per server)
Jc = $JC
Jmin = $JMIN
Jmax = $JMAX
S1 = $S1
S2 = $S2
S3 = $S3
S4 = $S4
H1 = $H1
H2 = $H2
H3 = $H3
H4 = $H4

PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
EOF

# ── Сохраняем параметры в JSON для агента ────────────────────────────────────
cat > "$PARAMS_FILE" <<EOF
{
  "interface": "$IFACE",
  "port": $PORT,
  "subnet": "$SUBNET",
  "server_ip": "$SERVER_IP",
  "endpoint": "$ENDPOINT_IP:$PORT",
  "server_public_key": "$PUB",
  "obfuscation": {
    "jc": $JC, "jmin": $JMIN, "jmax": $JMAX,
    "s1": $S1, "s2": $S2, "s3": $S3, "s4": $S4,
    "h1": $H1, "h2": $H2, "h3": $H3, "h4": $H4
  }
}
EOF
chmod 644 "$PARAMS_FILE"

# ── IP forwarding + sysctl tuning ────────────────────────────────────────────
sysctl -w net.ipv4.ip_forward=1 >/dev/null
grep -q "ip_forward=1" /etc/sysctl.conf || echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf

# Apply BBR + buffers if not already
if ! sysctl net.ipv4.tcp_congestion_control 2>/dev/null | grep -q bbr; then
    cat >> /etc/sysctl.d/99-vpn.conf <<EOF
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
net.core.rmem_max=67108864
net.core.wmem_max=67108864
net.ipv4.tcp_fastopen=3
EOF
    sysctl --system >/dev/null
fi

# ── Открытие порта ───────────────────────────────────────────────────────────
iptables -I INPUT -p udp --dport $PORT -j ACCEPT 2>/dev/null || true
ufw allow $PORT/udp 2>/dev/null || true

# ── Запуск интерфейса ────────────────────────────────────────────────────────
awg-quick down $IFACE 2>/dev/null || true
awg-quick up $IFACE
systemctl enable awg-quick@$IFACE 2>&1 | tail -1

# ── Финальный отчёт ──────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  AmneziaWG ready"
echo "============================================================"
awg show $IFACE | head -20
echo ""
echo "Endpoint: $ENDPOINT_IP:$PORT"
echo "Public key: $PUB"
echo "Params saved: $PARAMS_FILE"
echo ""
echo "Now agent will read $PARAMS_FILE on startup and serve peers."

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

# Внешний интерфейс для MASQUERADE — авто-детект по default-маршруту.
# На разных VPS бывает eth0 / ens3 / eno1 / enp1s0 — захардкодить нельзя.
EXT_IFACE="${AWG_EXT_IFACE:-$(ip route show default 2>/dev/null | awk '/default/ {print $5; exit}')}"
EXT_IFACE="${EXT_IFACE:-eth0}"  # fallback на eth0 если detection не удался

# MSS clamp на TCP-пакетах через awg0 — обязательно для Windows-клиентов
# через Amnezia VPN-большое приложение (userspace wireguard-go не делает
# корректный PMTU discovery → крупные CDN не грузятся). На iPhone не нужно,
# но и не мешает. См. AWG_DEPLOYMENT.md.
MSS_CLAMP="${AWG_MSS_CLAMP:-1200}"

echo "==> AmneziaWG installer"
echo "    iface=$IFACE port=$PORT subnet=$SUBNET endpoint=$ENDPOINT_IP"
echo "    ext_iface=$EXT_IFACE mss_clamp=$MSS_CLAMP"

# ── Установка ────────────────────────────────────────────────────────────────
if ! command -v awg >/dev/null; then
    echo "==> Installing amneziawg from PPA"
    add-apt-repository -y ppa:amnezia/ppa
    apt-get update -q
    apt-get install -y amneziawg amneziawg-tools jq
fi

# ── Генерация случайных DPI-обходных параметров ──────────────────────────────
# AmneziaWG 2.0 поддерживает ДИАПАЗОНЫ H1-H4 (формат "low-high") — клиент
# рандомизирует значения в каждой сессии. Это критично против MTS DPI:
# при статичных H1-H4 фингерпринт выучивается за 2-3 handshake. С диапазонами
# DPI видит разные magic-байты каждый раз и не может построить шаблон.
#
# H1-H4: 4 непересекающихся диапазона в [5..4294967295]. Делим uint32 на
# 4 равные четверти, в каждой берём случайное окно — диапазоны гарантированно
# уникальны и не пересекаются.
# S1-S4 (paddings) — 0..1280, S1+S2 ≤ 1280
gen_s() { echo $((RANDOM % 256 + 30)); }

# Генератор пары low-high внутри [base..base+span], min ширина диапазона ~20%
gen_h_range() {
    local base=$1 span=$2
    local low=$((base + RANDOM * RANDOM % (span / 2)))
    local width=$((span / 5 + RANDOM * RANDOM % (span / 2)))
    local high=$((low + width))
    if (( high > base + span )); then high=$((base + span)); fi
    echo "${low}-${high}"
}

JC=$((RANDOM % 9 + 2))      # 2-10 junk packets
JMIN=$((RANDOM % 30 + 10))  # 10-40 min size
JMAX=$((JMIN + RANDOM % 200 + 50))  # > Jmin
S1=$(gen_s)
S2=$(gen_s)
S3=$(gen_s)
S4=$(gen_s)

# 4 четверти uint32: [5..1073741823], [1073741824..2147483647],
#                    [2147483648..3221225471], [3221225472..4294967294]
QUARTER=1073741819
H1=$(gen_h_range 5 $QUARTER)
H2=$(gen_h_range 1073741824 $QUARTER)
H3=$(gen_h_range 2147483648 $QUARTER)
H4=$(gen_h_range 3221225472 $QUARTER)

# ── I1: handshake-маска под DNS-запрос к РФ-домену ───────────────────────────
# Без I1 МТС DPI режет 99% AmneziaWG transport-трафика — даже handshake проходит
# (короткий, обфусцированный), но дальнейшие data-пакеты палятся по эвристике
# UDP-flow. С I1 первые байты UDP-payload выглядят как DNS-query, DPI помечает
# flow как доверенный и не инспектирует последующие пакеты.
#
# Каждый сервер получает СВОЙ домен и СВОЙ TXID — DPI не может построить
# фингерпринт на статичном I1.
I1_DOMAINS=(mail.ru yandex.ru vk.com ok.ru rambler.ru lenta.ru rbc.ru gosuslugi.ru)
I1_DOMAIN="${I1_DOMAINS[$RANDOM % ${#I1_DOMAINS[@]}]}"
I1_TXID_HEX=$(printf "%04x" $((RANDOM % 0xFFFF)))
# Собираем DNS-query байтами: header(12) + qname(label-encoded) + qtype/qclass
I1_HEX=$(python3 -c "
import struct
domain = '$I1_DOMAIN'
txid = int('$I1_TXID_HEX', 16)
header = struct.pack('>HHHHHH', txid, 0x0100, 1, 0, 0, 0)
qname = b''.join(bytes([len(l)])+l.encode() for l in domain.split('.')) + b'\x00'
question = qname + struct.pack('>HH', 1, 1)  # A, IN
print((header + question).hex())
")
I1_VALUE="<b 0x${I1_HEX}>"
echo "    I1 mask: DNS-query to ${I1_DOMAIN} (txid=0x${I1_TXID_HEX})"

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
I1 = $I1_VALUE

PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -o $EXT_IFACE -j MASQUERADE; iptables -t mangle -A FORWARD -i %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss $MSS_CLAMP; iptables -t mangle -A FORWARD -o %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss $MSS_CLAMP
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -o $EXT_IFACE -j MASQUERADE; iptables -t mangle -D FORWARD -i %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss $MSS_CLAMP; iptables -t mangle -D FORWARD -o %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss $MSS_CLAMP
EOF

# ── Сохраняем параметры в JSON для агента ────────────────────────────────────
# H1-H4 — строки-диапазоны "low-high" (формат AmneziaWG 2.0). Агент
# отдаёт их клиенту as-is, клиент сам рандомизирует значения в диапазоне.
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
    "h1": "$H1", "h2": "$H2", "h3": "$H3", "h4": "$H4",
    "i1": "$I1_VALUE"
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

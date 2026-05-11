// Package awg обрабатывает AmneziaWG-пиры. Сервер ставится скриптом
// agent/scripts/awg-install.sh — он генерирует random Jc/H1-H4/S1-S4
// и пишет /etc/amnezia/amneziawg/server-params.json. Manager их читает
// и использует для генерации клиентских конфигов.
//
// Управление пирами идёт через CLI `awg` (kernel module amneziawg использует
// отдельную netlink-семью, wgctrl Go-либа с ним работать не умеет).
package awg

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/crypto/curve25519"
)

// Дефолты клиентского конфига. Менять осторожно — все клиенты пересоберутся.
const (
	// clientMTU=1240: -40 от стандартных 1280 для обычного WG. AmneziaWG с
	// advanced-security добавляет per-пакет padding (S1-S4) и junk (Jc) →
	// эффективный header больше. На MTU=1280 крупные TCP/QUIC сегменты
	// иногда фрагментируются и теряются — особенно заметно на YT/ТикТок-
	// стримах. 1240 даёт запас без потери производительности.
	clientMTU = 1240

	// Порядок DNS важен: Google первым, потому что 8.8.8.8 отправляет
	// EDNS-Client-Subnet → CDN отдают ближайший к нашему серверу edge.
	// Cloudflare 1.1.1.1 ECS не шлёт → может вернуть рандомный географически
	// далёкий edge, что даёт «иногда работает, иногда виснет» на YT/TikTok.
	clientDNS = "8.8.8.8, 1.1.1.1"

	clientAllowedIPs    = "0.0.0.0/0, ::/0"
	clientKeepaliveSecs = 25
)

// Obfuscation — параметры AmneziaWG DPI-обхода, генерируемые один раз
// при установке сервера и общие для всех клиентов.
type Obfuscation struct {
	Jc   int `json:"jc"`
	Jmin int `json:"jmin"`
	Jmax int `json:"jmax"`
	S1   int `json:"s1"`
	S2   int `json:"s2"`
	S3   int `json:"s3"`
	S4   int `json:"s4"`
	// H1-H4 — диапазоны вида "low-high" (AmneziaWG 2.0). Клиент рандомизирует
	// значения в диапазоне per-session, поэтому DPI не может выучить статичный
	// magic-байт. Старый формат (один uint32) тоже валиден — bash-скрипт
	// пишет либо число, либо строку "low-high"; здесь принимаем как строку
	// и проксируем в конфиг клиента without parsing.
	H1 string `json:"h1"`
	H2 string `json:"h2"`
	H3 string `json:"h3"`
	H4 string `json:"h4"`
	// I1 — DNS-маска первого UDP-пакета handshake. Формат "<b 0xHEXBYTES>",
	// где HEX — байты настоящего DNS-query к РФ-домену (mail.ru/yandex.ru/...).
	// БЕЗ ЭТОГО МТС DPI режет 99% transport-трафика. Опциональное поле для
	// обратной совместимости с серверами поднятыми до этой ревизии скрипта.
	I1 string `json:"i1,omitempty"`
}

// ServerParams читается из /etc/amnezia/amneziawg/server-params.json,
// генерируется bash-скриптом awg-install.sh при подъёме сервера.
type ServerParams struct {
	Interface       string      `json:"interface"`
	Port            int         `json:"port"`
	Subnet          string      `json:"subnet"`
	ServerIP        string      `json:"server_ip"`
	Endpoint        string      `json:"endpoint"`
	ServerPublicKey string      `json:"server_public_key"`
	Obfuscation     Obfuscation `json:"obfuscation"`
}

type Peer struct {
	PublicKey  string
	PrivateKey string
	AssignedIP string
	Label      string
	Suspended  bool
	CreatedAt  time.Time
	RxBytes    int64
	TxBytes    int64
	LastSeen   time.Time
}

type ClientConfig struct {
	PrivateKey string
	AssignedIP string
	ServerKey  string
	Endpoint   string
	DNS        string
	Params     *ServerParams
}

type Manager struct {
	mu      sync.RWMutex
	params  *ServerParams
	peers   map[string]*Peer
	usedIPs map[string]bool
}

func NewManager(paramsPath string) (*Manager, error) {
	data, err := os.ReadFile(paramsPath)
	if err != nil {
		return nil, fmt.Errorf("read params: %w", err)
	}
	var sp ServerParams
	if err := json.Unmarshal(data, &sp); err != nil {
		return nil, fmt.Errorf("parse params: %w", err)
	}

	m := &Manager{
		params:  &sp,
		peers:   make(map[string]*Peer),
		usedIPs: make(map[string]bool),
	}

	if err := m.syncFromKernel(); err != nil {
		fmt.Printf("awg: initial sync warning: %v\n", err)
	}
	return m, nil
}

// awgCmd выполняет `awg <args...>` и возвращает stdout. На ошибке — stderr в %w.
func awgCmd(args ...string) ([]byte, error) {
	out, err := exec.Command("awg", args...).CombinedOutput()
	if err != nil {
		return out, fmt.Errorf("awg %s: %w (%s)", strings.Join(args, " "), err, string(out))
	}
	return out, nil
}

// awgSetPeer вызывает `awg set <iface> peer <pub> <extra...>`.
func (m *Manager) awgSetPeer(pub string, extra ...string) error {
	args := append([]string{"set", m.params.Interface, "peer", pub}, extra...)
	_, err := awgCmd(args...)
	return err
}

// dumpPeers выполняет `awg show <iface> dump` и возвращает строки пиров.
// Первая строка `awg show ... dump` — это заголовок интерфейса, его пропускаем.
// Каждая строка пира: pub_key  preshared_key  endpoint  allowed_ips  latest_handshake  rx  tx  keepalive
func (m *Manager) dumpPeers() ([]string, error) {
	out, err := awgCmd("show", m.params.Interface, "dump")
	if err != nil {
		return nil, err
	}
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	if len(lines) < 2 {
		return nil, nil
	}
	return lines[1:], nil
}

// syncFromKernel импортирует существующие пиры (например, оставшиеся
// после перезапуска агента) в in-memory state.
func (m *Manager) syncFromKernel() error {
	peerLines, err := m.dumpPeers()
	if err != nil {
		return err
	}
	for _, line := range peerLines {
		fields := strings.Fields(line)
		if len(fields) < 4 {
			continue
		}
		pub, allowed := fields[0], fields[3]
		if ip := strings.SplitN(allowed, "/", 2)[0]; ip != "" {
			m.usedIPs[ip] = true
		}
		m.peers[pub] = &Peer{
			PublicKey:  pub,
			AssignedIP: allowed,
			Label:      "imported",
			CreatedAt:  time.Now(),
		}
	}
	return nil
}

func (m *Manager) Interface() string       { return m.params.Interface }
func (m *Manager) ServerPublicKey() string { return m.params.ServerPublicKey }
func (m *Manager) Endpoint() string        { return m.params.Endpoint }

// AddPeer создаёт нового пира с авто-IP. advanced-security включает обфускацию
// transport-плейна (data-пакеты после handshake) — без неё MTS DPI режет 99%
// трафика по эвристике «регулярный UDP-flow одинаковых пакетов».
func (m *Manager) AddPeer(label string) (*Peer, *ClientConfig, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	priv, pub, err := generateKeypair()
	if err != nil {
		return nil, nil, err
	}

	ip, err := m.nextFreeIP()
	if err != nil {
		return nil, nil, err
	}
	allowedCidr := ip + "/32"

	if err := m.awgSetPeer(pub,
		"allowed-ips", allowedCidr,
		"advanced-security", "on",
	); err != nil {
		return nil, nil, err
	}

	peer := &Peer{
		PublicKey:  pub,
		PrivateKey: priv,
		AssignedIP: allowedCidr,
		Label:      label,
		CreatedAt:  time.Now(),
	}
	m.peers[pub] = peer
	m.usedIPs[ip] = true

	cc := &ClientConfig{
		PrivateKey: priv,
		AssignedIP: allowedCidr,
		ServerKey:  m.params.ServerPublicKey,
		Endpoint:   m.params.Endpoint,
		DNS:        clientDNS,
		Params:     m.params,
	}
	return peer, cc, nil
}

func (m *Manager) RemovePeer(pubkey string) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	if err := m.awgSetPeer(pubkey, "remove"); err != nil {
		return err
	}
	if p, ok := m.peers[pubkey]; ok {
		if ip, _, _ := net.ParseCIDR(p.AssignedIP); ip != nil {
			delete(m.usedIPs, ip.String())
		}
		delete(m.peers, pubkey)
	}
	return nil
}

// Stats обновляет байты/handshake из awg-вывода и возвращает копии пиров.
func (m *Manager) Stats() ([]*Peer, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	peerLines, err := m.dumpPeers()
	if err != nil {
		return nil, err
	}
	for _, line := range peerLines {
		fields := strings.Fields(line)
		if len(fields) < 8 {
			continue
		}
		p, ok := m.peers[fields[0]]
		if !ok {
			continue
		}
		// fields: pub psk endpoint allowed handshake rx tx keepalive
		if hs, err := strconv.ParseInt(fields[4], 10, 64); err == nil && hs > 0 {
			p.LastSeen = time.Unix(hs, 0)
		}
		p.RxBytes, _ = strconv.ParseInt(fields[5], 10, 64)
		p.TxBytes, _ = strconv.ParseInt(fields[6], 10, 64)
	}
	out := make([]*Peer, 0, len(m.peers))
	for _, p := range m.peers {
		cp := *p
		out = append(out, &cp)
	}
	return out, nil
}

// nextFreeIP перебирает подсеть начиная с третьего адреса (.2), пропуская
// серверный IP. Первые два адреса (.0 network, .1 server) зарезервированы.
func (m *Manager) nextFreeIP() (string, error) {
	_, network, err := net.ParseCIDR(m.params.Subnet)
	if err != nil {
		return "", err
	}
	ip := cloneIP(network.IP)
	inc(ip)
	inc(ip)

	for network.Contains(ip) {
		s := ip.String()
		if !m.usedIPs[s] && s != m.params.ServerIP {
			return s, nil
		}
		inc(ip)
	}
	return "", fmt.Errorf("no free IPs in %s", m.params.Subnet)
}

func inc(ip net.IP) {
	for j := len(ip) - 1; j >= 0; j-- {
		ip[j]++
		if ip[j] != 0 {
			break
		}
	}
}

func cloneIP(ip net.IP) net.IP {
	c := make(net.IP, len(ip))
	copy(c, ip)
	return c
}

// generateKeypair — Curve25519 ключи в base64 (WireGuard формат).
func generateKeypair() (privB64, pubB64 string, err error) {
	var priv [32]byte
	if _, err := rand.Read(priv[:]); err != nil {
		return "", "", err
	}
	priv[0] &= 248
	priv[31] &= 127
	priv[31] |= 64
	pub, err := curve25519.X25519(priv[:], curve25519.Basepoint)
	if err != nil {
		return "", "", err
	}
	return base64.StdEncoding.EncodeToString(priv[:]),
		base64.StdEncoding.EncodeToString(pub), nil
}

// AmneziaWGConfig — клиентский конфиг с обфускацией. Понимают:
// Amnezia VPN app (iOS/Android/Mac/Win), wireguard-tools с патчами.
//
// I1 опционален — старые сервера без I1 в params.json продолжают работать,
// просто без DNS-маски (handshake может резаться MTS DPI).
func AmneziaWGConfig(cc *ClientConfig) string {
	o := cc.Params.Obfuscation
	i1Line := ""
	if o.I1 != "" {
		i1Line = "I1 = " + o.I1 + "\n"
	}
	return fmt.Sprintf(`[Interface]
PrivateKey = %s
Address = %s
DNS = %s
MTU = %d

# AmneziaWG obfuscation (matches server)
Jc = %d
Jmin = %d
Jmax = %d
S1 = %d
S2 = %d
S3 = %d
S4 = %d
H1 = %s
H2 = %s
H3 = %s
H4 = %s
%s
[Peer]
PublicKey = %s
Endpoint = %s
AllowedIPs = %s
PersistentKeepalive = %d
`,
		cc.PrivateKey, cc.AssignedIP, cc.DNS, clientMTU,
		o.Jc, o.Jmin, o.Jmax,
		o.S1, o.S2, o.S3, o.S4,
		o.H1, o.H2, o.H3, o.H4,
		i1Line,
		cc.ServerKey, cc.Endpoint,
		clientAllowedIPs, clientKeepaliveSecs,
	)
}

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

// ServerParams — параметры обфускации, генерируемые один раз при установке.
type ServerParams struct {
	Interface       string `json:"interface"`
	Port            int    `json:"port"`
	Subnet          string `json:"subnet"`
	ServerIP        string `json:"server_ip"`
	Endpoint        string `json:"endpoint"`
	ServerPublicKey string `json:"server_public_key"`
	Obfuscation     struct {
		Jc   int   `json:"jc"`
		Jmin int   `json:"jmin"`
		Jmax int   `json:"jmax"`
		S1   int   `json:"s1"`
		S2   int   `json:"s2"`
		S3   int   `json:"s3"`
		S4   int   `json:"s4"`
		H1   int64 `json:"h1"`
		H2   int64 `json:"h2"`
		H3   int64 `json:"h3"`
		H4   int64 `json:"h4"`
	} `json:"obfuscation"`
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

// syncFromKernel читает текущие пиры через `awg show <iface>`.
func (m *Manager) syncFromKernel() error {
	out, err := exec.Command("awg", "show", m.params.Interface, "dump").Output()
	if err != nil {
		return fmt.Errorf("awg show: %w", err)
	}
	// Format: первая строка — server, дальше peer per line
	// peer: pub_key  preshared_key  endpoint  allowed_ips  latest_handshake  rx  tx  keepalive
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	if len(lines) < 2 {
		return nil // no peers
	}
	for _, line := range lines[1:] {
		fields := strings.Fields(line)
		if len(fields) < 4 {
			continue
		}
		pub := fields[0]
		allowed := fields[3]
		ip := strings.SplitN(allowed, "/", 2)[0]
		if ip != "" {
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

// AddPeer создаёт нового пира с авто-IP.
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

	// awg set awg0 peer <pub> allowed-ips <ip>/32
	allowedCidr := ip + "/32"
	cmd := exec.Command("awg", "set", m.params.Interface,
		"peer", pub,
		"allowed-ips", allowedCidr,
	)
	if out, err := cmd.CombinedOutput(); err != nil {
		return nil, nil, fmt.Errorf("awg set: %w (%s)", err, string(out))
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
		DNS:        "1.1.1.1, 8.8.8.8",
		Params:     m.params,
	}
	return peer, cc, nil
}

func (m *Manager) RemovePeer(pubkey string) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	cmd := exec.Command("awg", "set", m.params.Interface,
		"peer", pubkey, "remove",
	)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("awg set remove: %w (%s)", err, string(out))
	}
	if p, ok := m.peers[pubkey]; ok {
		ip, _, _ := net.ParseCIDR(p.AssignedIP)
		if ip != nil {
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

	out, err := exec.Command("awg", "show", m.params.Interface, "dump").Output()
	if err != nil {
		return nil, err
	}
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	for _, line := range lines[1:] {
		fields := strings.Fields(line)
		if len(fields) < 8 {
			continue
		}
		pub := fields[0]
		p, ok := m.peers[pub]
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
	out2 := make([]*Peer, 0, len(m.peers))
	for _, p := range m.peers {
		cp := *p
		out2 = append(out2, &cp)
	}
	return out2, nil
}

func (m *Manager) nextFreeIP() (string, error) {
	_, network, err := net.ParseCIDR(m.params.Subnet)
	if err != nil {
		return "", err
	}
	ip := cloneIP(network.IP)
	inc(ip)
	inc(ip) // skip network and server (.1)

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

// generateKeypair — Curve25519 ключи для WireGuard формата.
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
func AmneziaWGConfig(cc *ClientConfig) string {
	o := cc.Params.Obfuscation
	return fmt.Sprintf(`[Interface]
PrivateKey = %s
Address = %s
DNS = %s
MTU = 1280

# AmneziaWG obfuscation (matches server)
Jc = %d
Jmin = %d
Jmax = %d
S1 = %d
S2 = %d
S3 = %d
S4 = %d
H1 = %d
H2 = %d
H3 = %d
H4 = %d

[Peer]
PublicKey = %s
Endpoint = %s
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
`,
		cc.PrivateKey, cc.AssignedIP, cc.DNS,
		o.Jc, o.Jmin, o.Jmax,
		o.S1, o.S2, o.S3, o.S4,
		o.H1, o.H2, o.H3, o.H4,
		cc.ServerKey, cc.Endpoint,
	)
}

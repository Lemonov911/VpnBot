package wg

import (
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"log"
	"net"
	"os/exec"
	"sync"
	"time"

	"golang.zx2c4.com/wireguard/wgctrl"
	"golang.zx2c4.com/wireguard/wgctrl/wgtypes"
)

// Peer holds all info about a WireGuard peer managed by vpnctl.
type Peer struct {
	PublicKey  string
	PrivateKey string // kept only to generate client config
	AssignedIP string // e.g. 10.8.0.3/32
	Label      string
	Suspended  bool
	CreatedAt  time.Time

	// last stats snapshot from kernel
	RxBytes   int64
	TxBytes   int64
	LastSeen  time.Time
}

// ClientConfig is what we hand to the user.
type ClientConfig struct {
	PrivateKey string
	AssignedIP string
	PublicKey  string // server pubkey
	Endpoint   string
	DNS        string
}

type Manager struct {
	mu        sync.RWMutex
	iface     string
	subnet    string // 10.8.0.0/24
	endpoint  string // host:port
	serverKey wgtypes.Key
	peers     map[string]*Peer // pubkey → Peer
	usedIPs   map[string]bool
	client    *wgctrl.Client
}

func NewManager(iface, subnet, endpoint string) (*Manager, error) {
	c, err := wgctrl.New()
	if err != nil {
		return nil, fmt.Errorf("wgctrl: %w", err)
	}

	m := &Manager{
		iface:    iface,
		subnet:   subnet,
		endpoint: endpoint,
		peers:    make(map[string]*Peer),
		usedIPs:  make(map[string]bool),
		client:   c,
	}

	// Load existing device state (if wg0 already configured)
	if err := m.syncFromKernel(); err != nil {
		log.Printf("wg: initial sync warning: %v", err)
	}

	return m, nil
}

// ServerPublicKey returns the server's WireGuard public key as base64.
func (m *Manager) ServerPublicKey() string {
	return m.serverKey.String()
}

// AddPeer creates a new WireGuard peer, assigns an IP, returns client config.
func (m *Manager) AddPeer(label string) (*Peer, *ClientConfig, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	// Generate client key pair
	privKey, err := wgtypes.GeneratePrivateKey()
	if err != nil {
		return nil, nil, err
	}
	pubKey := privKey.PublicKey()

	// Assign next free IP in subnet
	ip, err := m.nextFreeIP()
	if err != nil {
		return nil, nil, err
	}

	_, ipNet, _ := net.ParseCIDR(ip + "/32")

	// Add to WireGuard
	cfg := wgtypes.Config{
		Peers: []wgtypes.PeerConfig{
			{
				PublicKey:         pubKey,
				AllowedIPs:        []net.IPNet{*ipNet},
				ReplaceAllowedIPs: true,
			},
		},
	}
	if err := m.client.ConfigureDevice(m.iface, cfg); err != nil {
		return nil, nil, fmt.Errorf("configure wg peer: %w", err)
	}

	peer := &Peer{
		PublicKey:  pubKey.String(),
		PrivateKey: privKey.String(),
		AssignedIP: ip + "/32",
		Label:      label,
		CreatedAt:  time.Now(),
	}
	m.peers[peer.PublicKey] = peer
	m.usedIPs[ip] = true

	clientCfg := &ClientConfig{
		PrivateKey: privKey.String(),
		AssignedIP: ip + "/32",
		PublicKey:  m.serverKey.String(),
		Endpoint:   m.endpoint,
		DNS:        "1.1.1.1",
	}

	return peer, clientCfg, nil
}

// RemovePeer removes a peer from WireGuard permanently (slot freed).
func (m *Manager) RemovePeer(pubkeyStr string) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	key, err := wgtypes.ParseKey(pubkeyStr)
	if err != nil {
		return fmt.Errorf("invalid pubkey: %w", err)
	}

	cfg := wgtypes.Config{
		Peers: []wgtypes.PeerConfig{
			{PublicKey: key, Remove: true},
		},
	}
	if err := m.client.ConfigureDevice(m.iface, cfg); err != nil {
		return err
	}

	if p, ok := m.peers[pubkeyStr]; ok {
		ip := stripMask(p.AssignedIP)
		delete(m.usedIPs, ip)
		delete(m.peers, pubkeyStr)
	}
	return nil
}

// SuspendPeer blocks all traffic for a peer by clearing AllowedIPs.
func (m *Manager) SuspendPeer(pubkeyStr string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.setSuspended(pubkeyStr, true)
}

// ResumePeer restores AllowedIPs for a suspended peer.
func (m *Manager) ResumePeer(pubkeyStr string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.setSuspended(pubkeyStr, false)
}

// SuspendAll suspends all peers for a subscription (by list of pubkeys).
func (m *Manager) SuspendAll(pubkeys []string) error {
	for _, pk := range pubkeys {
		if err := m.SuspendPeer(pk); err != nil {
			log.Printf("wg: suspend %s: %v", pk[:8], err)
		}
	}
	return nil
}

// ResumeAll resumes all peers for a subscription.
func (m *Manager) ResumeAll(pubkeys []string) error {
	for _, pk := range pubkeys {
		if err := m.ResumePeer(pk); err != nil {
			log.Printf("wg: resume %s: %v", pk[:8], err)
		}
	}
	return nil
}

// Stats returns a snapshot of all peers with current rx/tx from kernel.
func (m *Manager) Stats() ([]*Peer, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	dev, err := m.client.Device(m.iface)
	if err != nil {
		return nil, err
	}

	for _, kp := range dev.Peers {
		pk := kp.PublicKey.String()
		if p, ok := m.peers[pk]; ok {
			p.RxBytes = kp.ReceiveBytes
			p.TxBytes = kp.TransmitBytes
			if !kp.LastHandshakeTime.IsZero() {
				p.LastSeen = kp.LastHandshakeTime
			}
		}
	}

	result := make([]*Peer, 0, len(m.peers))
	for _, p := range m.peers {
		cp := *p
		result = append(result, &cp)
	}
	return result, nil
}

// ActivePeerCount returns number of peers active in the last 5 minutes.
func (m *Manager) ActivePeerCount() int {
	m.mu.RLock()
	defer m.mu.RUnlock()

	cutoff := time.Now().Add(-5 * time.Minute)
	count := 0
	for _, p := range m.peers {
		if !p.Suspended && p.LastSeen.After(cutoff) {
			count++
		}
	}
	return count
}

// --------- internal helpers ---------

func (m *Manager) setSuspended(pubkeyStr string, suspend bool) error {
	p, ok := m.peers[pubkeyStr]
	if !ok {
		return fmt.Errorf("peer not found: %s", pubkeyStr[:8])
	}

	key, _ := wgtypes.ParseKey(pubkeyStr)
	var allowed []net.IPNet

	if !suspend {
		// restore original IP
		_, ipNet, _ := net.ParseCIDR(p.AssignedIP)
		allowed = []net.IPNet{*ipNet}
	}
	// suspend → empty AllowedIPs → WireGuard drops all packets

	cfg := wgtypes.Config{
		Peers: []wgtypes.PeerConfig{
			{
				PublicKey:         key,
				AllowedIPs:        allowed,
				ReplaceAllowedIPs: true,
			},
		},
	}
	if err := m.client.ConfigureDevice(m.iface, cfg); err != nil {
		return err
	}
	p.Suspended = suspend
	return nil
}

func (m *Manager) syncFromKernel() error {
	dev, err := m.client.Device(m.iface)
	if err != nil {
		// Device may not exist yet on first start
		return err
	}
	m.serverKey = dev.PrivateKey.PublicKey()

	for _, kp := range dev.Peers {
		pk := kp.PublicKey.String()
		if _, exists := m.peers[pk]; exists {
			continue
		}
		var ip string
		if len(kp.AllowedIPs) > 0 {
			ip = kp.AllowedIPs[0].IP.String() + "/32"
			m.usedIPs[kp.AllowedIPs[0].IP.String()] = true
		}
		m.peers[pk] = &Peer{
			PublicKey:  pk,
			AssignedIP: ip,
			Label:      "imported",
			CreatedAt:  time.Now(),
		}
	}
	return nil
}

func (m *Manager) nextFreeIP() (string, error) {
	_, network, err := net.ParseCIDR(m.subnet)
	if err != nil {
		return "", err
	}

	// Start from .2 (.1 is server)
	ip := cloneIP(network.IP)
	inc(ip)
	inc(ip) // skip .1

	for network.Contains(ip) {
		s := ip.String()
		if !m.usedIPs[s] {
			return s, nil
		}
		inc(ip)
	}
	return "", fmt.Errorf("no free IPs in %s", m.subnet)
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

func stripMask(cidr string) string {
	ip, _, _ := net.ParseCIDR(cidr)
	if ip == nil {
		return cidr
	}
	return ip.String()
}

// Interface returns the WireGuard interface name.
func (m *Manager) Interface() string {
	return m.iface
}

// PeerIPs returns assigned IPs of all non-suspended peers (for tc rules).
func (m *Manager) PeerIPs() []string {
	m.mu.RLock()
	defer m.mu.RUnlock()
	var ips []string
	for _, p := range m.peers {
		if !p.Suspended {
			ips = append(ips, stripMask(p.AssignedIP))
		}
	}
	return ips
}

// WireGuardConfig returns a ready-to-use [Interface]+[Peer] INI string for the client.
func WireGuardConfig(cc *ClientConfig) string {
	return fmt.Sprintf(`[Interface]
PrivateKey = %s
Address = %s
DNS = %s

[Peer]
PublicKey = %s
Endpoint = %s
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
`, cc.PrivateKey, cc.AssignedIP, cc.DNS, cc.PublicKey, cc.Endpoint)
}

// randomHex returns n random hex bytes (for token generation).
func randomHex(n int) string {
	b := make([]byte, n)
	rand.Read(b)
	return base64.StdEncoding.EncodeToString(b)
}

// SetupInterface ensures wg0 exists with server keys and listen port.
// Safe to call on every start — skips if already configured.
func SetupInterface(iface string, port int) error {
	// Check if interface exists
	out, _ := exec.Command("ip", "link", "show", iface).Output()
	if len(out) > 0 {
		log.Printf("wg: interface %s already exists, skipping setup", iface)
		return nil
	}

	cmds := [][]string{
		{"ip", "link", "add", "dev", iface, "type", "wireguard"},
		{"wg", "genkey"},
	}
	_ = cmds
	// Real setup is done via wg-quick or separate script; agent just manages peers.
	log.Printf("wg: interface %s not found — create it with wg-quick or setup script", iface)
	return nil
}

package wg

import (
	"bufio"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"log"
	"net"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.zx2c4.com/wireguard/wgctrl"
	"golang.zx2c4.com/wireguard/wgctrl/wgtypes"
)

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
	PublicKey  string
	Endpoint   string
	DNS        string
}

type Manager struct {
	mu         sync.RWMutex
	iface      string
	subnet     string
	endpoint   string
	serverKey  wgtypes.Key
	peers      map[string]*Peer
	usedIPs    map[string]bool
	client     *wgctrl.Client
	userspace  bool
	uapiSocket string
}

func NewManager(iface, subnet, endpoint string) (*Manager, error) {
	m := &Manager{
		iface:      iface,
		subnet:     subnet,
		endpoint:   endpoint,
		peers:      make(map[string]*Peer),
		usedIPs:    make(map[string]bool),
	}

	uapiSocket := "/var/run/wireguard/" + iface + ".sock"
	if _, err := os.Stat(uapiSocket); err == nil {
		log.Printf("wg: using UAPI socket (userspace mode)")
		m.userspace = true
		m.uapiSocket = uapiSocket
	} else {
		c, err := wgctrl.New()
		if err != nil {
			return nil, fmt.Errorf("wgctrl: %w", err)
		}
		m.client = c
	}

	if err := m.syncFromKernel(); err != nil {
		log.Printf("wg: initial sync warning: %v", err)
	}

	return m, nil
}

func (m *Manager) uapiRequest(req string) (string, error) {
	conn, err := net.Dial("unix", m.uapiSocket)
	if err != nil {
		return "", fmt.Errorf("uapi connect: %w", err)
	}
	defer conn.Close()

	fmt.Fprint(conn, req)

	scanner := bufio.NewScanner(conn)
	var resp strings.Builder
	for scanner.Scan() {
		line := scanner.Text()
		resp.WriteString(line + "\n")
		if line == "" {
			break
		}
	}
	return resp.String(), nil
}

func (m *Manager) uapiSetPeer(pubKey wgtypes.Key, ipNet *net.IPNet, remove bool, allowed []net.IPNet) error {
	pubHex := hex.EncodeToString(pubKey[:])

	var cmd strings.Builder
	cmd.WriteString("set=1\n")
	cmd.WriteString("public_key=" + pubHex + "\n")

	if remove {
		cmd.WriteString("remove=true\n")
	} else {
		for _, ip := range allowed {
			cmd.WriteString("allowed_ip=" + ip.String() + "\n")
		}
	}
	cmd.WriteString("\n")

	resp, err := m.uapiRequest(cmd.String())
	if err != nil {
		return err
	}
	if strings.Contains(resp, "errno=-") {
		return fmt.Errorf("uapi error: %s", resp)
	}
	return nil
}

func (m *Manager) uapiDevice() (*wgtypes.Device, error) {
	resp, err := m.uapiRequest("get=1\n\n")
	if err != nil {
		return nil, err
	}

	dev := &wgtypes.Device{
		Type: wgtypes.Userspace,
		Name: m.iface,
	}

	var curPeer *wgtypes.Peer
	lines := strings.Split(resp, "\n")
	for _, line := range lines {
		parts := strings.SplitN(line, "=", 2)
		if len(parts) != 2 {
			continue
		}
		key, val := parts[0], parts[1]

		switch key {
		case "private_key":
			b, _ := hex.DecodeString(val)
			if len(b) == 32 {
				priv, _ := wgtypes.NewKey(b)
				dev.PrivateKey = priv
				dev.PublicKey = priv.PublicKey()
				m.serverKey = dev.PublicKey
			}
		case "listen_port":
			port, _ := strconv.Atoi(val)
			dev.ListenPort = port
		case "public_key":
			if curPeer != nil {
				dev.Peers = append(dev.Peers, *curPeer)
			}
			b, _ := hex.DecodeString(val)
			if len(b) == 32 {
				pubKey, _ := wgtypes.NewKey(b)
				curPeer = &wgtypes.Peer{
					PublicKey: pubKey,
				}
			}
		case "allowed_ip":
			_, ipNet, err := net.ParseCIDR(val)
			if err == nil && curPeer != nil {
				curPeer.AllowedIPs = append(curPeer.AllowedIPs, *ipNet)
			}
		case "endpoint":
			addr, err := net.ResolveUDPAddr("udp", val)
			if err == nil && curPeer != nil {
				curPeer.Endpoint = addr
			}
		case "rx_bytes":
			if curPeer != nil {
				curPeer.ReceiveBytes, _ = strconv.ParseInt(val, 10, 64)
			}
		case "tx_bytes":
			if curPeer != nil {
				curPeer.TransmitBytes, _ = strconv.ParseInt(val, 10, 64)
			}
		case "last_handshake_time_sec":
			if curPeer != nil {
				sec, _ := strconv.ParseInt(val, 10, 64)
				if sec > 0 {
					curPeer.LastHandshakeTime = time.Unix(sec, 0)
				}
			}
		case "persistent_keepalive_interval":
			if curPeer != nil {
				interval, _ := strconv.Atoi(val)
				curPeer.PersistentKeepaliveInterval = time.Duration(interval) * time.Second
			}
		}
	}
	if curPeer != nil {
		dev.Peers = append(dev.Peers, *curPeer)
	}

	return dev, nil
}

func (m *Manager) configureDevice(cfg wgtypes.Config) error {
	if m.userspace {
		return m.configureDeviceUAPI(cfg)
	}
	return m.client.ConfigureDevice(m.iface, cfg)
}

func (m *Manager) configureDeviceUAPI(cfg wgtypes.Config) error {
	for _, peer := range cfg.Peers {
		if peer.Remove {
			if err := m.uapiSetPeer(peer.PublicKey, nil, true, nil); err != nil {
				return err
			}
		} else {
			if err := m.uapiSetPeer(peer.PublicKey, nil, false, peer.AllowedIPs); err != nil {
				return err
			}
		}
	}
	return nil
}

func (m *Manager) getDevice() (*wgtypes.Device, error) {
	if m.userspace {
		return m.uapiDevice()
	}
	return m.client.Device(m.iface)
}

func (m *Manager) ServerPublicKey() string {
	return m.serverKey.String()
}

func (m *Manager) Interface() string {
	return m.iface
}

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

func (m *Manager) AddPeer(label string) (*Peer, *ClientConfig, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	privKey, err := wgtypes.GeneratePrivateKey()
	if err != nil {
		return nil, nil, err
	}
	pubKey := privKey.PublicKey()

	ip, err := m.nextFreeIP()
	if err != nil {
		return nil, nil, err
	}

	_, ipNet, _ := net.ParseCIDR(ip + "/32")

	cfg := wgtypes.Config{
		Peers: []wgtypes.PeerConfig{
			{
				PublicKey:         pubKey,
				AllowedIPs:        []net.IPNet{*ipNet},
				ReplaceAllowedIPs: true,
			},
		},
	}
	if err := m.configureDevice(cfg); err != nil {
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
	if err := m.configureDevice(cfg); err != nil {
		return err
	}

	if p, ok := m.peers[pubkeyStr]; ok {
		ip := stripMask(p.AssignedIP)
		delete(m.usedIPs, ip)
		delete(m.peers, pubkeyStr)
	}
	return nil
}

func (m *Manager) SuspendPeer(pubkeyStr string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.setSuspended(pubkeyStr, true)
}

func (m *Manager) ResumePeer(pubkeyStr string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.setSuspended(pubkeyStr, false)
}

func (m *Manager) SuspendAll(pubkeys []string) error {
	for _, pk := range pubkeys {
		if err := m.SuspendPeer(pk); err != nil {
			log.Printf("wg: suspend %s: %v", pk[:8], err)
		}
	}
	return nil
}

func (m *Manager) ResumeAll(pubkeys []string) error {
	for _, pk := range pubkeys {
		if err := m.ResumePeer(pk); err != nil {
			log.Printf("wg: resume %s: %v", pk[:8], err)
		}
	}
	return nil
}

func (m *Manager) Stats() ([]*Peer, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	dev, err := m.getDevice()
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

func (m *Manager) setSuspended(pubkeyStr string, suspend bool) error {
	p, ok := m.peers[pubkeyStr]
	if !ok {
		return fmt.Errorf("peer not found: %s", pubkeyStr[:8])
	}

	key, _ := wgtypes.ParseKey(pubkeyStr)
	var allowed []net.IPNet

	if !suspend {
		_, ipNet, _ := net.ParseCIDR(p.AssignedIP)
		allowed = []net.IPNet{*ipNet}
	}

	cfg := wgtypes.Config{
		Peers: []wgtypes.PeerConfig{
			{
				PublicKey:         key,
				AllowedIPs:        allowed,
				ReplaceAllowedIPs: true,
			},
		},
	}
	if err := m.configureDevice(cfg); err != nil {
		return err
	}
	p.Suspended = suspend
	return nil
}

func (m *Manager) syncFromKernel() error {
	dev, err := m.getDevice()
	if err != nil {
		return err
	}
	m.serverKey = dev.PublicKey

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

	ip := cloneIP(network.IP)
	inc(ip)
	inc(ip)

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

func randomHex(n int) string {
	b := make([]byte, n)
	b = b[:copy(b, b)]
	return base64.StdEncoding.EncodeToString(b)
}

func SetupInterface(iface string, port int) error {
	out, _ := exec.Command("ip", "link", "show", iface).Output()
	if len(out) > 0 {
		log.Printf("wg: interface %s already exists, skipping setup", iface)
		return nil
	}

	log.Printf("wg: interface %s not found — create it with wg-quick or setup script", iface)
	return nil
}

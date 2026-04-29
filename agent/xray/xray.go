package xray

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"sync"

	"github.com/google/uuid"
)

type VLESSUser struct {
	UUID  string
	Email string
}

type UserStats struct {
	Uplink   int64
	Downlink int64
}

type Manager struct {
	mu          sync.RWMutex
	confPath    string
	apiAddr     string
	inboundTag  string
	xrayBin     string
	inboundPort int
	flow        string
}

func NewManager(confPath, apiAddr, inboundTag, xrayBin string, inboundPort int, flow string) *Manager {
	if inboundTag == "" {
		inboundTag = "vless-in"
	}
	if xrayBin == "" {
		xrayBin = "/usr/local/bin/xray"
	}
	if inboundPort == 0 {
		inboundPort = 8443
	}
	if flow == "" {
		flow = "xtls-rprx-vision"
	}
	return &Manager{
		confPath:    confPath,
		apiAddr:     apiAddr,
		inboundTag:  inboundTag,
		xrayBin:     xrayBin,
		inboundPort: inboundPort,
		flow:        flow,
	}
}

type xrayConfig struct {
	Log       json.RawMessage   `json:"log"`
	API       json.RawMessage   `json:"api,omitempty"`
	Stats     json.RawMessage   `json:"stats,omitempty"`
	Policy    json.RawMessage   `json:"policy,omitempty"`
	DNS       json.RawMessage   `json:"dns,omitempty"`
	FakeDNS   json.RawMessage   `json:"fakedns,omitempty"`
	Inbounds  []json.RawMessage `json:"inbounds"`
	Outbounds []json.RawMessage `json:"outbounds"`
	Routing   json.RawMessage   `json:"routing,omitempty"`
}

type inbound struct {
	Tag      string          `json:"tag"`
	Listen   string          `json:"listen,omitempty"`
	Port     json.RawMessage `json:"port"`
	Protocol string          `json:"protocol"`
	Settings json.RawMessage `json:"settings"`
	Stream   json.RawMessage `json:"streamSettings,omitempty"`
	Sniff    json.RawMessage `json:"sniffing,omitempty"`
}

type vlessSettings struct {
	Clients    []vlessClient `json:"clients"`
	Decryption string        `json:"decryption"`
}

type vlessClient struct {
	ID    string `json:"id"`
	Flow  string `json:"flow,omitempty"`
	Level int    `json:"level"`
	Email string `json:"email"`
}

func (m *Manager) loadConfig() (*xrayConfig, error) {
	data, err := os.ReadFile(m.confPath)
	if err != nil {
		return nil, fmt.Errorf("read config: %w", err)
	}
	var cfg xrayConfig
	if err := json.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parse config: %w", err)
	}
	return &cfg, nil
}

func (m *Manager) saveConfig(cfg *xrayConfig) error {
	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(m.confPath, data, 0o644)
}

func (m *Manager) findVLESSInbound(cfg *xrayConfig) (int, *inbound, error) {
	for i, raw := range cfg.Inbounds {
		var ib inbound
		if err := json.Unmarshal(raw, &ib); err != nil {
			continue
		}
		if ib.Tag == m.inboundTag {
			return i, &ib, nil
		}
	}
	return -1, nil, fmt.Errorf("vless inbound %q not found", m.inboundTag)
}

// addUserToConfig persists a new client in /etc/xray/config.json.
// Returns the existing user if one with the same email already exists.
func (m *Manager) addUserToConfig(uuidStr, email string) (*VLESSUser, error) {
	cfg, err := m.loadConfig()
	if err != nil {
		return nil, err
	}

	idx, ib, err := m.findVLESSInbound(cfg)
	if err != nil {
		return nil, err
	}

	var settings vlessSettings
	if err := json.Unmarshal(ib.Settings, &settings); err != nil {
		return nil, fmt.Errorf("parse vless settings: %w", err)
	}

	for _, c := range settings.Clients {
		if c.Email == email {
			return &VLESSUser{UUID: c.ID, Email: c.Email}, nil
		}
	}

	settings.Clients = append(settings.Clients, vlessClient{
		ID:    uuidStr,
		Flow:  m.flow,
		Level: 0,
		Email: email,
	})

	settingsBytes, err := json.Marshal(settings)
	if err != nil {
		return nil, err
	}
	ib.Settings = settingsBytes

	ibRaw, err := json.Marshal(ib)
	if err != nil {
		return nil, err
	}
	cfg.Inbounds[idx] = ibRaw

	if err := m.saveConfig(cfg); err != nil {
		return nil, err
	}

	return &VLESSUser{UUID: uuidStr, Email: email}, nil
}

// removeUserFromConfig removes a client by UUID from config.
func (m *Manager) removeUserFromConfig(userUUID string) (string, error) {
	cfg, err := m.loadConfig()
	if err != nil {
		return "", err
	}

	idx, ib, err := m.findVLESSInbound(cfg)
	if err != nil {
		return "", err
	}

	var settings vlessSettings
	if err := json.Unmarshal(ib.Settings, &settings); err != nil {
		return "", fmt.Errorf("parse vless settings: %w", err)
	}

	filtered := make([]vlessClient, 0, len(settings.Clients))
	var removedEmail string
	for _, c := range settings.Clients {
		if c.ID == userUUID {
			removedEmail = c.Email
			continue
		}
		filtered = append(filtered, c)
	}
	if removedEmail == "" {
		return "", nil // already absent — idempotent
	}
	settings.Clients = filtered

	settingsBytes, err := json.Marshal(settings)
	if err != nil {
		return "", err
	}
	ib.Settings = settingsBytes

	ibRaw, err := json.Marshal(ib)
	if err != nil {
		return "", err
	}
	cfg.Inbounds[idx] = ibRaw

	if err := m.saveConfig(cfg); err != nil {
		return "", err
	}

	return removedEmail, nil
}

// addUserAPI applies the user to the running Xray via gRPC (no restart).
func (m *Manager) addUserAPI(uuidStr, email string) error {
	if m.apiAddr == "" {
		return nil // API disabled — config-only mode
	}
	inboundJSON := fmt.Sprintf(
		`{"inbounds":[{"tag":"%s","port":%d,"listen":"0.0.0.0","protocol":"vless","settings":{"clients":[{"id":"%s","flow":"%s","level":0,"email":"%s"}],"decryption":"none"}}]}`,
		m.inboundTag, m.inboundPort, uuidStr, m.flow, email,
	)

	f, err := os.CreateTemp("", "xray-adu-*.json")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	if _, err := f.WriteString(inboundJSON); err != nil {
		f.Close()
		return err
	}
	f.Close()

	cmd := exec.Command(m.xrayBin, "api", "adu", "--server="+m.apiAddr, f.Name())
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("adu: %s: %w", out, err)
	}
	if !strings.Contains(string(out), "Added 1") {
		return fmt.Errorf("adu unexpected output: %s", out)
	}
	return nil
}

// removeUserAPI removes the user from the running Xray via gRPC.
// Idempotent — silent on "user not found".
func (m *Manager) removeUserAPI(email string) error {
	if m.apiAddr == "" {
		return nil
	}
	cmd := exec.Command(m.xrayBin, "api", "rmu", "--server="+m.apiAddr, "-tag="+m.inboundTag, email)
	out, err := cmd.CombinedOutput()
	if err != nil {
		// "User ... not found" comes back with exit 0 + "Removed 0"; if exit != 0 it's a real error
		if strings.Contains(string(out), "User") && strings.Contains(string(out), "not found") {
			return nil
		}
		return fmt.Errorf("rmu: %s: %w", out, err)
	}
	return nil
}

// AddUser creates a new VLESS user (auto-generates UUID).
// Persists to config.json AND applies to running Xray via API.
func (m *Manager) AddUser(label string) (*VLESSUser, error) {
	return m.AddUserWithUUID(uuid.New().String(), label+"@vpn")
}

// AddUserWithUUID adds a user with explicit UUID and email.
func (m *Manager) AddUserWithUUID(existingUUID, email string) (*VLESSUser, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	user, err := m.addUserToConfig(existingUUID, email)
	if err != nil {
		return nil, err
	}

	if err := m.addUserAPI(user.UUID, user.Email); err != nil {
		log.Printf("xray: addUserAPI failed (config persisted, will apply on next restart): %v", err)
	}

	return user, nil
}

// RemoveUser removes a user by UUID. Persists to config + applies live.
func (m *Manager) RemoveUser(userUUID string) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	email, err := m.removeUserFromConfig(userUUID)
	if err != nil {
		return err
	}
	if email == "" {
		return nil // wasn't there, no-op
	}

	if err := m.removeUserAPI(email); err != nil {
		log.Printf("xray: removeUserAPI failed (config persisted): %v", err)
	}
	return nil
}

// ListUsers reads from config (canonical source).
// For live-only check use ListLiveUsers.
func (m *Manager) ListUsers() ([]VLESSUser, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	cfg, err := m.loadConfig()
	if err != nil {
		return nil, err
	}

	_, ib, err := m.findVLESSInbound(cfg)
	if err != nil {
		return nil, err
	}

	var settings vlessSettings
	if err := json.Unmarshal(ib.Settings, &settings); err != nil {
		return nil, err
	}

	users := make([]VLESSUser, len(settings.Clients))
	for i, c := range settings.Clients {
		users[i] = VLESSUser{UUID: c.ID, Email: c.Email}
	}
	return users, nil
}

// GetUserStats fetches uplink/downlink counters for a user via Xray stats API.
func (m *Manager) GetUserStats(email string) (*UserStats, error) {
	if m.apiAddr == "" {
		return &UserStats{}, nil
	}
	stats := &UserStats{}

	upCmd := exec.Command(m.xrayBin, "api", "stats", "--server="+m.apiAddr, "-name",
		fmt.Sprintf("user>>>%s>>>traffic>>>uplink", email))
	if out, err := upCmd.CombinedOutput(); err == nil {
		stats.Uplink = parseStatValue(out)
	}

	dnCmd := exec.Command(m.xrayBin, "api", "stats", "--server="+m.apiAddr, "-name",
		fmt.Sprintf("user>>>%s>>>traffic>>>downlink", email))
	if out, err := dnCmd.CombinedOutput(); err == nil {
		stats.Downlink = parseStatValue(out)
	}

	return stats, nil
}

func parseStatValue(out []byte) int64 {
	var resp struct {
		Stat struct {
			Value json.Number `json:"value"`
		} `json:"stat"`
	}
	if err := json.Unmarshal(out, &resp); err != nil {
		return 0
	}
	v, _ := resp.Stat.Value.Int64()
	return v
}

// Health pings the Xray HTTP health endpoint (legacy — separate from gRPC API).
func (m *Manager) Health() (map[string]any, error) {
	if m.apiAddr == "" {
		return map[string]any{"status": "no-api"}, nil
	}
	resp, err := http.Get("http://" + m.apiAddr + "/health")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	var result map[string]any
	json.Unmarshal(body, &result)
	return result, nil
}

package xray

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"sync"

	"github.com/google/uuid"
)

type VLESSUser struct {
	UUID  string
	Email string
}

type Manager struct {
	mu       sync.RWMutex
	confPath string
	apiAddr  string
	inboundTag string
}

func NewManager(confPath, apiAddr, inboundTag string) *Manager {
	if inboundTag == "" {
		inboundTag = "vless-in"
	}
	return &Manager{
		confPath:   confPath,
		apiAddr:    apiAddr,
		inboundTag: inboundTag,
	}
}

type xrayConfig struct {
	Log       json.RawMessage   `json:"log"`
	API       json.RawMessage   `json:"api"`
	Policy    json.RawMessage   `json:"policy"`
	Inbounds  []json.RawMessage `json:"inbounds"`
	Outbounds []json.RawMessage `json:"outbounds"`
	Routing   json.RawMessage   `json:"routing"`
}

type inbound struct {
	Tag      string          `json:"tag"`
	Listen   string          `json:"listen"`
	Port     json.Number     `json:"port"`
	Protocol string          `json:"protocol"`
	Settings json.RawMessage `json:"settings"`
	Stream   json.RawMessage `json:"streamSettings,omitempty"`
	Sniff    json.RawMessage `json:"sniffing,omitempty"`
}

type vlessSettings struct {
	Clients     []vlessClient `json:"clients"`
	Decryption  string        `json:"decryption"`
}

type vlessClient struct {
	ID    string `json:"id"`
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
	return os.WriteFile(m.confPath, data, 0644)
}

func (m *Manager) reloadXray() error {
	cmd := exec.Command("systemctl", "restart", "xray")
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("restart xray: %s: %w", output, err)
	}
	return nil
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

func (m *Manager) AddUser(label string) (*VLESSUser, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

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
		return nil, fmt.Errorf("parse vless settings: %w", err)
	}

	email := label + "@vpn"

	for _, c := range settings.Clients {
		if c.Email == email {
			return &VLESSUser{UUID: c.ID, Email: c.Email}, nil
		}
	}

	newUUID := uuid.New().String()

	settings.Clients = append(settings.Clients, vlessClient{
		ID:    newUUID,
		Level: 0,
		Email: email,
	})

	settingsBytes, err := json.Marshal(settings)
	if err != nil {
		return nil, err
	}
	ib.Settings = settingsBytes

	idx, _, _ := m.findVLESSInbound(cfg)
	ibRaw, err := json.Marshal(ib)
	if err != nil {
		return nil, err
	}
	cfg.Inbounds[idx] = ibRaw

	if err := m.saveConfig(cfg); err != nil {
		return nil, err
	}

	if err := m.reloadXray(); err != nil {
		log.Printf("xray: reload warning: %v", err)
	}

	return &VLESSUser{UUID: newUUID, Email: email}, nil
}

func (m *Manager) AddUserWithUUID(existingUUID, email string) (*VLESSUser, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

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
		return nil, fmt.Errorf("parse vless settings: %w", err)
	}

	for _, c := range settings.Clients {
		if c.ID == existingUUID {
			return &VLESSUser{UUID: c.ID, Email: c.Email}, nil
		}
	}

	settings.Clients = append(settings.Clients, vlessClient{
		ID:    existingUUID,
		Level: 0,
		Email: email,
	})

	settingsBytes, err := json.Marshal(settings)
	if err != nil {
		return nil, err
	}
	ib.Settings = settingsBytes

	idx, _, _ := m.findVLESSInbound(cfg)
	ibRaw, err := json.Marshal(ib)
	if err != nil {
		return nil, err
	}
	cfg.Inbounds[idx] = ibRaw

	if err := m.saveConfig(cfg); err != nil {
		return nil, err
	}

	return &VLESSUser{UUID: existingUUID, Email: email}, m.reloadXray()
}

func (m *Manager) RemoveUser(userUUID string) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	cfg, err := m.loadConfig()
	if err != nil {
		return err
	}

	_, ib, err := m.findVLESSInbound(cfg)
	if err != nil {
		return err
	}

	var settings vlessSettings
	if err := json.Unmarshal(ib.Settings, &settings); err != nil {
		return fmt.Errorf("parse vless settings: %w", err)
	}

	filtered := make([]vlessClient, 0, len(settings.Clients))
	found := false
	for _, c := range settings.Clients {
		if c.ID == userUUID {
			found = true
			continue
		}
		filtered = append(filtered, c)
	}
	if !found {
		return nil
	}
	settings.Clients = filtered

	settingsBytes, err := json.Marshal(settings)
	if err != nil {
		return err
	}
	ib.Settings = settingsBytes

	idx, _, _ := m.findVLESSInbound(cfg)
	ibRaw, err := json.Marshal(ib)
	if err != nil {
		return err
	}
	cfg.Inbounds[idx] = ibRaw

	if err := m.saveConfig(cfg); err != nil {
		return err
	}

	return m.reloadXray()
}

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

func (m *Manager) Health() (map[string]any, error) {
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
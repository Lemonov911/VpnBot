package config

import (
	"log"
	"os"
	"strconv"
	"strings"
)

type Config struct {
	ListenAddr string
	AgentToken string

	Services []string // e.g. ["wg", "awg"]

	WGInterface string
	WGSubnet    string
	WGEndpoint  string
	WGPort      int

	TotalBandwidthMbit   int
	MinPerPeerMbit      int
	FairShareIntervalSec int

	TelegramBotToken string
	TelegramAdminIDs []int64

	ScriptsDir string // base directory for script-based services
}

func Load() *Config {
	adminIDs := parseAdminIDs(env("ADMIN_IDS", ""))
	port, _ := strconv.Atoi(env("WG_PORT", "51820"))
	totalBW, _ := strconv.Atoi(env("TOTAL_BANDWIDTH_MBIT", "1000"))
	minBW, _ := strconv.Atoi(env("MIN_PER_PEER_MBIT", "50"))
	fsInterval, _ := strconv.Atoi(env("FAIRSHARE_INTERVAL_SEC", "120"))

	services := parseServices(env("SERVICES", "wg"))

	cfg := &Config{
		ListenAddr:           env("LISTEN_ADDR", "0.0.0.0:9000"),
		AgentToken:           mustEnv("AGENT_TOKEN"),
		Services:             services,
		WGInterface:          env("WG_INTERFACE", "wg0"),
		WGSubnet:             env("WG_SUBNET", "10.8.0.0/24"),
		WGEndpoint:           mustEnv("WG_ENDPOINT"),
		WGPort:               port,
		TotalBandwidthMbit:   totalBW,
		MinPerPeerMbit:       minBW,
		FairShareIntervalSec: fsInterval,
		TelegramBotToken:     env("BOT_TOKEN", ""),
		TelegramAdminIDs:     adminIDs,
		ScriptsDir:           env("SCRIPTS_DIR", "/opt/vpnbot/scripts"),
	}
	return cfg
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func mustEnv(key string) string {
	v := os.Getenv(key)
	if v == "" {
		log.Fatalf("required env %s is not set", key)
	}
	return v
}

func parseAdminIDs(s string) []int64 {
	var ids []int64
	for _, p := range strings.Split(s, ",") {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		id, err := strconv.ParseInt(p, 10, 64)
		if err == nil {
			ids = append(ids, id)
		}
	}
	return ids
}

func parseServices(s string) []string {
	var services []string
	for _, p := range strings.Split(s, ",") {
		p = strings.TrimSpace(p)
		if p != "" {
			services = append(services, strings.ToLower(p))
		}
	}
	return services
}
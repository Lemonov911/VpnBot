package config

import (
	"log"
	"os"
	"strconv"
	"strings"
)

type Config struct {
	// HTTP API
	ListenAddr string // e.g. 127.0.0.1:9000

	// Auth
	AgentToken string // X-Agent-Token header

	// WireGuard
	WGInterface string // e.g. wg0
	WGSubnet    string // e.g. 10.8.0.0/24
	WGEndpoint  string // e.g. 1.2.3.4:51820
	WGPort      int

	// Bandwidth (Mbit/s)
	TotalBandwidthMbit int // server uplink, e.g. 1000
	MinPerPeerMbit     int // guaranteed minimum per peer, e.g. 50

	// Fair-share recalc interval (seconds)
	FairShareIntervalSec int

	// Watchdog
	TelegramBotToken string
	TelegramAdminIDs []int64 // who to notify
}

func Load() *Config {
	adminIDs := parseAdminIDs(env("ADMIN_IDS", ""))
	port, _ := strconv.Atoi(env("WG_PORT", "51820"))
	totalBW, _ := strconv.Atoi(env("TOTAL_BANDWIDTH_MBIT", "1000"))
	minBW, _ := strconv.Atoi(env("MIN_PER_PEER_MBIT", "50"))
	fsInterval, _ := strconv.Atoi(env("FAIRSHARE_INTERVAL_SEC", "120"))

	cfg := &Config{
		ListenAddr:           env("LISTEN_ADDR", "127.0.0.1:9000"),
		AgentToken:           mustEnv("AGENT_TOKEN"),
		WGInterface:          env("WG_INTERFACE", "wg0"),
		WGSubnet:             env("WG_SUBNET", "10.8.0.0/24"),
		WGEndpoint:           mustEnv("WG_ENDPOINT"),
		WGPort:               port,
		TotalBandwidthMbit:   totalBW,
		MinPerPeerMbit:       minBW,
		FairShareIntervalSec: fsInterval,
		TelegramBotToken:     env("BOT_TOKEN", ""),
		TelegramAdminIDs:     adminIDs,
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

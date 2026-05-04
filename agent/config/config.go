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

	// Xray / VLESS-Reality (used when "vless"/"vless-base"/"vless-max" appears in Services).
	XrayConfigPath  string // /usr/local/etc/xray/config.json
	XrayAPIAddr     string // 127.0.0.1:10085
	XrayBin         string // /usr/local/bin/xray
	XrayFlow        string // "xtls-rprx-vision" or empty
	XrayPublicHost  string // host to embed in vless:// URLs (e.g. fr.maxvpn.shop or IP)
	XrayPubKey      string // Reality publicKey (shared across tiers)
	XrayShortID     string // Reality shortId (shared across tiers)
	XraySNI         string // Reality dest, e.g. www.yahoo.com
	XrayFingerprint string // utls fingerprint, default "chrome"
	XrayPeerLabel   string // Human-friendly peer name (e.g. "🇩🇪 Frankfurt") for vless:// fragment

	// Per-service tier params. Key = service name ("vless", "vless-base", "vless-max").
	// Each tier has its own Xray inbound tag and first-port for adu JSON.
	XrayTiers map[string]TierConfig
}

type TierConfig struct {
	InboundTag  string
	InboundPort int
}

func Load() *Config {
	adminIDs := parseAdminIDs(env("ADMIN_IDS", ""))
	port, _ := strconv.Atoi(env("WG_PORT", "51820"))
	totalBW, _ := strconv.Atoi(env("TOTAL_BANDWIDTH_MBIT", "1000"))
	minBW, _ := strconv.Atoi(env("MIN_PER_PEER_MBIT", "50"))
	fsInterval, _ := strconv.Atoi(env("FAIRSHARE_INTERVAL_SEC", "120"))

	services := parseServices(env("SERVICES", "wg"))

	wgEndpoint := env("WG_ENDPOINT", "")
	if wgEndpoint == "" && contains(services, "wg") {
		log.Fatalf("required env WG_ENDPOINT is not set (needed for wg service)")
	}

	cfg := &Config{
		ListenAddr:           env("LISTEN_ADDR", "0.0.0.0:9000"),
		AgentToken:           mustEnv("AGENT_TOKEN"),
		Services:             services,
		WGInterface:          env("WG_INTERFACE", "wg0"),
		WGSubnet:             env("WG_SUBNET", "10.8.0.0/24"),
		WGEndpoint:           wgEndpoint,
		WGPort:               port,
		TotalBandwidthMbit:   totalBW,
		MinPerPeerMbit:       minBW,
		FairShareIntervalSec: fsInterval,
		TelegramBotToken:     env("BOT_TOKEN", ""),
		TelegramAdminIDs:     adminIDs,
		ScriptsDir:           env("SCRIPTS_DIR", "/opt/vpnbot/scripts"),

		XrayConfigPath:  env("XRAY_CONFIG_PATH", "/usr/local/etc/xray/config.json"),
		XrayAPIAddr:     env("XRAY_API_ADDR", "127.0.0.1:10085"),
		XrayBin:         env("XRAY_BIN", "/usr/local/bin/xray"),
		XrayFlow:        env("XRAY_FLOW", "xtls-rprx-vision"),
		XrayPublicHost:  env("XRAY_PUBLIC_HOST", ""),
		XrayPubKey:      env("XRAY_PUBKEY", ""),
		XrayShortID:     env("XRAY_SHORT_ID", ""),
		XraySNI:         env("XRAY_SNI", "www.yahoo.com"),
		XrayFingerprint: env("XRAY_FINGERPRINT", "chrome"),
		XrayPeerLabel:   env("XRAY_PEER_LABEL", ""),
		XrayTiers:       map[string]TierConfig{},
	}

	// Tier-specific config: каждый VLESS-service (vless, vless-base, vless-max)
	// читает свою пару INBOUND_TAG / INBOUND_PORT.
	tierVarPrefix := map[string]string{
		"vless":           "XRAY", // legacy compatibility — XRAY_INBOUND_TAG / XRAY_INBOUND_PORT
		"vless-base":      "XRAY_BASE",
		"vless-max":       "XRAY_MAX",
		"vless-base-slow": "XRAY_BASE_SLOW",
		"vless-max-slow":  "XRAY_MAX_SLOW",
	}
	tierDefaults := map[string]TierConfig{
		"vless":           {InboundTag: "vless-in", InboundPort: 8443},
		"vless-base":      {InboundTag: "vless-reality-base", InboundPort: 8443},
		"vless-max":       {InboundTag: "vless-reality-max", InboundPort: 8448},
		"vless-base-slow": {InboundTag: "vless-reality-base-slow", InboundPort: 9443},
		"vless-max-slow":  {InboundTag: "vless-reality-max-slow", InboundPort: 9448},
	}
	hasVLESS := false
	for _, svc := range services {
		prefix, ok := tierVarPrefix[svc]
		if !ok {
			continue
		}
		hasVLESS = true
		def := tierDefaults[svc]
		port, _ := strconv.Atoi(env(prefix+"_INBOUND_PORT", strconv.Itoa(def.InboundPort)))
		cfg.XrayTiers[svc] = TierConfig{
			InboundTag:  env(prefix+"_INBOUND_TAG", def.InboundTag),
			InboundPort: port,
		}
	}

	if hasVLESS {
		if cfg.XrayPublicHost == "" {
			log.Fatalf("required env XRAY_PUBLIC_HOST is not set (needed for VLESS service)")
		}
		if cfg.XrayPubKey == "" {
			log.Fatalf("required env XRAY_PUBKEY is not set (needed for VLESS service)")
		}
		if cfg.XrayShortID == "" {
			log.Fatalf("required env XRAY_SHORT_ID is not set (needed for VLESS service)")
		}
	}

	return cfg
}

func contains(s []string, target string) bool {
	for _, v := range s {
		if v == target {
			return true
		}
	}
	return false
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
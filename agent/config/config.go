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
	StateDir   string // directory for persistent agent state (suspended VLESS peers)

	// Xray / VLESS-Reality (used when "vless"/"vless-base"/"vless-max" appears in Services).
	XrayConfigPath  string // /usr/local/etc/xray/config.json
	XrayAPIAddr     string // 127.0.0.1:10085
	XrayBin         string // /usr/local/bin/xray
	XrayFlow        string // "xtls-rprx-vision" or empty
	XrayPublicHost  string // host to embed in vless:// URLs (e.g. fr.maxvpnesim.com or IP)
	XrayPubKey      string // Reality publicKey (shared across tiers)
	XrayShortID     string // Reality shortId (shared across tiers)
	XraySNI         string // Reality dest, e.g. www.yahoo.com
	XrayFingerprint string // utls fingerprint, default "chrome"
	XrayPeerLabel   string // Human-friendly peer name (e.g. "🇩🇪 Frankfurt") for vless:// fragment

	// Per-service tier params. Key = service name ("vless", "vless-base", "vless-max").
	// Each tier has its own Xray inbound tag and first-port for adu JSON.
	XrayTiers map[string]TierConfig

	// Интерфейс на котором ставится HTB-shaping для VLESS-tier'ов (slow/grace).
	// На большинстве VPS дефолтный route — eth0; auto-detect неоправдан т.к. в
	// контейнерах/AWS бывает ens5/enp0s3 — переопределяй через TC_SHAPE_IFACE.
	// Пустая строка отключает tcshape (для dev/testing без root).
	TCShapeIface string
}

type TierConfig struct {
	InboundTag  string
	InboundPort int
	// RateKbit — server-side HTB throttle для этого tier'а (kbit/s). 0 = unlimited.
	// Применяется через source-port filter на egress-интерфейсе (см. tcshape).
	// Default'ы: slow=5/15Mbit, grace=1Mbit (история: был 256, потом 512, потом 1Mbit —
	// эволюция через жалобы юзеров. 1Mbit = Telegram грузит фото быстро, web-серфинг
	// ok; video/youtube всё равно лагает = стимул продлить).
	RateKbit int
	// TCClassID + TCFilterPref — фиксированные для совместимости с уже
	// задеплоенным HTB на серверах где tc ставился руками.
	TCClassID    string
	TCFilterPref int
}

func Load() *Config {
	adminIDs := parseAdminIDs(env("ADMIN_IDS", ""))
	port := envInt("WG_PORT", 51820)
	totalBW := envInt("TOTAL_BANDWIDTH_MBIT", 1000)
	minBW := envInt("MIN_PER_PEER_MBIT", 50)
	fsInterval := envInt("FAIRSHARE_INTERVAL_SEC", 120)

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
		StateDir:             env("VPNCTL_STATE_DIR", "/var/lib/vpnctl"),

		XrayConfigPath:  env("XRAY_CONFIG_PATH", "/usr/local/etc/xray/config.json"),
		XrayAPIAddr:     env("XRAY_API_ADDR", "127.0.0.1:10085"),
		XrayBin:         env("XRAY_BIN", "/usr/local/bin/xray"),
		XrayFlow:        env("XRAY_FLOW", "xtls-rprx-vision"),
		XrayPublicHost:  env("XRAY_PUBLIC_HOST", ""),
		XrayPubKey:      env("XRAY_PUBKEY", ""),
		XrayShortID:     env("XRAY_SHORT_ID", ""),
		// SNI для VLESS Reality dest. Должен быть домен с TLS 1.3 + HTTP/2, НЕ за Cloudflare/Fastly
		// (CDN headers ломают Reality routing). Выбран addons.mozilla.org:
		//  - mass-used в РФ (Mozilla addons, AdBlock и т.д.) — не вызовет подозрений ТСПУ
		//  - "too big to block" — РКН не блокирует Mozilla
		//  - не на CDN, нативный TLS 1.3 + HTTP/2
		// Переопределяется через env XRAY_SNI per-node (например для RU-нод можно vk.com/yandex).
		XraySNI:         env("XRAY_SNI", "addons.mozilla.org"),
		XrayFingerprint: env("XRAY_FINGERPRINT", "chrome"),
		XrayPeerLabel:   env("XRAY_PEER_LABEL", ""),
		XrayTiers:       map[string]TierConfig{},
		TCShapeIface:    env("TC_SHAPE_IFACE", "eth0"),
	}

	// Tier-specific config: каждый VLESS-service (vless, vless-base, vless-max)
	// читает свою пару INBOUND_TAG / INBOUND_PORT.
	tierVarPrefix := map[string]string{
		"vless":           "XRAY", // legacy compatibility — XRAY_INBOUND_TAG / XRAY_INBOUND_PORT
		"vless-base":      "XRAY_BASE",
		"vless-max":       "XRAY_MAX",
		"vless-base-slow": "XRAY_BASE_SLOW",
		"vless-max-slow":  "XRAY_MAX_SLOW",
		"vless-grace":     "XRAY_GRACE",
	}
	tierDefaults := map[string]TierConfig{
		"vless":           {InboundTag: "vless-in", InboundPort: 8443, RateKbit: 0},
		"vless-base":      {InboundTag: "vless-reality-base", InboundPort: 8443, RateKbit: 0},
		"vless-max":       {InboundTag: "vless-reality-max", InboundPort: 8448, RateKbit: 0},
		// Class-id / filter-pref совпадают с тем что ручной setup ставил на проде
		// (Amsterdam). Менять их = повторный `add` упадёт с "File exists" норм,
		// но если кто-то ОТЛИЧАЕТСЯ — будет два set'а классов с разными rate'ами.
		"vless-base-slow": {InboundTag: "vless-reality-base-slow", InboundPort: 9443, RateKbit: 5000, TCClassID: "1:20", TCFilterPref: 49152},
		"vless-max-slow":  {InboundTag: "vless-reality-max-slow", InboundPort: 9448, RateKbit: 15000, TCClassID: "1:30", TCFilterPref: 49151},
		"vless-grace":     {InboundTag: "vless-reality-grace", InboundPort: 9453, RateKbit: 1024, TCClassID: "1:40", TCFilterPref: 49150},
	}
	hasVLESS := false
	for _, svc := range services {
		prefix, ok := tierVarPrefix[svc]
		if !ok {
			continue
		}
		hasVLESS = true
		def := tierDefaults[svc]
		portStr := env(prefix+"_INBOUND_PORT", strconv.Itoa(def.InboundPort))
		port, err := strconv.Atoi(portStr)
		if err != nil {
			// Atoi-swallow silently gave port=0 → random port → agent broken
			// in a way that's invisible until the bot tries to connect.
			log.Fatalf("invalid env %s_INBOUND_PORT=%q: %v", prefix, portStr, err)
		}
		// RateKbit можно переопределить через ENV (например для дешёвых нод
		// поднять vless-grace до 512kbit). Если ENV пуст — default'ы.
		rateKbit := envInt(prefix+"_RATE_KBIT", def.RateKbit)
		cfg.XrayTiers[svc] = TierConfig{
			InboundTag:   env(prefix+"_INBOUND_TAG", def.InboundTag),
			InboundPort:  port,
			RateKbit:     rateKbit,
			TCClassID:    def.TCClassID,
			TCFilterPref: def.TCFilterPref,
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

func envInt(key string, fallback int) int {
	s := os.Getenv(key)
	if s == "" {
		return fallback
	}
	v, err := strconv.Atoi(s)
	if err != nil {
		log.Printf("config: invalid %s=%q, using default %d", key, s, fallback)
		return fallback
	}
	return v
}
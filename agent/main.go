package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/joho/godotenv"
	"vpnctl/api"
	awgpkg "vpnctl/awg"
	"vpnctl/config"
	"vpnctl/fairshare"
	"vpnctl/service"
	"vpnctl/watchdog"
	wgpkg "vpnctl/wg"
	xraypkg "vpnctl/xray"
)

func main() {
	godotenv.Load()

	cfg := config.Load()

	services := make(map[string]service.Service)

	var wgMgr *wgpkg.Manager

	for _, svcName := range cfg.Services {
		switch svcName {
		case "wg":
			mgr, err := wgpkg.NewManager(cfg.WGInterface, cfg.WGSubnet, cfg.WGEndpoint)
			if err != nil {
				log.Fatalf("wg init: %v", err)
			}
			wgMgr = mgr
			services["wg"] = service.NewWGService(mgr)
			log.Printf("service: wg (built-in, interface=%s)", cfg.WGInterface)

		case "awg":
			// AmneziaWG — server params (Jc/H1-H4/S1-S4) generated once by
			// agent/scripts/awg-install.sh and saved to JSON file.
			paramsPath := os.Getenv("AWG_PARAMS_FILE")
			if paramsPath == "" {
				paramsPath = "/etc/amnezia/amneziawg/server-params.json"
			}
			mgr, err := awgpkg.NewManager(paramsPath)
			if err != nil {
				log.Fatalf("awg init: %v", err)
			}
			services["awg"] = service.NewAWGService(mgr)
			log.Printf("service: awg (built-in, interface=%s, endpoint=%s)",
				mgr.Interface(), mgr.Endpoint())

		case "vless", "vless-base", "vless-max", "vless-base-slow", "vless-max-slow":
			tier, ok := cfg.XrayTiers[svcName]
			if !ok {
				log.Fatalf("vless tier %q not configured", svcName)
			}
			// vless-max и его slow-вариант используют чистый VLESS без vision,
			// чтобы быть совместимым с большим набором клиентов и mux в перспективе.
			// vless / vless-base / vless-base-slow — с Vision.
			flow := cfg.XrayFlow
			if svcName == "vless-max" || svcName == "vless-max-slow" {
				flow = ""
			}
			xrayMgr := xraypkg.NewManager(
				cfg.XrayConfigPath,
				cfg.XrayAPIAddr,
				tier.InboundTag,
				cfg.XrayBin,
				tier.InboundPort,
				flow,
			)
			services[svcName] = service.NewVLESSService(xrayMgr, service.VLESSConnection{
				Host:      cfg.XrayPublicHost,
				Port:      tier.InboundPort,
				SNI:       cfg.XraySNI,
				PubKey:    cfg.XrayPubKey,
				ShortID:   cfg.XrayShortID,
				FP:        cfg.XrayFingerprint,
				Flow:      flow,
				PeerLabel: cfg.XrayPeerLabel,
			})
			log.Printf("service: %s (built-in, inbound=%s, port=%d, host=%s, flow=%q)",
				svcName, tier.InboundTag, tier.InboundPort, cfg.XrayPublicHost, flow)

		default:
			svcDir := cfg.ScriptsDir + "/" + svcName
			if _, err := os.Stat(svcDir); os.IsNotExist(err) {
				log.Fatalf("service %q: scripts dir %s not found", svcName, svcDir)
			}
			services[svcName] = service.NewScriptService(svcName, svcDir)
			log.Printf("service: %s (script, dir=%s)", svcName, svcDir)
		}
	}

	if len(services) == 0 {
		log.Fatal("no services configured (set SERVICES=wg,awg,...)")
	}

	if wgMgr != nil {
		fs := fairshare.NewScheduler(
			cfg.WGInterface,
			cfg.TotalBandwidthMbit,
			cfg.MinPerPeerMbit,
			cfg.FairShareIntervalSec,
			wgMgr,
		)
		go fs.Run()
	}

	wd := watchdog.New(
		cfg.TelegramBotToken,
		cfg.TelegramAdminIDs,
		"http://"+cfg.ListenAddr+"/health",
	)
	go wd.Run()

	srv := api.NewServer(services, wgMgr, cfg.AgentToken)
	httpServer := &http.Server{
		Addr:         cfg.ListenAddr,
		Handler:      srv.Handler(),
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 30 * time.Second,
	}

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		log.Printf("vpnctl listening on %s (services: %v)", cfg.ListenAddr, cfg.Services)
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("http: %v", err)
		}
	}()

	<-quit
	log.Println("shutting down...")

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	httpServer.Shutdown(ctx)

	log.Println("vpnctl stopped")
}
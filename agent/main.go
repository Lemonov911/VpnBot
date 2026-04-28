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
	"vpnctl/config"
	"vpnctl/fairshare"
	"vpnctl/watchdog"
	"vpnctl/wg"
	"vpnctl/xray"
)

func main() {
	// Load .env if present
	godotenv.Load()

	cfg := config.Load()

	log.Printf("vpnctl starting on %s (wg=%s)", cfg.ListenAddr, cfg.WGInterface)

	// Init WireGuard manager
	mgr, err := wg.NewManager(cfg.WGInterface, cfg.WGSubnet, cfg.WGEndpoint)
	if err != nil {
		log.Fatalf("wg init: %v", err)
	}

	// Init Xray VLESS manager (optional — only if config path is set)
	var vlessMgr *xray.Manager
	if cfg.XrayConfigPath != "" {
		vlessMgr = xray.NewManager(cfg.XrayConfigPath, cfg.XrayAPIAddr, cfg.XrayInboundTag)
		log.Printf("vless: xray manager enabled (config=%s, api=%s)", cfg.XrayConfigPath, cfg.XrayAPIAddr)
	}

	// Fair-share scheduler
	fs := fairshare.NewScheduler(
		cfg.WGInterface,
		cfg.TotalBandwidthMbit,
		cfg.MinPerPeerMbit,
		cfg.FairShareIntervalSec,
		mgr,
	)
	go fs.Run()

	// Watchdog
	wd := watchdog.New(
		cfg.TelegramBotToken,
		cfg.TelegramAdminIDs,
		"http://"+cfg.ListenAddr+"/health",
	)
	go wd.Run()

	// HTTP server
	srv := api.NewServer(mgr, vlessMgr, cfg.AgentToken, cfg.VLESSAddr)
	httpServer := &http.Server{
		Addr:         cfg.ListenAddr,
		Handler:      srv.Handler(),
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 10 * time.Second,
	}

	// Graceful shutdown
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		log.Printf("vpnctl listening on %s", cfg.ListenAddr)
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

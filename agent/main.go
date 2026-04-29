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
	"vpnctl/service"
	wgpkg "vpnctl/wg"
	"vpnctl/watchdog"
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
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 10 * time.Second,
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
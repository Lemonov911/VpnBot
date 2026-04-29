package api

import (
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"vpnctl/service"
	"vpnctl/wg"
)

type Server struct {
	services  map[string]service.Service
	wgMgr     *wg.Manager
	token     string
	startTime time.Time
}

func NewServer(services map[string]service.Service, wgMgr *wg.Manager, token string) *Server {
	return &Server{
		services:  services,
		wgMgr:     wgMgr,
		token:     token,
		startTime: time.Now(),
	}
}

func (s *Server) Handler() http.Handler {
	r := chi.NewRouter()
	r.Use(middleware.Recoverer)

	r.Get("/health", s.handleHealth)

	r.Group(func(r chi.Router) {
		r.Use(middleware.Logger)
		r.Use(s.authMiddleware)

		r.Get("/services", s.handleListServices)

		for name, svc := range s.services {
			svcName := name
			svcRef := svc
			isWG := name == "wg"
			r.Route("/services/"+svcName, func(r chi.Router) {
				r.Post("/peers", s.handleServiceAddPeer(svcRef, false))
				r.Get("/peers", s.handleServiceListPeers(svcRef, false))
				r.Delete("/peers/{id}", s.handleServiceRemovePeer(svcRef))
				r.Put("/peers/{id}/suspend", s.handleServiceSuspendPeer(svcRef))
				r.Put("/peers/{id}/resume", s.handleServiceResumePeer(svcRef))
				r.Post("/peers/suspend-all", s.handleServiceSuspendAll(svcRef, false))
				r.Post("/peers/resume-all", s.handleServiceResumeAll(svcRef, false))
				r.Get("/info", s.handleServiceInfo(svcRef))
			})

			if isWG {
				r.Post("/peers", s.handleServiceAddPeer(svcRef, true))
				r.Get("/peers", s.handleServiceListPeers(svcRef, true))
				r.Delete("/peers/{pubkey}", s.handleServiceRemovePeer(svcRef))
				r.Put("/peers/{pubkey}/suspend", s.handleServiceSuspendPeer(svcRef))
				r.Put("/peers/{pubkey}/resume", s.handleServiceResumePeer(svcRef))
				r.Post("/peers/suspend-all", s.handleServiceSuspendAll(svcRef, true))
				r.Post("/peers/resume-all", s.handleServiceResumeAll(svcRef, true))
			}
		}
	})

	return r
}

func (s *Server) authMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Agent-Token") != s.token {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		next.ServeHTTP(w, r)
	})
}
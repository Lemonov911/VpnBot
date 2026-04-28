package api

import (
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"vpnctl/wg"
	"vpnctl/xray"
)

// WGIface is everything the API needs from the WireGuard manager.
type WGIface interface {
	AddPeer(label string) (*wg.Peer, *wg.ClientConfig, error)
	RemovePeer(pubkey string) error
	SuspendPeer(pubkey string) error
	ResumePeer(pubkey string) error
	SuspendAll(pubkeys []string) error
	ResumeAll(pubkeys []string) error
	Stats() ([]*wg.Peer, error)
	ActivePeerCount() int
	PeerIPs() []string
	ServerPublicKey() string
	Interface() string
}

type Server struct {
	wg           WGIface
	vless        *xray.Manager
	token        string
	vlessAddr    string // host:port for VLESS connection (e.g. 151.243.113.31:8443)
	startTime    time.Time
}

func NewServer(mgr WGIface, vlessMgr *xray.Manager, token, vlessAddr string) *Server {
	return &Server{wg: mgr, vless: vlessMgr, token: token, vlessAddr: vlessAddr, startTime: time.Now()}
}

func (s *Server) Handler() http.Handler {
	r := chi.NewRouter()
	r.Use(middleware.Recoverer)

	// Health is public — watchdog hits it without a token
	r.Get("/health", s.handleHealth)

	// Everything else requires auth
	r.Group(func(r chi.Router) {
		r.Use(middleware.Logger)
		r.Use(s.authMiddleware)

		r.Post("/peers", s.handleAddPeer)
		r.Get("/peers", s.handleListPeers)
		r.Get("/peers/{pubkey}", s.handleGetPeer)
		r.Delete("/peers/{pubkey}", s.handleRemovePeer)
		r.Put("/peers/{pubkey}/suspend", s.handleSuspendPeer)
		r.Put("/peers/{pubkey}/resume", s.handleResumePeer)

		r.Post("/peers/suspend-all", s.handleSuspendAll)
		r.Post("/peers/resume-all", s.handleResumeAll)

		if s.vless != nil {
			r.Post("/vless/users", s.handleAddVLESSUser)
			r.Get("/vless/users", s.handleListVLESSUsers)
			r.Delete("/vless/users/{uuid}", s.handleRemoveVLESSUser)
			r.Put("/vless/users/{uuid}/suspend", s.handleSuspendVLESSUser)
			r.Put("/vless/users/{uuid}/resume", s.handleResumeVLESSUser)
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

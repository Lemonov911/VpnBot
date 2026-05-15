package api

import (
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"io"
	"net/http"
	"strconv"
	"strings"
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
				r.Post("/peers/{id}/throttle", s.handleServiceThrottlePeer("awg0"))
				r.Delete("/peers/{id}/throttle", s.handleServiceUnthrottlePeer("awg0"))
				r.Post("/sync", s.handleServiceSync(svcRef))
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

// authMiddleware требует HMAC-SHA256 подпись в заголовке
// `X-Agent-Sig: <ts>.<hex(hmac)>` где hmac = HMAC_SHA256(token, ts+":"+method+path+":"+body).
//
// Раньше принимался также `X-Agent-Token: <raw token>` как legacy fallback.
// Удалён 2026-05-15 (sec audit C1): он сводил на нет защиту HMAC от replay/
// перехвата одиночного запроса. Если боту нужно откатиться — оба компонента
// (агент + bot) рестартить вместе, иначе 401.
func (s *Server) authMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		sig := r.Header.Get("X-Agent-Sig")
		if sig == "" || !s.verifyHMAC(r, sig) {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (s *Server) verifyHMAC(r *http.Request, sig string) bool {
	parts := strings.SplitN(sig, ".", 2)
	if len(parts) != 2 {
		return false
	}
	tsStr, gotHex := parts[0], parts[1]
	ts, err := strconv.ParseInt(tsStr, 10, 64)
	if err != nil {
		return false
	}
	now := time.Now().Unix()
	if ts < now-300 || ts > now+300 {
		return false // replay window: ±5 min
	}

	body, _ := io.ReadAll(r.Body)
	r.Body = io.NopCloser(strings.NewReader(string(body)))

	mac := hmac.New(sha256.New, []byte(s.token))
	mac.Write([]byte(tsStr))
	mac.Write([]byte(":"))
	mac.Write([]byte(r.Method))
	mac.Write([]byte(r.URL.Path))
	mac.Write([]byte(":"))
	mac.Write(body)
	want := hex.EncodeToString(mac.Sum(nil))

	return subtle.ConstantTimeCompare([]byte(want), []byte(gotHex)) == 1
}
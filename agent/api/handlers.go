package api

import (
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"os/exec"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"vpnctl/service"
)

var logger = log.Default()

type addPeerReq struct {
	Label string `json:"label"`
	ID    string `json:"id,omitempty"` // optional — reuse this UUID instead of generating a new one
}

type bulkIDsReq struct {
	IDs   []string `json:"ids"`
	Pubkeys []string `json:"pubkeys"`
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	jsonOK(w, map[string]any{
		"status":   "ok",
		"uptime":   time.Since(s.startTime).String(),
		"services": serviceNames(s.services),
	})
}

func (s *Server) handleListServices(w http.ResponseWriter, r *http.Request) {
	type svcInfo struct {
		Name string         `json:"name"`
		Info map[string]any `json:"info"`
	}
	result := make([]svcInfo, 0, len(s.services))
	for name, svc := range s.services {
		result = append(result, svcInfo{Name: name, Info: svc.Info()})
	}
	jsonOK(w, result)
}

func (s *Server) handleServiceAddPeer(svc service.Service, compatWG bool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req addPeerReq
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			jsonError(w, "bad request", http.StatusBadRequest)
			return
		}
		if req.Label == "" {
			req.Label = "unnamed"
		}
		var peer *service.Peer
		var err error
		if req.ID != "" {
			if svc2, ok := svc.(service.PeerWithIDAdder); ok {
				peer, err = svc2.AddPeerWithID(req.ID, req.Label)
			} else {
				jsonError(w, "service does not support add-with-id", http.StatusBadRequest)
				return
			}
		} else {
			peer, err = svc.AddPeer(req.Label)
		}
		if err != nil {
			jsonError(w, err.Error(), http.StatusInternalServerError)
			return
		}
		if compatWG {
			jsonOK(w, map[string]any{
				"peer": map[string]any{
					"public_key":  peer.ID,
					"assigned_ip": peer.Extra["assigned_ip"],
					"label":        peer.Label,
					"suspended":    peer.Suspended,
					"rx_bytes":     peer.RxBytes,
					"tx_bytes":     peer.TxBytes,
					"created_at":  peer.CreatedAt,
				},
				"wg_config": peer.Config,
			})
			return
		}
		jsonOK(w, peer)
	}
}

func (s *Server) handleServiceListPeers(svc service.Service, compatWG bool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		peers, err := svc.ListPeers()
		if err != nil {
			jsonError(w, err.Error(), http.StatusInternalServerError)
			return
		}
		if compatWG {
			type oldPeer struct {
				PublicKey  string    `json:"public_key"`
				AssignedIP string    `json:"assigned_ip"`
				Label      string    `json:"label"`
				Suspended  bool      `json:"suspended"`
				RxBytes    int64     `json:"rx_bytes"`
				TxBytes    int64     `json:"tx_bytes"`
				LastSeen   time.Time `json:"last_seen,omitempty"`
				CreatedAt  time.Time `json:"created_at"`
			}
			old := make([]oldPeer, len(peers))
			for i, p := range peers {
				ip, _ := p.Extra["assigned_ip"].(string)
				old[i] = oldPeer{
					PublicKey:  p.ID,
					AssignedIP: ip,
					Label:      p.Label,
					Suspended:  p.Suspended,
					RxBytes:    p.RxBytes,
					TxBytes:    p.TxBytes,
					LastSeen:   p.LastSeen,
					CreatedAt:  p.CreatedAt,
				}
			}
			jsonOK(w, old)
			return
		}
		jsonOK(w, peers)
	}
}

func (s *Server) handleServiceRemovePeer(svc service.Service) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id := peerID(r)
		if id == "" {
			jsonError(w, "missing peer id", http.StatusBadRequest)
			return
		}
		if err := svc.RemovePeer(id); err != nil {
			jsonError(w, err.Error(), http.StatusInternalServerError)
			return
		}
		jsonOK(w, map[string]string{"status": "removed"})
	}
}

func (s *Server) handleServiceSuspendPeer(svc service.Service) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id := peerID(r)
		if id == "" {
			jsonError(w, "missing peer id", http.StatusBadRequest)
			return
		}
		if err := svc.SuspendPeer(id); err != nil {
			jsonError(w, err.Error(), http.StatusInternalServerError)
			return
		}
		jsonOK(w, map[string]string{"status": "suspended"})
	}
}

func (s *Server) handleServiceResumePeer(svc service.Service) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id := peerID(r)
		if id == "" {
			jsonError(w, "missing peer id", http.StatusBadRequest)
			return
		}
		if err := svc.ResumePeer(id); err != nil {
			jsonError(w, err.Error(), http.StatusInternalServerError)
			return
		}
		jsonOK(w, map[string]string{"status": "resumed"})
	}
}

// handleServiceThrottlePeer applies a tc bandwidth limit (kbps) to an AWG peer
// by its assigned IP on the awg0 interface. For non-AWG services this is a no-op
// (VLESS grace is handled via inbound routing at the Xray level).
func (s *Server) handleServiceThrottlePeer(iface string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id := peerID(r)
		if id == "" {
			jsonError(w, "missing peer id", http.StatusBadRequest)
			return
		}
		var req struct {
			Kbps int    `json:"kbps"`
			IP   string `json:"ip"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.IP == "" || req.Kbps <= 0 {
			jsonError(w, "bad request: need ip and kbps", http.StatusBadRequest)
			return
		}
		peerIP := strings.Split(req.IP, "/")[0] // strip /32 if present
		if net.ParseIP(peerIP) == nil {
			jsonError(w, "bad request: invalid ip", http.StatusBadRequest)
			return
		}
		// classid 3:X — derived from last octet of peer IP. Default class = 3:1
		// (зарезервировано, реальный peer не может получить IP с октетом .1
		// — это gateway). Раньше был 3:30 и пир c .30 ломал full-speed класс.
		parts := strings.Split(peerIP, ".")
		octet := parts[len(parts)-1]
		classid := "3:" + octet
		cmds := [][]string{
			// Ensure HTB root exists on the interface (idempotent)
			{"tc", "qdisc", "add", "dev", iface, "root", "handle", "3:", "htb", "default", "1"},
			// Default full-speed class (classid 3:1 — never collides with peer octets 2-254)
			{"tc", "class", "add", "dev", iface, "parent", "3:", "classid", "3:1", "htb", "rate", "1000mbit"},
			// Throttle class for this peer
			{"tc", "class", "add", "dev", iface, "parent", "3:", "classid", classid,
				"htb", "rate", fmt.Sprintf("%dkbit", req.Kbps), "ceil", fmt.Sprintf("%dkbit", req.Kbps), "burst", "64k"},
			// Filter: traffic TO peer IP → throttle class
			{"tc", "filter", "add", "dev", iface, "parent", "3:", "protocol", "ip",
				"u32", "match", "ip", "dst", peerIP + "/32", "flowid", classid},
		}
		for _, cmd := range cmds {
			if out, err := exec.Command(cmd[0], cmd[1:]...).CombinedOutput(); err != nil {
				// ignore "already exists" errors
				if !strings.Contains(string(out), "RTNETLINK answers: File exists") {
					logger.Printf("throttle tc warn: %v: %s", err, out)
				}
			}
		}
		jsonOK(w, map[string]string{"status": "throttled", "ip": peerIP, "classid": classid})
	}
}

// handleServiceUnthrottlePeer removes tc bandwidth limit for a peer IP.
func (s *Server) handleServiceUnthrottlePeer(iface string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			IP string `json:"ip"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.IP == "" {
			jsonError(w, "bad request: need ip", http.StatusBadRequest)
			return
		}
		peerIP := strings.Split(req.IP, "/")[0]
		if net.ParseIP(peerIP) == nil {
			jsonError(w, "bad request: invalid ip", http.StatusBadRequest)
			return
		}
		parts := strings.Split(peerIP, ".")
		octet := parts[len(parts)-1]
		classid := "3:" + octet
		// Remove filter and class for this peer
		exec.Command("tc", "filter", "del", "dev", iface, "parent", "3:", "protocol", "ip",
			"u32", "match", "ip", "dst", peerIP+"/32", "flowid", classid).Run()
		exec.Command("tc", "class", "del", "dev", iface, "classid", classid).Run()
		jsonOK(w, map[string]string{"status": "unthrottled", "ip": peerIP})
	}
}

func (s *Server) handleServiceSuspendAll(svc service.Service, compatWG bool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req bulkIDsReq
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			jsonError(w, "bad request", http.StatusBadRequest)
			return
		}
		ids := req.IDs
		if len(ids) == 0 {
			ids = req.Pubkeys
		}
		if err := svc.SuspendAll(ids); err != nil {
			jsonError(w, err.Error(), http.StatusInternalServerError)
			return
		}
		jsonOK(w, map[string]string{"status": "ok"})
	}
}

func (s *Server) handleServiceResumeAll(svc service.Service, compatWG bool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req bulkIDsReq
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			jsonError(w, "bad request", http.StatusBadRequest)
			return
		}
		ids := req.IDs
		if len(ids) == 0 {
			ids = req.Pubkeys
		}
		if err := svc.ResumeAll(ids); err != nil {
			jsonError(w, err.Error(), http.StatusInternalServerError)
			return
		}
		jsonOK(w, map[string]string{"status": "ok"})
	}
}

func (s *Server) handleServiceInfo(svc service.Service) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		jsonOK(w, svc.Info())
	}
}

type syncReq struct {
	ValidIDs []string `json:"valid_ids"`
}

// handleServiceSync removes any peer whose ID is not in valid_ids.
// Used by the bot to keep the agent in sync with paid subscriptions —
// peers whose subscription expired/was cancelled are removed automatically.
func (s *Server) handleServiceSync(svc service.Service) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req syncReq
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			jsonError(w, "bad request", http.StatusBadRequest)
			return
		}
		peers, err := svc.ListPeers()
		if err != nil {
			jsonError(w, err.Error(), http.StatusInternalServerError)
			return
		}
		valid := make(map[string]bool, len(req.ValidIDs))
		for _, id := range req.ValidIDs {
			valid[id] = true
		}
		removed := []string{}
		kept := 0
		for _, p := range peers {
			if !valid[p.ID] {
				if err := svc.RemovePeer(p.ID); err == nil {
					removed = append(removed, p.ID)
				}
				continue
			}
			kept++
		}
		jsonOK(w, map[string]any{
			"removed":     removed,
			"kept":        kept,
			"valid_count": len(req.ValidIDs),
		})
	}
}

func serviceNames(services map[string]service.Service) []string {
	names := make([]string, 0, len(services))
	for name := range services {
		names = append(names, name)
	}
	return names
}

func jsonOK(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}

func peerID(r *http.Request) string {
	if id := chi.URLParam(r, "id"); id != "" {
		return id
	}
	if id := chi.URLParam(r, "pubkey"); id != "" {
		return id
	}
	if id := r.URL.Query().Get("id"); id != "" {
		return id
	}
	return r.URL.Query().Get("pubkey")
}

func jsonError(w http.ResponseWriter, msg string, code int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(map[string]string{"error": msg})
}
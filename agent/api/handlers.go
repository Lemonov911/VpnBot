package api

import (
	"encoding/json"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"vpnctl/wg"
)

// ---- request/response types ----

type addPeerReq struct {
	Label string `json:"label"`
}

type peerResp struct {
	PublicKey  string    `json:"public_key"`
	AssignedIP string    `json:"assigned_ip"`
	Label      string    `json:"label"`
	Suspended  bool      `json:"suspended"`
	RxBytes    int64     `json:"rx_bytes"`
	TxBytes    int64     `json:"tx_bytes"`
	LastSeen   time.Time `json:"last_seen,omitempty"`
	CreatedAt  time.Time `json:"created_at"`
}

type addPeerResp struct {
	Peer   peerResp `json:"peer"`
	Config string   `json:"wg_config"` // ready-to-use .conf file content
}

type bulkReq struct {
	Pubkeys []string `json:"pubkeys"`
}

// ---- handlers ----

// POST /peers
func (s *Server) handleAddPeer(w http.ResponseWriter, r *http.Request) {
	var req addPeerReq
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	if req.Label == "" {
		req.Label = "unnamed"
	}

	peer, cfg, err := s.wg.AddPeer(req.Label)
	if err != nil {
		jsonError(w, err.Error(), http.StatusInternalServerError)
		return
	}

	jsonOK(w, addPeerResp{
		Peer:   toPeerResp(peer),
		Config: wg.WireGuardConfig(cfg),
	})
}

// GET /peers
func (s *Server) handleListPeers(w http.ResponseWriter, r *http.Request) {
	peers, err := s.wg.Stats()
	if err != nil {
		jsonError(w, err.Error(), http.StatusInternalServerError)
		return
	}
	resp := make([]peerResp, len(peers))
	for i, p := range peers {
		resp[i] = toPeerResp(p)
	}
	jsonOK(w, resp)
}

// GET /peers/{pubkey}
func (s *Server) handleGetPeer(w http.ResponseWriter, r *http.Request) {
	pubkey := chi.URLParam(r, "pubkey")
	peers, err := s.wg.Stats()
	if err != nil {
		jsonError(w, err.Error(), http.StatusInternalServerError)
		return
	}
	for _, p := range peers {
		if p.PublicKey == pubkey {
			jsonOK(w, toPeerResp(p))
			return
		}
	}
	http.NotFound(w, r)
}

// DELETE /peers/{pubkey}
func (s *Server) handleRemovePeer(w http.ResponseWriter, r *http.Request) {
	pubkey := chi.URLParam(r, "pubkey")
	if err := s.wg.RemovePeer(pubkey); err != nil {
		jsonError(w, err.Error(), http.StatusInternalServerError)
		return
	}
	jsonOK(w, map[string]string{"status": "removed"})
}

// PUT /peers/{pubkey}/suspend
func (s *Server) handleSuspendPeer(w http.ResponseWriter, r *http.Request) {
	pubkey := chi.URLParam(r, "pubkey")
	if err := s.wg.SuspendPeer(pubkey); err != nil {
		jsonError(w, err.Error(), http.StatusInternalServerError)
		return
	}
	jsonOK(w, map[string]string{"status": "suspended"})
}

// PUT /peers/{pubkey}/resume
func (s *Server) handleResumePeer(w http.ResponseWriter, r *http.Request) {
	pubkey := chi.URLParam(r, "pubkey")
	if err := s.wg.ResumePeer(pubkey); err != nil {
		jsonError(w, err.Error(), http.StatusInternalServerError)
		return
	}
	jsonOK(w, map[string]string{"status": "resumed"})
}

// POST /peers/suspend-all
func (s *Server) handleSuspendAll(w http.ResponseWriter, r *http.Request) {
	var req bulkReq
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	s.wg.SuspendAll(req.Pubkeys)
	jsonOK(w, map[string]string{"status": "ok"})
}

// POST /peers/resume-all
func (s *Server) handleResumeAll(w http.ResponseWriter, r *http.Request) {
	var req bulkReq
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	s.wg.ResumeAll(req.Pubkeys)
	jsonOK(w, map[string]string{"status": "ok"})
}

// GET /health
func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	jsonOK(w, map[string]any{
		"status":     "ok",
		"wg_iface":   s.wg.Interface(),
		"server_key": s.wg.ServerPublicKey(),
		"active":     s.wg.ActivePeerCount(),
		"uptime":     time.Since(s.startTime).String(),
	})
}

// ---- helpers ----

func toPeerResp(p *wg.Peer) peerResp {
	return peerResp{
		PublicKey:  p.PublicKey,
		AssignedIP: p.AssignedIP,
		Label:      p.Label,
		Suspended:  p.Suspended,
		RxBytes:    p.RxBytes,
		TxBytes:    p.TxBytes,
		LastSeen:   p.LastSeen,
		CreatedAt:  p.CreatedAt,
	}
}

func jsonOK(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}

func jsonError(w http.ResponseWriter, msg string, code int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(map[string]string{"error": msg})
}

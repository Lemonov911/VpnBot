package service

import (
	"encoding/json"
	"fmt"
	"log"
	"net/url"
	"os"
	"strings"
	"sync"

	"vpnctl/xray"
)

// VLESSConnection holds the public-facing parameters needed to build vless:// URLs.
type VLESSConnection struct {
	Host       string // public host or IP, e.g. "fr.maxvpnesim.com" or "207.154.214.108"
	Port       int    // 8443 (or first port from a multi-port range)
	SNI        string // www.yahoo.com (Reality dest)
	PubKey     string // Reality publicKey
	ShortID    string // Reality shortId
	FP         string // utls fingerprint, default "chrome"
	Flow       string // VLESS flow, default "xtls-rprx-vision"
	PeerLabel  string // Human-friendly peer name, e.g. "🇩🇪 Frankfurt".
	            //     Falls back to the per-call label if empty.
}

// VLESSService implements service.Service on top of xray.Manager + Reality URL builder.
type VLESSService struct {
	mgr       *xray.Manager
	conn      VLESSConnection
	statePath string // persisted suspended-peers state; empty = no persistence

	mu        sync.Mutex
	suspended map[string]VLESSSuspended // key = uuid
}

type VLESSSuspended struct {
	UUID  string
	Email string
}

// NewVLESSService creates a VLESS service. statePath is the path to a JSON file
// used to persist suspended-peer state across restarts; pass "" to disable.
func NewVLESSService(mgr *xray.Manager, conn VLESSConnection, statePath string) *VLESSService {
	if conn.FP == "" {
		conn.FP = "chrome"
	}
	// Note: empty conn.Flow is intentional — vless-max / vless-max-slow run
	// plain VLESS without xtls-rprx-vision. Don't set a default here.
	svc := &VLESSService{
		mgr:       mgr,
		conn:      conn,
		statePath: statePath,
		suspended: make(map[string]VLESSSuspended),
	}
	svc.loadState()
	return svc
}

// loadState reads persisted suspended-peer state from disk (best-effort).
func (s *VLESSService) loadState() {
	if s.statePath == "" {
		return
	}
	data, err := os.ReadFile(s.statePath)
	if err != nil {
		return // file may not exist yet — normal on first run
	}
	var m map[string]VLESSSuspended
	if err := json.Unmarshal(data, &m); err != nil {
		log.Printf("vless: failed to parse state file %s: %v", s.statePath, err)
		return
	}
	s.mu.Lock()
	s.suspended = m
	s.mu.Unlock()
	log.Printf("vless: loaded %d suspended peers from %s", len(m), s.statePath)
}

// saveState persists the suspended-peer map to disk atomically.
func (s *VLESSService) saveState() {
	if s.statePath == "" {
		return
	}
	s.mu.Lock()
	data, err := json.Marshal(s.suspended)
	s.mu.Unlock()
	if err != nil {
		return
	}
	tmp := s.statePath + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		log.Printf("vless: failed to write state file %s: %v", tmp, err)
		return
	}
	if err := os.Rename(tmp, s.statePath); err != nil {
		log.Printf("vless: failed to rename state file: %v", err)
	}
}

func (s *VLESSService) buildURL(uuid, label string) string {
	q := url.Values{}
	q.Set("encryption", "none")
	if s.conn.Flow != "" {
		q.Set("flow", s.conn.Flow)
	}
	q.Set("security", "reality")
	q.Set("sni", s.conn.SNI)
	q.Set("fp", s.conn.FP)
	q.Set("pbk", s.conn.PubKey)
	q.Set("sid", s.conn.ShortID)
	q.Set("type", "tcp")
	q.Set("headerType", "none")
	q.Set("spx", "/")

	// Human-friendly fragment (Happ shows this as the peer's name + flag emoji).
	// Use the configured label if set; fall back to the technical label otherwise.
	displayName := s.conn.PeerLabel
	if displayName == "" {
		displayName = label
	}
	frag := url.PathEscape(displayName)
	return fmt.Sprintf("vless://%s@%s:%d?%s#%s", uuid, s.conn.Host, s.conn.Port, q.Encode(), frag)
}

func (s *VLESSService) AddPeer(label string) (*Peer, error) {
	user, err := s.mgr.AddUser(label)
	if err != nil {
		return nil, err
	}
	return &Peer{
		ID:     user.UUID,
		Label:  label,
		Config: s.buildURL(user.UUID, label),
		Extra: map[string]any{
			"email":    user.Email,
			"uuid":     user.UUID,
			"protocol": "vless-reality",
		},
	}, nil
}

// AddPeerWithID adds the user using a caller-supplied UUID — used by the bot
// to "move" a user between tiers (e.g. base → base-slow on quota throttle).
// Email is "<label>@vpn".
func (s *VLESSService) AddPeerWithID(id, label string) (*Peer, error) {
	email := label + "@vpn"
	user, err := s.mgr.AddUserWithUUID(id, email)
	if err != nil {
		return nil, err
	}
	return &Peer{
		ID:     user.UUID,
		Label:  label,
		Config: s.buildURL(user.UUID, label),
		Extra: map[string]any{
			"email":    user.Email,
			"uuid":     user.UUID,
			"protocol": "vless-reality",
		},
	}, nil
}

func (s *VLESSService) RemovePeer(id string) error {
	s.mu.Lock()
	delete(s.suspended, id)
	s.mu.Unlock()
	return s.mgr.RemoveUser(id)
}

func (s *VLESSService) ListPeers() ([]*Peer, error) {
	users, err := s.mgr.ListUsers()
	if err != nil {
		return nil, err
	}

	peers := make([]*Peer, 0, len(users))
	for _, u := range users {
		stats, _ := s.mgr.GetUserStats(u.Email)
		var rx, tx int64
		if stats != nil {
			// Xray: Uplink = bytes received FROM the client (server's rx).
			//        Downlink = bytes sent TO the client (server's tx).
			rx = stats.Uplink
			tx = stats.Downlink
		}
		s.mu.Lock()
		_, isSuspended := s.suspended[u.UUID]
		s.mu.Unlock()

		peers = append(peers, &Peer{
			ID:        u.UUID,
			Label:     strings.TrimSuffix(u.Email, "@vpn"),
			Config:    s.buildURL(u.UUID, u.Email),
			Suspended: isSuspended,
			RxBytes:   rx,
			TxBytes:   tx,
			Extra: map[string]any{
				"email":    u.Email,
				"protocol": "vless-reality",
			},
		})
	}
	return peers, nil
}

// SuspendPeer removes the user from live Xray (and config). Tracks state for Resume.
// Note: the UUID/email pair must be in the suspended map for resume to work — so we save here.
func (s *VLESSService) SuspendPeer(id string) error {
	users, err := s.mgr.ListUsers()
	if err != nil {
		return err
	}
	var email string
	for _, u := range users {
		if u.UUID == id {
			email = u.Email
			break
		}
	}
	if email == "" {
		return nil // already absent
	}

	s.mu.Lock()
	s.suspended[id] = VLESSSuspended{UUID: id, Email: email}
	s.mu.Unlock()

	if err := s.mgr.RemoveUser(id); err != nil {
		// Roll back the in-memory record — the peer is still live
		s.mu.Lock()
		delete(s.suspended, id)
		s.mu.Unlock()
		return err
	}
	s.saveState()
	return nil
}

func (s *VLESSService) ResumePeer(id string) error {
	s.mu.Lock()
	susp, ok := s.suspended[id]
	s.mu.Unlock()
	if !ok {
		// Suspended map is empty after a restart but the user was already removed
		// from Xray config. Re-add with best-effort email; stats reset but access
		// is restored. Bot has the real UUID, which is what matters for routing.
		susp = VLESSSuspended{UUID: id, Email: id + "@vpn"}
	}

	if _, err := s.mgr.AddUserWithUUID(susp.UUID, susp.Email); err != nil {
		return err
	}

	s.mu.Lock()
	delete(s.suspended, id)
	s.mu.Unlock()
	s.saveState()
	return nil
}

func (s *VLESSService) SuspendAll(ids []string) error {
	var errs []string
	for _, id := range ids {
		if err := s.SuspendPeer(id); err != nil {
			errs = append(errs, fmt.Sprintf("%s: %v", id, err))
		}
	}
	if len(errs) > 0 {
		return fmt.Errorf("SuspendAll partial failure (%d/%d): %s",
			len(errs), len(ids), strings.Join(errs, "; "))
	}
	return nil
}

func (s *VLESSService) ResumeAll(ids []string) error {
	var errs []string
	for _, id := range ids {
		if err := s.ResumePeer(id); err != nil {
			errs = append(errs, fmt.Sprintf("%s: %v", id, err))
		}
	}
	if len(errs) > 0 {
		return fmt.Errorf("ResumeAll partial failure (%d/%d): %s",
			len(errs), len(ids), strings.Join(errs, "; "))
	}
	return nil
}

func (s *VLESSService) Info() map[string]any {
	return map[string]any{
		"type":    "vless-reality",
		"host":    s.conn.Host,
		"port":    s.conn.Port,
		"sni":     s.conn.SNI,
		"flow":    s.conn.Flow,
		"pubkey":  s.conn.PubKey,
		"shortid": s.conn.ShortID,
	}
}

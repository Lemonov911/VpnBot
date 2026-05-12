package service

import (
	"errors"
	"fmt"
	"net/url"
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
	mgr  *xray.Manager
	conn VLESSConnection

	mu        sync.Mutex
	suspended map[string]VLESSSuspended // key = uuid
}

type VLESSSuspended struct {
	UUID  string
	Email string
}

func NewVLESSService(mgr *xray.Manager, conn VLESSConnection) *VLESSService {
	if conn.FP == "" {
		conn.FP = "chrome"
	}
	// Note: empty conn.Flow is intentional — vless-max / vless-max-slow run
	// plain VLESS without xtls-rprx-vision. Don't set a default here.
	return &VLESSService{
		mgr:       mgr,
		conn:      conn,
		suspended: make(map[string]VLESSSuspended),
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
			rx = stats.Downlink
			tx = stats.Uplink
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

	return s.mgr.RemoveUser(id)
}

func (s *VLESSService) ResumePeer(id string) error {
	s.mu.Lock()
	susp, ok := s.suspended[id]
	s.mu.Unlock()
	if !ok {
		return errors.New("peer is not suspended (or vpnctl was restarted; resume needs restart-resilient state)")
	}

	_, err := s.mgr.AddUserWithUUID(susp.UUID, susp.Email)
	if err != nil {
		return err
	}

	s.mu.Lock()
	delete(s.suspended, id)
	s.mu.Unlock()
	return nil
}

func (s *VLESSService) SuspendAll(ids []string) error {
	for _, id := range ids {
		if err := s.SuspendPeer(id); err != nil {
			return err
		}
	}
	return nil
}

func (s *VLESSService) ResumeAll(ids []string) error {
	for _, id := range ids {
		if err := s.ResumePeer(id); err != nil {
			return err
		}
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

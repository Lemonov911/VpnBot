package service

import "time"

type Peer struct {
	ID        string         `json:"id"`
	Label     string         `json:"label"`
	Config    string         `json:"config,omitempty"`
	Suspended bool           `json:"suspended"`
	RxBytes   int64          `json:"rx_bytes"`
	TxBytes   int64          `json:"tx_bytes"`
	LastSeen  time.Time      `json:"last_seen,omitempty"`
	CreatedAt time.Time      `json:"created_at,omitempty"`
	Extra     map[string]any `json:"extra,omitempty"`
}

type Service interface {
	AddPeer(label string) (*Peer, error)
	RemovePeer(id string) error
	ListPeers() ([]*Peer, error)
	SuspendPeer(id string) error
	ResumePeer(id string) error
	SuspendAll(ids []string) error
	ResumeAll(ids []string) error
	Info() map[string]any
}

// PeerWithIDAdder is an optional capability — services that can add a peer
// with a caller-supplied ID (e.g. to keep VLESS UUIDs across tier moves)
// implement this. Use a type assertion in handlers.
type PeerWithIDAdder interface {
	AddPeerWithID(id, label string) (*Peer, error)
}
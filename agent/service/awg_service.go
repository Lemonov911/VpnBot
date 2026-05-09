package service

import (
	awgpkg "vpnctl/awg"
)

// AWGService — обёртка для AmneziaWG, аналогичная WGService но с обфускацией.
// Параметры обфускации (Jc/H1-H4/S1-S4) устанавливаются один раз скриптом
// agent/scripts/awg-install.sh — каждый сервер уникален.
type AWGService struct {
	mgr *awgpkg.Manager
}

func NewAWGService(mgr *awgpkg.Manager) *AWGService {
	return &AWGService{mgr: mgr}
}

func (s *AWGService) AddPeer(label string) (*Peer, error) {
	awgPeer, clientCfg, err := s.mgr.AddPeer(label)
	if err != nil {
		return nil, err
	}
	return &Peer{
		ID:        awgPeer.PublicKey,
		Label:     awgPeer.Label,
		Config:    awgpkg.AmneziaWGConfig(clientCfg),
		Suspended: awgPeer.Suspended,
		RxBytes:   awgPeer.RxBytes,
		TxBytes:   awgPeer.TxBytes,
		LastSeen:  awgPeer.LastSeen,
		CreatedAt: awgPeer.CreatedAt,
		Extra: map[string]any{
			"public_key":  awgPeer.PublicKey,
			"assigned_ip": awgPeer.AssignedIP,
		},
	}, nil
}

func (s *AWGService) RemovePeer(id string) error {
	return s.mgr.RemovePeer(id)
}

func (s *AWGService) ListPeers() ([]*Peer, error) {
	awgPeers, err := s.mgr.Stats()
	if err != nil {
		return nil, err
	}
	peers := make([]*Peer, len(awgPeers))
	for i, p := range awgPeers {
		peers[i] = &Peer{
			ID:        p.PublicKey,
			Label:     p.Label,
			Suspended: p.Suspended,
			RxBytes:   p.RxBytes,
			TxBytes:   p.TxBytes,
			LastSeen:  p.LastSeen,
			CreatedAt: p.CreatedAt,
			Extra: map[string]any{
				"public_key":  p.PublicKey,
				"assigned_ip": p.AssignedIP,
			},
		}
	}
	return peers, nil
}

// Suspend/Resume — пока не реализованы для AWG (можно через ReplaceAllowedIPs).
func (s *AWGService) SuspendPeer(id string) error  { return nil }
func (s *AWGService) ResumePeer(id string) error   { return nil }
func (s *AWGService) SuspendAll(ids []string) error { return nil }
func (s *AWGService) ResumeAll(ids []string) error  { return nil }

func (s *AWGService) Info() map[string]any {
	return map[string]any{
		"type":       "amneziawg",
		"interface":  s.mgr.Interface(),
		"endpoint":   s.mgr.Endpoint(),
		"public_key": s.mgr.ServerPublicKey(),
	}
}

func (s *AWGService) Manager() *awgpkg.Manager {
	return s.mgr
}

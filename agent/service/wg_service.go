package service

import (
	vpnctlwg "vpnctl/wg"
)

type WGService struct {
	mgr *vpnctlwg.Manager
}

func NewWGService(mgr *vpnctlwg.Manager) *WGService {
	return &WGService{mgr: mgr}
}

func (s *WGService) AddPeer(label string) (*Peer, error) {
	wgPeer, clientCfg, err := s.mgr.AddPeer(label)
	if err != nil {
		return nil, err
	}
	return &Peer{
		ID:        wgPeer.PublicKey,
		Label:     wgPeer.Label,
		Config:    vpnctlwg.WireGuardConfig(clientCfg),
		Suspended: wgPeer.Suspended,
		RxBytes:   wgPeer.RxBytes,
		TxBytes:   wgPeer.TxBytes,
		LastSeen:  wgPeer.LastSeen,
		CreatedAt: wgPeer.CreatedAt,
		Extra: map[string]any{
			"public_key":  wgPeer.PublicKey,
			"assigned_ip": wgPeer.AssignedIP,
		},
	}, nil
}

func (s *WGService) RemovePeer(id string) error {
	return s.mgr.RemovePeer(id)
}

func (s *WGService) ListPeers() ([]*Peer, error) {
	wgPeers, err := s.mgr.Stats()
	if err != nil {
		return nil, err
	}
	peers := make([]*Peer, len(wgPeers))
	for i, p := range wgPeers {
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

func (s *WGService) SuspendPeer(id string) error {
	return s.mgr.SuspendPeer(id)
}

func (s *WGService) ResumePeer(id string) error {
	return s.mgr.ResumePeer(id)
}

func (s *WGService) SuspendAll(ids []string) error {
	return s.mgr.SuspendAll(ids)
}

func (s *WGService) ResumeAll(ids []string) error {
	return s.mgr.ResumeAll(ids)
}

func (s *WGService) Info() map[string]any {
	return map[string]any{
		"type":       "wireguard",
		"interface":  s.mgr.Interface(),
		"public_key": s.mgr.ServerPublicKey(),
	}
}

func (s *WGService) Manager() *vpnctlwg.Manager {
	return s.mgr
}
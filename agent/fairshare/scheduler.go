package fairshare

import (
	"fmt"
	"log"
	"os/exec"
	"time"
)

// WGManager is the interface we need from wg.Manager.
type WGManager interface {
	ActivePeerCount() int
	// PeerIPs returns list of assigned IPs for non-suspended peers.
	PeerIPs() []string
}

type Scheduler struct {
	iface      string
	totalMbit  int
	minMbit    int
	interval   time.Duration
	mgr        WGManager
	lastCount  int
}

func NewScheduler(iface string, totalMbit, minMbit, intervalSec int, mgr WGManager) *Scheduler {
	return &Scheduler{
		iface:     iface,
		totalMbit: totalMbit,
		minMbit:   minMbit,
		interval:  time.Duration(intervalSec) * time.Second,
		mgr:       mgr,
	}
}

func (s *Scheduler) Run() {
	ticker := time.NewTicker(s.interval)
	defer ticker.Stop()

	log.Printf("fairshare: started (total=%d Mbit, min=%d Mbit, interval=%s)",
		s.totalMbit, s.minMbit, s.interval)

	for range ticker.C {
		s.recalc()
	}
}

func (s *Scheduler) recalc() {
	active := s.mgr.ActivePeerCount()
	if active == 0 {
		if s.lastCount != 0 {
			log.Printf("fairshare: no active peers, clearing tc rules")
			s.clearTC()
		}
		s.lastCount = 0
		return
	}

	perPeer := s.totalMbit / active
	if perPeer < s.minMbit {
		perPeer = s.minMbit
	}

	if active == s.lastCount {
		return // nothing changed
	}

	log.Printf("fairshare: %d active peers → %d Mbit each", active, perPeer)

	ips := s.mgr.PeerIPs()
	if err := s.applyTC(ips, perPeer); err != nil {
		log.Printf("fairshare: tc error: %v", err)
	}
	s.lastCount = active
}

// applyTC sets up HTB qdisc with one class per peer IP.
// Each class gets perPeer Mbit/s ceiling, minMbit guaranteed.
func (s *Scheduler) applyTC(peerIPs []string, perPeerMbit int) error {
	iface := s.iface

	// Reset qdisc
	exec.Command("tc", "qdisc", "del", "dev", iface, "root").Run()

	// Root HTB qdisc
	if err := run("tc", "qdisc", "add", "dev", iface, "root", "handle", "1:", "htb", "default", "999"); err != nil {
		return fmt.Errorf("add root qdisc: %w", err)
	}

	// Root class — full bandwidth
	totalKbit := s.totalMbit * 1000
	if err := run("tc", "class", "add", "dev", iface, "parent", "1:", "classid", "1:1",
		"htb", "rate", fmt.Sprintf("%dkbit", totalKbit), "burst", "15k"); err != nil {
		return fmt.Errorf("add root class: %w", err)
	}

	// Default class (unclassified traffic — server itself etc)
	run("tc", "class", "add", "dev", iface, "parent", "1:1", "classid", "1:999",
		"htb", "rate", fmt.Sprintf("%dkbit", totalKbit), "burst", "15k")

	perKbit := perPeerMbit * 1000
	minKbit := s.minMbit * 1000

	for i, ip := range peerIPs {
		classID := fmt.Sprintf("1:%d", 10+i)

		// Class per peer
		run("tc", "class", "add", "dev", iface, "parent", "1:1", "classid", classID,
			"htb",
			"rate", fmt.Sprintf("%dkbit", minKbit),
			"ceil", fmt.Sprintf("%dkbit", perKbit),
			"burst", "15k")

		// SFQ leaf qdisc for fairness within the class
		run("tc", "qdisc", "add", "dev", iface, "parent", classID,
			"handle", fmt.Sprintf("%d:", 10+i), "sfq", "perturb", "10")

		// Filter: match peer IP → class
		run("tc", "filter", "add", "dev", iface, "parent", "1:0", "protocol", "ip",
			"u32", "match", "ip", "dst", ip+"/32", "flowid", classID)
	}

	return nil
}

func (s *Scheduler) clearTC() {
	exec.Command("tc", "qdisc", "del", "dev", s.iface, "root").Run()
}

func run(args ...string) error {
	out, err := exec.Command(args[0], args[1:]...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%v: %s", err, out)
	}
	return nil
}

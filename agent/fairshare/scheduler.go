package fairshare

import (
	"context"
	"fmt"
	"log"
	"os/exec"
	"sort"
	"strings"
	"time"
)

// WGManager is the interface we need from wg.Manager.
type WGManager interface {
	ActivePeerCount() int
	// PeerIPs returns list of assigned IPs for non-suspended peers.
	PeerIPs() []string
}

type Scheduler struct {
	iface     string
	totalMbit int
	minMbit   int
	interval  time.Duration
	mgr       WGManager
	lastKey   string
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

func (s *Scheduler) Run(ctx context.Context) {
	ticker := time.NewTicker(s.interval)
	defer ticker.Stop()

	log.Printf("fairshare: started (total=%d Mbit, min=%d Mbit, interval=%s)",
		s.totalMbit, s.minMbit, s.interval)

	for {
		select {
		case <-ctx.Done():
			log.Println("fairshare: stopped")
			return
		case <-ticker.C:
			s.recalc()
		}
	}
}

func (s *Scheduler) recalc() {
	ips := s.mgr.PeerIPs()
	sort.Strings(ips)
	key := strings.Join(ips, ",")

	active := len(ips)
	if active == 0 {
		if s.lastKey != "" {
			log.Printf("fairshare: no active peers, clearing tc rules")
			s.clearTC()
		}
		s.lastKey = ""
		return
	}

	perPeer := s.totalMbit / active
	remainder := s.totalMbit % active
	if perPeer < s.minMbit {
		perPeer = s.minMbit
		remainder = 0
	}

	if key == s.lastKey {
		return // nothing changed
	}

	log.Printf("fairshare: %d active peers → %d Mbit each (+1 to first %d)", active, perPeer, remainder)

	if err := s.applyTC(ips, perPeer, remainder); err != nil {
		log.Printf("fairshare: tc error: %v", err)
	}
	s.lastKey = key
}

// applyTC sets up HTB qdisc with one class per peer IP.
// Each class gets perPeer Mbit/s ceiling, minMbit guaranteed.
func (s *Scheduler) applyTC(peerIPs []string, perPeerMbit, remainder int) error {
	iface := s.iface

	// Reset qdisc — best-effort, may not exist on first run.
	if out, err := exec.Command("tc", "qdisc", "del", "dev", iface, "root").CombinedOutput(); err != nil {
		// Not fatal: missing qdisc is normal at startup; log for visibility.
		log.Printf("fairshare: tc qdisc del (initial): %v: %s", err, strings.TrimSpace(string(out)))
	}

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
	runLogged("tc", "class", "add", "dev", iface, "parent", "1:1", "classid", "1:999",
		"htb", "rate", fmt.Sprintf("%dkbit", totalKbit), "burst", "15k")

	minKbit := s.minMbit * 1000

	for i, ip := range peerIPs {
		classID := fmt.Sprintf("1:%d", 10+i)

		peerMbit := perPeerMbit
		if i < remainder {
			peerMbit = perPeerMbit + 1
		}
		perKbit := peerMbit * 1000

		// Class per peer
		runLogged("tc", "class", "add", "dev", iface, "parent", "1:1", "classid", classID,
			"htb",
			"rate", fmt.Sprintf("%dkbit", minKbit),
			"ceil", fmt.Sprintf("%dkbit", perKbit),
			"burst", "15k")

		// SFQ leaf qdisc for fairness within the class
		runLogged("tc", "qdisc", "add", "dev", iface, "parent", classID,
			"handle", fmt.Sprintf("%d:", 10+i), "sfq", "perturb", "10")

		// Filter: match peer IP → class
		runLogged("tc", "filter", "add", "dev", iface, "parent", "1:0", "protocol", "ip",
			"u32", "match", "ip", "dst", ip+"/32", "flowid", classID)
	}

	return nil
}

func (s *Scheduler) clearTC() {
	if out, err := exec.Command("tc", "qdisc", "del", "dev", s.iface, "root").CombinedOutput(); err != nil {
		log.Printf("fairshare: tc clear qdisc: %v: %s", err, strings.TrimSpace(string(out)))
	}
}

// run returns an error including tc's stderr. Use when the caller can act on
// failure (e.g. abort applyTC if the root class can't be created).
func run(args ...string) error {
	out, err := exec.Command(args[0], args[1:]...).CombinedOutput()
	if err != nil {
		// %w preserves the error chain so callers can errors.Is/As against
		// *exec.ExitError; %v dropped that.
		return fmt.Errorf("%w: %s", err, out)
	}
	return nil
}

// runLogged is fire-and-log: best-effort tc commands where individual peer
// class/filter failures shouldn't abort the whole reconfig — we just need
// observability in journalctl.
func runLogged(args ...string) {
	if out, err := exec.Command(args[0], args[1:]...).CombinedOutput(); err != nil {
		log.Printf("fairshare: tc %s failed: %v: %s", strings.Join(args, " "), err, strings.TrimSpace(string(out)))
	}
}

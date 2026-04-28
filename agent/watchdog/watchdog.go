package watchdog

import (
	"fmt"
	"log"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type Watchdog struct {
	botToken  string
	adminIDs  []int64
	checkAddr string // e.g. http://127.0.0.1:9000/health
	interval  time.Duration
	client    *http.Client
	wasDown   bool
}

func New(botToken string, adminIDs []int64, checkAddr string) *Watchdog {
	return &Watchdog{
		botToken:  botToken,
		adminIDs:  adminIDs,
		checkAddr: checkAddr,
		interval:  60 * time.Second,
		client:    &http.Client{Timeout: 5 * time.Second},
	}
}

func (w *Watchdog) Run() {
	if w.botToken == "" || len(w.adminIDs) == 0 {
		log.Println("watchdog: no bot token or admin IDs, disabled")
		return
	}

	log.Printf("watchdog: started (interval=%s, target=%s)", w.interval, w.checkAddr)
	ticker := time.NewTicker(w.interval)
	defer ticker.Stop()

	for range ticker.C {
		w.check()
	}
}

func (w *Watchdog) check() {
	resp, err := w.client.Get(w.checkAddr)
	if err != nil || resp.StatusCode != http.StatusOK {
		if !w.wasDown {
			msg := fmt.Sprintf("🚨 *VPN Agent DOWN*\n`%s`\nError: `%v`", w.checkAddr, err)
			w.notify(msg)
			w.wasDown = true
		}
		return
	}

	if w.wasDown {
		msg := fmt.Sprintf("✅ *VPN Agent RESTORED*\n`%s` is back online", w.checkAddr)
		w.notify(msg)
		w.wasDown = false
	}
}

func (w *Watchdog) notify(text string) {
	for _, id := range w.adminIDs {
		apiURL := fmt.Sprintf("https://api.telegram.org/bot%s/sendMessage", w.botToken)
		body := url.Values{
			"chat_id":    {fmt.Sprintf("%d", id)},
			"text":       {text},
			"parse_mode": {"Markdown"},
		}
		resp, err := w.client.Post(apiURL, "application/x-www-form-urlencoded",
			strings.NewReader(body.Encode()))
		if err != nil {
			log.Printf("watchdog: telegram notify error: %v", err)
		} else {
			resp.Body.Close()
		}
	}
}

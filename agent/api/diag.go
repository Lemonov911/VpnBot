package api

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"time"
)

// HandleDiag возвращает диагностический snapshot agent-сервера: tc-state,
// Xray inbounds (с SNI/dest для Reality troubleshooting), outbound DNS+TCP
// тест для фронтинг-хостов.
//
// Нужен когда у нас нет SSH к серверу (Charlotte), но есть HMAC-доступ к
// agent'у. Аналог `cat /etc/xray/config.json && tc class show dev eth0 &&
// curl https://<sni>:443` без шелла.
//
// Auth: тот же authMiddleware что и остальные admin-endpoint'ы (HMAC).
type DiagConfig struct {
	XrayConfigPath string
	TCShapeIface   string
}

type tcInfo struct {
	Qdisc   []string `json:"qdisc"`
	Classes []string `json:"classes"`
	Filters []string `json:"filters"`
}

type inboundInfo struct {
	Tag          string `json:"tag"`
	Listen       string `json:"listen,omitempty"`
	Port         int    `json:"port"`
	Protocol     string `json:"protocol"`
	RealityDest  string `json:"reality_dest,omitempty"`
	RealityNames []string `json:"reality_server_names,omitempty"`
	RealityShortIDs []string `json:"reality_short_ids,omitempty"`
	ClientCount  int    `json:"client_count"`
}

type frontingCheck struct {
	Host         string `json:"host"`
	DNSResolved  []string `json:"dns,omitempty"`
	DNSError     string `json:"dns_error,omitempty"`
	TCPReachable bool   `json:"tcp_443,omitempty"`
	TCPError     string `json:"tcp_443_error,omitempty"`
	LatencyMS    int64  `json:"tcp_443_latency_ms,omitempty"`
}

type diagResponse struct {
	Iface     string          `json:"iface"`
	TC        tcInfo          `json:"tc"`
	Inbounds  []inboundInfo   `json:"xray_inbounds"`
	Fronting  []frontingCheck `json:"fronting_checks"`
	BinaryMD5 string          `json:"binary_md5,omitempty"`  // sanity check какой бинарь крутится
}

func (s *Server) HandleDiag(cfg DiagConfig) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		resp := diagResponse{
			Iface: cfg.TCShapeIface,
		}

		// === tc state ===
		runTC := func(args ...string) []string {
			out, err := exec.Command("tc", args...).Output()
			if err != nil {
				return []string{"<error>: " + err.Error()}
			}
			lines := strings.Split(strings.TrimRight(string(out), "\n"), "\n")
			return lines
		}
		resp.TC.Qdisc = runTC("qdisc", "show", "dev", cfg.TCShapeIface)
		// `-s` = со статистикой (Sent/dropped/overlimits) — нужно для измерения
		// действительно ли throttle применяется к юзерскому трафику или нет.
		resp.TC.Classes = runTC("-s", "class", "show", "dev", cfg.TCShapeIface)
		resp.TC.Filters = runTC("filter", "show", "dev", cfg.TCShapeIface)

		// === xray config ===
		// Парсим только relevant части (inbounds: tag, port, listen,
		// streamSettings.realitySettings.dest/serverNames/shortIds,
		// settings.clients count). Большая config'а не нужна.
		if cfg.XrayConfigPath != "" {
			data, err := os.ReadFile(cfg.XrayConfigPath)
			if err == nil {
				var raw struct {
					Inbounds []struct {
						Tag      string `json:"tag"`
						Listen   string `json:"listen"`
						Port     int    `json:"port"`
						Protocol string `json:"protocol"`
						Settings struct {
							Clients []map[string]any `json:"clients"`
						} `json:"settings"`
						StreamSettings struct {
							RealitySettings struct {
								Dest        string   `json:"dest"`
								ServerNames []string `json:"serverNames"`
								ShortIds    []string `json:"shortIds"`
							} `json:"realitySettings"`
						} `json:"streamSettings"`
					} `json:"inbounds"`
				}
				if json.Unmarshal(data, &raw) == nil {
					for _, ib := range raw.Inbounds {
						resp.Inbounds = append(resp.Inbounds, inboundInfo{
							Tag:             ib.Tag,
							Listen:          ib.Listen,
							Port:            ib.Port,
							Protocol:        ib.Protocol,
							RealityDest:     ib.StreamSettings.RealitySettings.Dest,
							RealityNames:    ib.StreamSettings.RealitySettings.ServerNames,
							RealityShortIDs: ib.StreamSettings.RealitySettings.ShortIds,
							ClientCount:     len(ib.Settings.Clients),
						})
					}
				}
			}
		}

		// === fronting hosts check ===
		// Собираем уникальные dest'ы из всех reality inbound'ов + проверяем
		// каждый. Если Charlotte не может dial-out до addons.mozilla.org —
		// Reality handshake падает, юзер видит EOF.
		hosts := map[string]bool{}
		for _, ib := range resp.Inbounds {
			if ib.RealityDest != "" {
				// "addons.mozilla.org:443" → "addons.mozilla.org"
				host := strings.SplitN(ib.RealityDest, ":", 2)[0]
				hosts[host] = true
			}
		}
		ctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
		defer cancel()
		for host := range hosts {
			fc := frontingCheck{Host: host}
			// DNS resolve
			resolver := net.Resolver{}
			ips, err := resolver.LookupHost(ctx, host)
			if err != nil {
				fc.DNSError = err.Error()
			} else {
				fc.DNSResolved = ips
				// TCP dial на :443 если DNS прошёл
				start := time.Now()
				dialer := net.Dialer{Timeout: 5 * time.Second}
				conn, err := dialer.DialContext(ctx, "tcp", host+":443")
				if err != nil {
					fc.TCPError = err.Error()
				} else {
					conn.Close()
					fc.TCPReachable = true
					fc.LatencyMS = time.Since(start).Milliseconds()
				}
			}
			resp.Fronting = append(resp.Fronting, fc)
		}

		// Sanity: md5 of own binary — confirm we're running the version we
		// think we are (was self-update applied? правильный ли binary?).
		if exe, err := os.Readlink("/proc/self/exe"); err == nil {
			if out, err := exec.Command("md5sum", exe).Output(); err == nil {
				if parts := strings.Fields(string(out)); len(parts) > 0 {
					resp.BinaryMD5 = parts[0]
				}
			}
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}
}

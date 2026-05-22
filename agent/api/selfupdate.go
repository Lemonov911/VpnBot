package api

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

// SelfUpdateConfig — конфиг endpoint'а самообновления.
// Зачем нужен: один раз обновить агент на новой VPS через SSH/web-console,
// дальше — все будущие обновления remote через этот endpoint. Идея: бот
// (или admin tool) postит {url, sha256}, агент скачивает + verify + swap
// + systemctl restart self-service.
//
// Security: endpoint защищён обычным HMAC middleware (authMiddleware,
// тот же что охраняет add_peer/remove_peer). Дополнительная защита —
// SHA256 хеш скачанного бинаря должен совпадать с переданным. Если
// атакующий смог обойти HMAC, то checksum-проверка ловит подмену binary.
type SelfUpdateConfig struct {
	// Имя systemd-сервиса для рестарта после swap'а.
	// На текущей инфре сервис называется vpnctl-awg.service.
	ServiceName string
}

// selfUpdateReq — тело POST /admin/self-update.
type selfUpdateReq struct {
	// URL откуда скачать новый бинарь. Должен быть достижим с агент-сервера.
	// Обычно http://<bot-vps>:<port>/path/vpnctl_awg
	URL string `json:"url"`
	// Hex-encoded SHA256 ожидаемого бинаря (64 hex chars). Обязательно.
	SHA256 string `json:"sha256"`
}

// HandleSelfUpdate возвращает HTTP handler, который скачивает новый бинарь,
// верифицирует SHA256, swap'ит на месте и просит systemd рестарт.
//
// Сигнатура endpoint'а:
//
//	POST /admin/self-update
//	{
//	    "url":    "http://151.243.113.31:8181/vpnctl_awg",
//	    "sha256": "abc123..."  (64 hex chars)
//	}
//
// Ответ 200 шлётся ДО рестарта, через 1с агент делает `systemctl restart`
// собственного сервиса. Systemd сразу же поднимает новый процесс.
func (s *Server) HandleSelfUpdate(cfg SelfUpdateConfig) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req selfUpdateReq
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			jsonError(w, "bad json", http.StatusBadRequest)
			return
		}
		if req.URL == "" {
			jsonError(w, "url required", http.StatusBadRequest)
			return
		}
		if len(req.SHA256) != 64 {
			jsonError(w, "sha256 must be 64 hex chars", http.StatusBadRequest)
			return
		}

		// Path к текущему бинарю — берём через /proc/self/exe, так
		// корректнее чем os.Args[0] (последнее может быть relative).
		currentBin, err := os.Readlink("/proc/self/exe")
		if err != nil {
			log.Printf("self-update: readlink /proc/self/exe: %v", err)
			jsonError(w, "cannot resolve own binary path", http.StatusInternalServerError)
			return
		}

		// Скачиваем во временный файл В ТОМ ЖЕ каталоге что target —
		// иначе os.Rename падает с EXDEV между разными mount'ами (/tmp →
		// /usr/local/bin часто разные fs в контейнерах).
		dir := filepath.Dir(currentBin)
		tmpFile, err := os.CreateTemp(dir, ".vpnctl-update-*")
		if err != nil {
			log.Printf("self-update: tempfile in %s: %v", dir, err)
			jsonError(w, "tempfile create failed", http.StatusInternalServerError)
			return
		}
		tmpPath := tmpFile.Name()
		cleanupTmp := func() {
			tmpFile.Close()
			os.Remove(tmpPath)
		}

		// HTTP GET с разумным timeout'ом — иначе зависший CDN держит handler
		// открытым и блокирует subsequent admin operations.
		client := &http.Client{Timeout: 5 * time.Minute}
		resp, err := client.Get(req.URL)
		if err != nil {
			cleanupTmp()
			log.Printf("self-update: download %s: %v", req.URL, err)
			jsonError(w, fmt.Sprintf("download failed: %v", err), http.StatusBadGateway)
			return
		}
		defer resp.Body.Close()
		if resp.StatusCode != 200 {
			cleanupTmp()
			jsonError(w, fmt.Sprintf("download http %d", resp.StatusCode), http.StatusBadGateway)
			return
		}

		// Streaming SHA256 — не загружаем весь бинарь в RAM (8+ MB).
		hasher := sha256.New()
		n, err := io.Copy(io.MultiWriter(tmpFile, hasher), resp.Body)
		if err != nil {
			cleanupTmp()
			log.Printf("self-update: copy: %v", err)
			jsonError(w, "download stream error", http.StatusBadGateway)
			return
		}
		tmpFile.Close()
		gotSHA := hex.EncodeToString(hasher.Sum(nil))
		if gotSHA != req.SHA256 {
			cleanupTmp()
			log.Printf("self-update: checksum mismatch url=%s expected=%s got=%s size=%d",
				req.URL, req.SHA256, gotSHA, n)
			jsonError(w, "sha256 mismatch — refusing swap", http.StatusBadRequest)
			return
		}

		// Делаем executable.
		if err := os.Chmod(tmpPath, 0o755); err != nil {
			cleanupTmp()
			log.Printf("self-update: chmod: %v", err)
			jsonError(w, "chmod failed", http.StatusInternalServerError)
			return
		}

		// Backup текущего бинаря — last-known-good для отката.
		// На staging Amsterdam уже видели несколько .old.<ts> накопленных —
		// очистку оставляем для cron или ручного управления (для отката важна
		// history глубже чем 1 версия).
		backupPath := fmt.Sprintf("%s.old.%d", currentBin, time.Now().Unix())
		if err := os.Rename(currentBin, backupPath); err != nil {
			cleanupTmp()
			log.Printf("self-update: backup rename %s → %s: %v",
				currentBin, backupPath, err)
			jsonError(w, "backup failed", http.StatusInternalServerError)
			return
		}

		// Swap: tmp → current.
		if err := os.Rename(tmpPath, currentBin); err != nil {
			// Catastrophic — мы только что снесли текущий бинарь. Пытаемся откатить.
			restoreErr := os.Rename(backupPath, currentBin)
			log.Printf("self-update: SWAP FAILED %s → %s: %v (restore: %v)",
				tmpPath, currentBin, err, restoreErr)
			cleanupTmp()
			jsonError(w, "swap failed (rolled back)", http.StatusInternalServerError)
			return
		}

		log.Printf("self-update: binary swapped ok (%d bytes, sha256=%s, backup=%s)",
			n, gotSHA, backupPath)

		// Респонсим клиенту PERED рестартом — иначе соединение разорвётся
		// до того как клиент прочтёт «ok».
		jsonOK(w, map[string]any{
			"status":  "ok",
			"message": "binary swapped, restarting in 2s",
			"bytes":   n,
			"sha256":  gotSHA,
			"backup":  backupPath,
		})

		// Flush response чтобы убедиться что клиент получил подтверждение
		// до того как systemd прибил нас.
		if f, ok := w.(http.Flusher); ok {
			f.Flush()
		}

		// Рестарт через 2с — даём время и клиенту-боту прочитать ответ, и
		// текущим in-flight handler'ам завершиться. systemctl restart killит
		// текущий процесс и поднимает новый (Restart=always в unit'е).
		go func(svc string) {
			time.Sleep(2 * time.Second)
			out, err := exec.Command("systemctl", "restart", svc).CombinedOutput()
			if err != nil {
				log.Printf("self-update: systemctl restart %s: %v: %s",
					svc, err, out)
				// Если systemctl не сработал — exit, systemd Restart=always
				// поднимет нас с новым бинарём.
				os.Exit(0)
			}
		}(cfg.ServiceName)
	}
}

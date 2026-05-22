//go:build linux

// Self-update тесты Linux-only — handler читает /proc/self/exe.  На macOS
// (dev-машина разработчика) этого файла нет, тесты бы fail'или на readlink
// до того как дошли бы до проверяемой логики.  В production agent крутится
// под Linux, так что покрытие сохраняется.

package api

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSelfUpdate_BadJSON(t *testing.T) {
	s := &Server{selfUpdateServiceName: "test.service"}
	handler := s.HandleSelfUpdate(SelfUpdateConfig{ServiceName: "test"})

	rr := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/admin/self-update",
		strings.NewReader("not json"))
	handler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for bad json, got %d", rr.Code)
	}
}

func TestSelfUpdate_MissingURL(t *testing.T) {
	s := &Server{selfUpdateServiceName: "test.service"}
	handler := s.HandleSelfUpdate(SelfUpdateConfig{ServiceName: "test"})

	body, _ := json.Marshal(map[string]string{"sha256": strings.Repeat("a", 64)})
	rr := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/admin/self-update", strings.NewReader(string(body)))
	handler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing url, got %d: %s", rr.Code, rr.Body.String())
	}
	if !strings.Contains(rr.Body.String(), "url") {
		t.Errorf("error should mention 'url', got: %s", rr.Body.String())
	}
}

func TestSelfUpdate_BadSHA256Length(t *testing.T) {
	s := &Server{selfUpdateServiceName: "test.service"}
	handler := s.HandleSelfUpdate(SelfUpdateConfig{ServiceName: "test"})

	cases := []struct {
		name   string
		sha256 string
	}{
		{"empty", ""},
		{"too_short", "abc"},
		{"63_chars", strings.Repeat("a", 63)},
		{"65_chars", strings.Repeat("a", 65)},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			body, _ := json.Marshal(map[string]string{
				"url": "http://example.com/bin", "sha256": c.sha256,
			})
			rr := httptest.NewRecorder()
			req := httptest.NewRequest("POST", "/admin/self-update",
				strings.NewReader(string(body)))
			handler(rr, req)
			if rr.Code != http.StatusBadRequest {
				t.Errorf("[%s] expected 400, got %d", c.name, rr.Code)
			}
		})
	}
}

func TestSelfUpdate_DownloadFails(t *testing.T) {
	s := &Server{selfUpdateServiceName: "test.service"}
	handler := s.HandleSelfUpdate(SelfUpdateConfig{ServiceName: "test"})

	body, _ := json.Marshal(map[string]string{
		// Заведомо мёртвый URL — должен dial fail
		"url":    "http://127.0.0.1:1/nope",
		"sha256": strings.Repeat("a", 64),
	})
	rr := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/admin/self-update",
		strings.NewReader(string(body)))
	handler(rr, req)

	// Любая download-ошибка должна вернуть 502, не 200 (не пытаемся swap).
	if rr.Code != http.StatusBadGateway {
		t.Errorf("expected 502 on download fail, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestSelfUpdate_ChecksumMismatch(t *testing.T) {
	// Mini HTTP server отдаёт известный payload, передаём заведомо НЕВЕРНЫЙ
	// хеш — endpoint должен 400, без swap'а.
	payload := []byte("fake binary content")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write(payload)
	}))
	defer srv.Close()

	// Real SHA256 был бы:
	realSum := sha256.Sum256(payload)
	realHex := hex.EncodeToString(realSum[:])
	wrongHex := strings.Replace(realHex, "a", "b", -1)
	if wrongHex == realHex {
		// Если в hex нет ни одной 'a' случайно, инвертируем 'b'<->'c' для надёжности.
		wrongHex = strings.Replace(realHex, "0", "1", -1)
	}

	s := &Server{selfUpdateServiceName: "test.service"}
	handler := s.HandleSelfUpdate(SelfUpdateConfig{ServiceName: "test"})

	body, _ := json.Marshal(map[string]string{"url": srv.URL, "sha256": wrongHex})
	rr := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/admin/self-update",
		strings.NewReader(string(body)))
	handler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 on checksum mismatch, got %d: %s",
			rr.Code, rr.Body.String())
	}
	if !strings.Contains(strings.ToLower(rr.Body.String()), "sha256") {
		t.Errorf("error should mention sha256, got: %s", rr.Body.String())
	}
}

// TestSelfUpdate_HTTP4xxNotSwallowed — если URL вернёт 404, не должны делать
// swap (мы получим HTML error page вместо бинаря).
func TestSelfUpdate_HTTP4xxNotSwallowed(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "not found", http.StatusNotFound)
	}))
	defer srv.Close()

	s := &Server{selfUpdateServiceName: "test.service"}
	handler := s.HandleSelfUpdate(SelfUpdateConfig{ServiceName: "test"})

	body, _ := json.Marshal(map[string]string{
		"url": srv.URL, "sha256": strings.Repeat("a", 64),
	})
	rr := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/admin/self-update",
		strings.NewReader(string(body)))
	handler(rr, req)

	if rr.Code != http.StatusBadGateway {
		t.Errorf("expected 502 on 404 source, got %d: %s", rr.Code, rr.Body.String())
	}
}

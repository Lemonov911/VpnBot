package service

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
)

type ScriptService struct {
	name string
	dir  string
}

func NewScriptService(name, dir string) *ScriptService {
	return &ScriptService{name: name, dir: dir}
}

func (s *ScriptService) run(op string, input any) ([]byte, error) {
	scriptPath := filepath.Join(s.dir, op)
	if _, err := os.Stat(scriptPath); os.IsNotExist(err) {
		return nil, fmt.Errorf("script %s/%s not found", s.name, op)
	}

	var inputJSON []byte
	var err error
	if input != nil {
		inputJSON, err = json.Marshal(input)
	} else {
		inputJSON = []byte("{}")
	}
	if err != nil {
		return nil, fmt.Errorf("marshal input: %w", err)
	}

	cmd := exec.Command(scriptPath)
	cmd.Stdin = bytes.NewReader(inputJSON)
	cmd.Env = append(os.Environ(),
		"VPNCTL_SERVICE="+s.name,
		"VPNCTL_OP="+op,
	)

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		log.Printf("script %s/%s error: %s", s.name, op, stderr.String())
		return nil, fmt.Errorf("script %s/%s failed: %w (stderr: %s)", s.name, op, err, stderr.String()[:min(200, len(stderr.String()))])
	}

	return stdout.Bytes(), nil
}

func (s *ScriptService) AddPeer(label string) (*Peer, error) {
	out, err := s.run("add", map[string]string{"label": label})
	if err != nil {
		return nil, err
	}
	var peer Peer
	if err := json.Unmarshal(out, &peer); err != nil {
		return nil, fmt.Errorf("parse add output: %w", err)
	}
	return &peer, nil
}

func (s *ScriptService) RemovePeer(id string) error {
	_, err := s.run("remove", map[string]string{"id": id})
	return err
}

func (s *ScriptService) ListPeers() ([]*Peer, error) {
	out, err := s.run("list", nil)
	if err != nil {
		return nil, err
	}
	var peers []*Peer
	if err := json.Unmarshal(out, &peers); err != nil {
		return nil, fmt.Errorf("parse list output: %w", err)
	}
	return peers, nil
}

func (s *ScriptService) SuspendPeer(id string) error {
	_, err := s.run("suspend", map[string]string{"id": id})
	return err
}

func (s *ScriptService) ResumePeer(id string) error {
	_, err := s.run("resume", map[string]string{"id": id})
	return err
}

func (s *ScriptService) SuspendAll(ids []string) error {
	_, err := s.run("suspend_all", map[string]any{"ids": ids})
	return err
}

func (s *ScriptService) ResumeAll(ids []string) error {
	_, err := s.run("resume_all", map[string]any{"ids": ids})
	return err
}

func (s *ScriptService) Info() map[string]any {
	out, err := s.run("info", nil)
	if err != nil {
		return map[string]any{"type": "script", "name": s.name, "error": err.Error()}
	}
	var info map[string]any
	if err := json.Unmarshal(out, &info); err != nil {
		return map[string]any{"type": "script", "name": s.name, "error": err.Error()}
	}
	return info
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
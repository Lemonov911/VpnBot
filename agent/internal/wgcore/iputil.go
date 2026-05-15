// Package wgcore — общие хелперы для WG/AWG менеджеров.
//
// До этого пакета `inc` и `cloneIP` были дословным дублем в
// `agent/awg/manager.go` и `agent/wg/manager.go`. Раньше менять
// одно — забывал второе.
//
// Логика добавления/удаления пиров остаётся per-manager: AWG идёт
// через CLI `awg` (kernel-модуль amneziawg не работает с wgctrl),
// WG — через wgctrl/UAPI. Оба нужны.
package wgcore

import "net"

// Inc увеличивает IP-адрес на 1 (in-place, mutates ip).
//
// Используется в IP allocator'е (`nextFreeIP`) для перебора подсети
// 10.66.66.0/24 → 10.66.66.1 → 10.66.66.2 → ... до свободного.
func Inc(ip net.IP) {
	for j := len(ip) - 1; j >= 0; j-- {
		ip[j]++
		if ip[j] > 0 {
			break
		}
	}
}

// CloneIP возвращает копию IP-адреса, чтобы Inc не мутировал общий
// network address из net.ParseCIDR (он шарится между вызовами).
func CloneIP(ip net.IP) net.IP {
	out := make(net.IP, len(ip))
	copy(out, ip)
	return out
}

// StripMask отрезает CIDR-маску у адреса: "10.66.66.5/32" → "10.66.66.5".
// Если маски нет — возвращает строку как есть.
func StripMask(cidr string) string {
	for i, c := range cidr {
		if c == '/' {
			return cidr[:i]
		}
	}
	return cidr
}

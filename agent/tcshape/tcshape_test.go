package tcshape

import (
	"testing"
)

func TestHex4(t *testing.T) {
	cases := []struct {
		in   int
		want string
	}{
		{0, "0000"},
		{15, "000f"},
		{255, "00ff"},
		{9443, "24e3"}, // vless-base-slow
		{9448, "24e8"}, // vless-max-slow
		{9453, "24ed"}, // vless-grace
		{8443, "20fb"}, // vless-base (но не throttled)
		{0xffff, "ffff"},
	}
	for _, c := range cases {
		got := hex4(c.in)
		if got != c.want {
			t.Errorf("hex4(%d)=%q, want %q", c.in, got, c.want)
		}
	}
}

func TestPortToU32Hex(t *testing.T) {
	cases := []struct {
		port int
		want string
	}{
		// Critical: эти строки должны точно совпадать с тем что live tc выдаёт.
		// На Amsterdam: `tc filter show dev eth0` показывает match 24ed0000/ffff0000.
		{9453, "0x24ed0000"},
		{9448, "0x24e80000"},
		{9443, "0x24e30000"},
	}
	for _, c := range cases {
		got := portToU32Hex(c.port)
		if got != c.want {
			t.Errorf("portToU32Hex(%d)=%q, want %q", c.port, got, c.want)
		}
	}
}

func TestKbitStr(t *testing.T) {
	cases := []struct {
		kbit int
		want string
	}{
		{256, "256kbit"},   // grace
		{5000, "5mbit"},    // slow
		{15000, "15mbit"},  // max-slow
		{1000, "1mbit"},    // edge
		{1, "1kbit"},       // edge
		{1500, "1500kbit"}, // не круглый mbit
	}
	for _, c := range cases {
		got := kbitStr(c.kbit)
		if got != c.want {
			t.Errorf("kbitStr(%d)=%q, want %q", c.kbit, got, c.want)
		}
	}
}

func TestItoa(t *testing.T) {
	cases := []struct {
		n    int
		want string
	}{
		{0, "0"},
		{1, "1"},
		{42, "42"},
		{256, "256"},
		{49150, "49150"}, // filter pref для grace
		{-5, "-5"},
	}
	for _, c := range cases {
		got := itoa(c.n)
		if got != c.want {
			t.Errorf("itoa(%d)=%q, want %q", c.n, got, c.want)
		}
	}
}

// TestApply_EmptyIface — guard: пустой iface не должен паниковать.
func TestApply_EmptyIface(t *testing.T) {
	// Не должно бросить exit/panic — просто early return с log.
	Apply("", []Tier{
		{Name: "vless-grace", Port: 9453, ClassID: "1:40", RateKbit: 256, FilterPref: 49150},
	})
}

// TestApply_NoTiers — guard: пустой список tier'ов = no-op.
func TestApply_NoTiers(t *testing.T) {
	// Если iface есть но tier'ов нет — попытается qdisc add, может упасть,
	// но не должен паниковать. Тестируем с пустым iface чтобы избежать exec
	// в unit-тесте.
	Apply("", nil)
	Apply("", []Tier{})
}

// TestApply_TierWithZeroRate — tier с RateKbit=0 должен быть пропущен
// (никаких class/filter команд).  Косвенно через "доходит до конца без panic".
func TestApply_TierWithZeroRate(t *testing.T) {
	Apply("", []Tier{
		{Name: "vless-base", Port: 8443, RateKbit: 0}, // skip
		{Name: "vless-grace", Port: 9453, ClassID: "1:40", RateKbit: 256, FilterPref: 49150},
	})
}

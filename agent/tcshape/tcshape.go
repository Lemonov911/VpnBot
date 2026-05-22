// Package tcshape устанавливает HTB-shaping на egress-интерфейсе для VLESS-tier'ов.
//
// Зачем это нужно: VLESS-throttle (slow / grace) делается не per-peer в агенте
// (Reality TLS-шифрован, agent не видит per-stream), а через server-wide HTB
// на eth0 + filter по source-port. До этого пакета HTB ставился вручную при
// провижининге сервера shell-командами; если забыли — VLESS-grace юзер получал
// full speed (баг 22.05 на Charlotte). Теперь agent сам гарантирует HTB при
// каждом старте.
//
// Идемпотентность: команды `add` дают "RTNETLINK answers: File exists" если
// классы уже есть — ловим и игнорим. Никаких del-then-add чтобы не дропать
// существующий traffic в краткий момент.
//
// Class-mapping (фиксирован для совместимости с уже задеплоенными серверами,
// где этот же tc-setup ставился вручную):
//
//	1:10 → default, 1 Gbit/s (vless-base, vless-max, всё остальное)
//	1:20 → vless-base-slow (5 Mbit/s)
//	1:30 → vless-max-slow  (15 Mbit/s)
//	1:40 → vless-grace     (256 kbit/s)
//
// Filter matchит source-port на offset 20 (первые 2 байта TCP header после
// 20-byte IP header) — egress server'а отвечает с этого порта, throttle применяется.
package tcshape

import (
	"log"
	"os/exec"
	"strings"
)

// Tier описывает один VLESS-tier для shaping. Если RateKbit=0, класс не
// создаётся (трафик идёт через default 1:10 без лимита).
type Tier struct {
	Name       string
	Port       int    // inbound port в Xray, например 9453 для grace
	ClassID    string // например "1:40"
	RateKbit   int    // 0 = unlimited (нет filter'а)
	FilterPref int    // priority filter'а, уникальный
}

// Apply ставит HTB-qdisc + per-tier классы и port-filter'ы на iface.
// Все команды идемпотентны — повторный запуск это no-op.
// Ошибки логируются, но не прерывают агент (best-effort: лучше работать без
// throttle чем не стартануть).
func Apply(iface string, tiers []Tier) {
	if iface == "" {
		log.Printf("tcshape: empty iface, skipping HTB setup")
		return
	}

	cmds := [][]string{
		// Root HTB qdisc.  default 0x10 = всё что не match'нулось → класс 1:10 (full speed).
		{"tc", "qdisc", "add", "dev", iface, "root", "handle", "1:", "htb", "default", "0x10"},
		// Default class — full speed.  burst 100mb — позволяет initial TCP-всплеск
		// чтобы handshake/первые TLS-пакеты не упирались в rate.
		{"tc", "class", "add", "dev", iface, "parent", "1:", "classid", "1:10",
			"htb", "rate", "1000mbit", "ceil", "1000mbit", "burst", "100mb"},
	}

	for _, t := range tiers {
		if t.RateKbit <= 0 {
			continue
		}
		rateStr := kbitStr(t.RateKbit)
		// Per-tier класс с rate==ceil (без overuse).  burst 64k = ~30ms на 256kbit.
		cmds = append(cmds, []string{
			"tc", "class", "add", "dev", iface,
			"parent", "1:", "classid", t.ClassID,
			"htb", "rate", rateStr, "ceil", rateStr, "burst", "64k",
		})
		// Filter: match source-port на offset 20.  Port в u32 — старшие 16 бит,
		// маска 0xffff0000 чтобы матчить только source-port (destination игнорируем).
		// ПЕРЕД add делаем del того же pref'а: `tc filter add` НЕ дедуплицируется,
		// каждый рестарт агента иначе добавлял бы дубль (filter с тем же match но
		// другим order'ом).  Через год набегало бы 50+ копий — мусор в kernel.
		// `del pref X` снимает все u32-filter'ы на этом pref'е; на чистом сервере
		// упадёт с "Empty filter list", это ловим как идемпотентный no-op.
		portHex := portToU32Hex(t.Port)
		cmds = append(cmds, []string{
			"tc", "filter", "del", "dev", iface,
			"parent", "1:", "protocol", "ip", "pref", itoa(t.FilterPref),
		})
		cmds = append(cmds, []string{
			"tc", "filter", "add", "dev", iface,
			"parent", "1:", "protocol", "ip", "pref", itoa(t.FilterPref), "u32",
			"match", "u32", portHex, "0xffff0000", "at", "20",
			"flowid", t.ClassID,
		})
		log.Printf("tcshape: %s port=%d → class %s rate=%s", t.Name, t.Port, t.ClassID, rateStr)
	}

	for _, c := range cmds {
		out, err := exec.Command(c[0], c[1:]...).CombinedOutput()
		if err != nil {
			s := string(out)
			// Идемпотентность: проглатываем варианты "уже существует" / "ещё нет".
			//   "File exists"                — class/filter add при дубле
			//   "Exclusivity flag on, cannot modify"  — qdisc add при существующем root
			//   "Empty filter list"          — `tc filter del` на чистом сервере (нечего удалять)
			//   "Specified filter handle not found" — то же, разные kernel'ы по-разному пишут
			if strings.Contains(s, "File exists") ||
				strings.Contains(s, "Exclusivity flag on") ||
				strings.Contains(s, "Empty filter list") ||
				strings.Contains(s, "Specified filter handle not found") {
				continue
			}
			log.Printf("tcshape WARN: %s: %v: %s",
				strings.Join(c, " "), err, strings.TrimSpace(s))
		}
	}
	log.Printf("tcshape: applied on %s (%d tiers)", iface, len(tiers))
}

// kbitStr форматирует "256kbit" / "5000kbit" / "15000kbit" — tc принимает kbit/mbit.
func kbitStr(kbit int) string {
	if kbit%1000 == 0 {
		return itoa(kbit/1000) + "mbit"
	}
	return itoa(kbit) + "kbit"
}

// portToU32Hex форматирует source-port для tc u32 match: 0xPPPP0000.
// Например порт 9453 (0x24ed) → "0x24ed0000".
func portToU32Hex(port int) string {
	return "0x" + hex4(port) + "0000"
}

func hex4(n int) string {
	const digits = "0123456789abcdef"
	buf := []byte{
		digits[(n>>12)&0xf],
		digits[(n>>8)&0xf],
		digits[(n>>4)&0xf],
		digits[n&0xf],
	}
	return string(buf)
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b [20]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		b[i] = '-'
	}
	return string(b[i:])
}

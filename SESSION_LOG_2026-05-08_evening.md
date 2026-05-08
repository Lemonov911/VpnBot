# Журнал 08.05.2026 (вечерняя сессия) — финальный диагноз

Продолжение `SESSION_LOG_2026-05-07_08.md`. Добавили India сервер, разобрали StealthSurf конфиг, подтвердили окончательно что **DigitalOcean AS14061 не выживает на MTS DPI**.

---

## Главное за вечер

### 1. Получили клиентский конфиг StealthSurf и расшифровали что они используют

```
Server: 5.231.119.78 (de.stealthsurf.network)
AS:     215365 — Tom Gewiese (Kirchberg, Бавария)
Netname: "Threatoff"   ← bulletproof хостинг
Port:   43000
Protocol: VLESS Reality
SNI:    "" (пустой!)
ShortID: 5d31bc70 (8 hex chars)
PublicKey: RD5TGQyD1gjVO1uOMQ9yhRzZ9rGE8CCnMm43iHMw4kE
Fingerprint: chrome
Flow: xtls-rprx-vision
Policy:
  bufferSize: 3
  downlinkOnly: 4
  uplinkOnly: 2
```

**Их секрет — НЕ протокол.** Они используют стандартный VLESS+Reality. Их **уникальное преимущество** — мелкий немецкий bulletproof хостер AS215365, **не помеченный как VPN-провайдер** в ML-системе MTS DPI.

### 2. Подняли India-сервер `139.59.44.171` (DigitalOcean Bangalore)

Сначала **первая минута показала 73 КБ/сек** — успех!  
Потом DPI распознал паттерн и шейпил до **7 КБ/сек** (только Telegram проходит).

Через ~5 минут — **0 байт payload** (только SYN/ACK), все большие ответы режутся.

### 3. Все варианты последних трюков — безуспешно на DO IP

Проверили:
- ❌ **SNI = max.ru** (русский домен, в whitelist) — не помогло
- ❌ **fp = random** (рандомный TLS fingerprint per session) — не помогло  
- ❌ **8-char shortId + пустой SNI fallback** (StealthSurf style) — не помогло
- ❌ **Порт 43100 (как у StealthSurf)** vs `:443`, `:23456` — порт не критичен
- ❌ **Bunny CDN front** — DPI режет на reverse-path даже через CDN
- ❌ Все остальные протоколы (Trojan-WS, AnyTLS, Hysteria2, AmneziaWG, Shadowsocks 2022) — то же

**Единственное что меняло картину** на 30-60 секунд — переход на свежий IP (Bangalore). Через минуту DPI догонял.

---

## Окончательный диагноз

### MTS DPI (2026) использует:

1. **Stateful flow analysis** — отслеживает encrypted flows, не зависит от протокола
2. **Reverse-path throttling** — режет ОТВЕТЫ от сервера, а не запросы
3. **Pattern matching на размер пакетов** — мелкие (Telegram <100 b) проходят, большие (видео ~1500 b) шейпятся
4. **AS-scoring system** — каждый AS имеет VPN-rank:
   - **AS14061 (DigitalOcean)** — высший rank, режется агрессивно
   - **AS24940 (Hetzner), AS16276 (OVH), AS20473 (Vultr)** — высокий
   - **AS215365 (Tom Gewiese), AS197540 (netcup), малые провайдеры** — низкий, не палят
5. **ML-детектор за минуты** учится на новом IP того же AS

### Что РЕАЛЬНО работает на MTS

#### Architecture (community-проверено):

| Компонент | Что |
|---|---|
| **VLESS+Reality** | Стандарт, ничего особенного |
| **dest** | `max.ru:443` (русский whitelist'ed домен) |
| **SNI** | `max.ru` или `bunny.net` (хитро, выдают за CDN) |
| **fp** | `chrome` или `random` |
| **shortid** | 8 или 16 hex (не критично) |
| **Transport** | XHTTP > gRPC > WS > raw TCP |
| **IP** | **МАЛЕНЬКИЙ AS не в watch-list MTS** (это главное!) |

#### Хостеры с low VPN-rank:
- **Tom Gewiese** (AS215365, ~€8/мес) — что использует StealthSurf
- **Aeza.net** (~€5/мес, разные мелкие AS) — VPN-friendly официально
- **netcup** (AS197540, ~€3/мес) — мелкий немецкий
- **HostKey** (AS395839, ~€5)
- **PS.KZ** (AS207912, $3, KZ-прокси)

#### Community ресурсы:
- [igareck/vpn-configs-for-russia](https://github.com/igareck/vpn-configs-for-russia) — auto-tested конфиги, обновляются 1-2 раза/час, **whitelist mobile направление есть**
- [Sergei-thinker/vpn-setup](https://github.com/Sergei-thinker/vpn-setup) — Multi-layer VPN
- [Хабр: VPN пережил белые списки за 265₽](https://habr.com/ru/articles/1021160/)

---

## Что не работает (зафиксировать чтоб не повторять)

1. **DigitalOcean любая локация** — AS14061 в watch-list
2. **CDN-фронт (Bunny / CF)** — режется на reverse-path  
3. **Любая обфускация на уже-помеченном IP** — ML догоняет
4. **Hysteria2/UDP** на mobile MTS — UDP пакеты режутся в whitelist mode
5. **CF Workers** — нужен новый CF аккаунт (наш с `maxvpnesim.shop` flagged за VPN-traffic)
6. **Yandex Cloud** — частично работает у некоторых, у других уже забанен (отдельный AS от Yandex.LLC)

---

## Следующий шаг (когда вернёмся)

**Срочно:**
1. Удалить burned DO дроплеты (104.248.101.179, 207.154.214.108, 139.59.44.171) — освободить ресурсы
2. Купить **netcup CX11** (~€3) или **Aeza Frankfurt** (~€5) — низкий VPN-rank
3. На новом IP поднять **только VLESS+Reality+max.ru** (не делать массу тестов!)
4. Использовать **тихо**, не светить публично — IP будет жить 1-3 месяца

**Стратегически (для VPN-бизнеса на MTS):**
- 3-5 серверов в **разных мелких AS**
- Auto-rotation в боте — health-check определяет когда сервер deflag-нулся
- **Не использовать DigitalOcean** для VPN-exit вообще (только для админки/бота)

**Бюджет минимум:** $20-30/мес инфраструктуры, постоянная ротация серверов раз в 2-4 недели.

---

## Что у нас есть в репо после этих 2 дней

- ✅ Полный agent (Go) — `agent/` с `setup-server.sh`
- ✅ Bot Python — все handlers, services, scheduler  
- ✅ Webapp (React) с подпиской
- ✅ Документация: `VPN_CF_BYPASS.md`, `SESSION_LOG_2026-05-06.md`, `SESSION_LOG_2026-05-07_08.md`, `SESSION_LOG_2026-05-08_evening.md`
- ✅ Bunny CDN setup описание + Caddy + NaiveProxy/AnyTLS configs
- ✅ Список рабочих/нерабочих протоколов с обоснованием
- ✅ Список community ресурсов и dpi-resistant хостеров

Когда возьмёшь новый VPS — есть с чего быстро развернуться.

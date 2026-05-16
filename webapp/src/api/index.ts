import WebApp from '@twa-dev/sdk'

const API_BASE = import.meta.env.VITE_API_URL ?? ''

// ── Базовые хелперы ───────────────────────────────────────────────────────────

/**
 * Заголовки для каждого запроса.
 * X-Telegram-Init-Data — новый способ авторизации.
 * init_data в теле — старый способ (backward compat на бэке).
 */
function authHeaders(): Record<string, string> {
  return {
    'Content-Type': 'application/json',
    'X-Telegram-Init-Data': WebApp.initData,
  }
}

async function post<T>(path: string, body: object): Promise<T> {
  const res = await fetch(API_BASE + path, {
    method: 'POST',
    headers: authHeaders(),
    // init_data в теле — для backward compatibility
    body: JSON.stringify({ ...body, init_data: WebApp.initData }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.error ?? `HTTP ${res.status}`)
  return data as T
}

async function get<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(API_BASE + path, window.location.origin)
  if (params) Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v))
  const res = await fetch(url.toString(), { headers: authHeaders() })
  const data = await res.json()
  if (!res.ok) throw new Error(data.error ?? `HTTP ${res.status}`)
  return data as T
}

// ── VPN ───────────────────────────────────────────────────────────────────────

export function createVpnInvoice(planKey: string): Promise<{ invoice_url: string }> {
  return post('/api/vpn/invoice', { plan_key: planKey })
}

export function createVpnInvoiceCrypto(
  planKey: string,
  currency: 'RUB' | 'USD',
): Promise<{ pay_url: string }> {
  return post('/api/vpn/invoice/crypto', { plan_key: planKey, currency })
}

export function createVpnInvoiceCryptomus(
  planKey: string,
  currency: 'RUB' | 'USD',
): Promise<{ pay_url: string }> {
  return post('/api/vpn/invoice/cryptomus', { plan_key: planKey, currency })
}

// Feature flags из /api/health — кэшируем на сессию.
let _featuresCache: Promise<Features> | null = null
export interface Features {
  esim: boolean
  cryptobot: boolean
  cryptomus: boolean
}
export function getFeatures(): Promise<Features> {
  if (_featuresCache) return _featuresCache
  _featuresCache = fetch(API_BASE + '/api/health')
    .then(r => r.json())
    .then(d => (d.features as Features) ?? { esim: false, cryptobot: false, cryptomus: false })
    .catch(() => ({ esim: false, cryptobot: false, cryptomus: false }))
  return _featuresCache
}

export interface VpnConfig {
  id:          number
  protocol:    'vless' | 'awg' | 'wg'
  peer_name:   string | null
  label:       string | null
  status:      string
  has_config:  boolean
  assigned_ip: string
  rx_bytes:    number
  tx_bytes:    number
  rx_human:    string
  tx_human:    string
  last_seen:   string | null
  plan:        string
  expires_at:  string
  sub_status:  string
  server_name: string
  server_flag: string
  server_city: string
  vless_url:   string | null
}

export function getUserConfigs(): Promise<VpnConfig[]> {
  return get('/api/vpn/configs')
}

/**
 * Возвращает URL для скачивания .conf файла (открывать через window.open или location.href).
 * Передаём init_data как query-параметр т.к. это прямая навигация, не fetch.
 */
export function getConfigDownloadUrl(configId: number): string {
  const encoded = encodeURIComponent(WebApp.initData)
  // API_BASE пустой в production-сборке (VITE_API_URL=""), поэтому берём origin окна.
  // WebApp.downloadFile() требует абсолютный https:// URL — относительный не принимает.
  const origin = API_BASE || window.location.origin
  return `${origin}/api/vpn/config/${configId}/download?init_data=${encoded}`
}

export function getConfigQrUrl(configId: number): string {
  const encoded = encodeURIComponent(WebApp.initData)
  const origin = API_BASE || window.location.origin
  return `${origin}/api/vpn/config/${configId}/qr?init_data=${encoded}`
}

export interface VpnServer {
  id:       number
  name:     string
  location: string
}

export function getVpnServers(protocol: string): Promise<VpnServer[]> {
  return get('/api/vpn/servers', { protocol })
}

export interface VpnServerStatus {
  id:       number
  name:     string
  location: string
  ok:       boolean
}

export function getVpnStatus(): Promise<VpnServerStatus[]> {
  return get('/api/vpn/status')
}

export function activateSlot(configId: number, serverId: number, format?: string): Promise<{ ok: boolean }> {
  return post(`/api/vpn/config/${configId}/activate`, { server_id: serverId, ...(format ? { format } : {}) })
}

export function revokeConfig(configId: number): Promise<{ ok: boolean }> {
  return post(`/api/vpn/config/${configId}/revoke`, {})
}

export interface Subscription {
  id:               number
  plan:             string
  stars_paid:       number
  expires_at:       string
  pending_plan:     string | null
  days_remaining:   number
  status?:          'active' | 'grace' | 'expired'
  grace_until?:     string | null
  grace_days_left?: number
  // Subscription URL для Happ/V2Box/Streisand. Persistent токен на юзера —
  // содержит все VLESS-локации, обновляется в клиенте раз в 12ч.
  // null если у юзера ещё нет ни одного VLESS-конфига.
  sub_url?:         string | null
}

export function getActiveSubscription(): Promise<Subscription | null> {
  return get('/api/vpn/subscription')
}

export function changeSubscriptionPlan(planKey: string): Promise<{
  invoice_url?: string
  ok?: boolean
  scheduled?: boolean
  cancelled?: boolean
  same?: boolean
}> {
  return post('/api/vpn/subscription/change', { plan_key: planKey })
}

export interface TrialStatus {
  eligible:      boolean
  duration_days: number
}

export interface TrialClaim {
  sub_id:        number
  sub_url:       string
  expires_at:    string
  duration_days: number
}

export function getTrialStatus(): Promise<TrialStatus> {
  return get('/api/vpn/trial')
}

export function claimTrial(): Promise<TrialClaim> {
  return post('/api/vpn/trial/claim', {})
}

// ── Public status — no auth ───────────────────────────────────────────────────

export interface UptimeWindow { pct: number | null; samples: number; total: number }

export interface PublicServerStatus {
  id:          number
  name:        string
  flag:        string
  location:    string
  protocol:    string
  status:      'up' | 'down' | 'unknown'
  latency_ms:  number | null
  uptime:      { '24h': UptimeWindow; '7d': UptimeWindow; '30d': UptimeWindow }
  strip_24h:   Array<'up' | 'down' | 'unknown'>
  strip_30d:   Array<'up' | 'down' | 'partial' | 'unknown'>
}

export interface Incident {
  id:           number
  server_name:  string
  flag:         string
  started_at:   string
  resolved_at:  string | null
  duration_sec: number | null
}

export interface PublicStatus {
  bot:        'up'
  updated:    string
  servers:    PublicServerStatus[]
  summary:    { up: number; total: number; all_ok: boolean }
  incidents:  Incident[]
}

export async function getPublicStatus(): Promise<PublicStatus> {
  // No auth headers — endpoint is public and is reachable from a browser
  // tab that has no Telegram initData.
  const res = await fetch((import.meta.env.VITE_API_URL ?? '') + '/api/status')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export interface IncidentHistory {
  incidents: Incident[]
  total:     number
  limit:     number
  offset:    number
}

export async function getIncidentHistory(limit = 50, offset = 0): Promise<IncidentHistory> {
  const url = `${import.meta.env.VITE_API_URL ?? ''}/api/status/incidents?limit=${limit}&offset=${offset}`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

// ── eSIM ──────────────────────────────────────────────────────────────────────

export interface Country {
  code:      string
  name:      string
  name_en?:  string
  flag?:     string
  count:     number
  is_russia?: boolean
}

export interface ESimPackage {
  packageCode:  string
  slug?:        string
  name:         string
  location?:    string
  volume?:      number
  dataLabel:    string
  dataType:     number   // 1=total data, 2=daily-FUP (speed reduced after limit)
  duration:     number
  durationUnit: string
  speed:        string
  ipExport:     string   // country code(s) of IP exit, e.g. "UK"
  fupPolicy?:   string
  price:        number   // wholesale units (для invoice payload)
  priceRub:     number   // основная цена для UI
  priceUsd:     number   // справочно
  stars:        number
}

export interface MyESim {
  id:           number
  status:       'pending' | 'ready' | 'failed'
  packageName:  string
  locationCode: string | null
  iccid:        string | null
  ac:           string | null
  qrUrl:        string | null
  shortUrl:     string | null
  smdpAddress:  string | null
  matchingId:   string | null
  usedBytes:    number
  totalBytes:   number
  usedPct:      number
  expireAt:     string | null
  lastSyncAt:   string | null
  createdAt:    string
}

export function getESimCountries(): Promise<Country[]> {
  return get('/api/esim/countries')
}

export function getESimPackages(country: string): Promise<ESimPackage[]> {
  return get('/api/esim/packages', { country })
}

export function createESimInvoice(pkg: ESimPackage): Promise<{ invoice_url: string }> {
  return post('/api/esim/invoice', {
    package_code: pkg.packageCode,
    price:        pkg.price,
    stars:        pkg.stars,
    name:         `eSIM ${pkg.dataLabel} · ${pkg.duration} ${pkg.durationUnit}`,
  })
}

export function getMyESims(): Promise<MyESim[]> {
  return get('/api/esim/my')
}

// ── Поддержка ─────────────────────────────────────────────────────────────────

export type SupportCategory = 'vpn' | 'esim' | 'payment' | 'other'

export function createSupportTicket(
  category: SupportCategory,
  message: string,
): Promise<{ ok: boolean; ticket_id: number }> {
  return post('/api/support/ticket', { category, message })
}

// ── Реферальная программа ─────────────────────────────────────────────────────

export interface ReferralStats {
  ref_link:   string
  invited:    number
  converted:  number
  bonus_days: number
}

export function getReferralStats(): Promise<ReferralStats> {
  return get('/api/referral/stats')
}

// ── User stats ────────────────────────────────────────────────────────────────

export interface UserStats {
  stars_spent: number
  bonus_days:  number
  invited:     number
}

export function getUserStats(): Promise<UserStats> {
  return get('/api/user/stats')
}

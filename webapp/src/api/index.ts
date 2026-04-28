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

export interface VpnConfig {
  id:          number
  protocol:    'awg' | 'vless'
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
  return `/api/vpn/config/${configId}/download?init_data=${encoded}`
}

export function getConfigQrUrl(configId: number): string {
  const encoded = encodeURIComponent(WebApp.initData)
  return `/api/vpn/config/${configId}/qr?init_data=${encoded}`
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

export function activateSlot(configId: number, serverId: number): Promise<{ ok: boolean }> {
  return post(`/api/vpn/config/${configId}/activate`, { server_id: serverId })
}

export function revokeConfig(configId: number): Promise<{ ok: boolean }> {
  return post(`/api/vpn/config/${configId}/revoke`, {})
}

export interface Subscription {
  id:             number
  plan:           string
  stars_paid:     number
  expires_at:     string
  pending_plan:   string | null
  days_remaining: number
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

// ── eSIM ──────────────────────────────────────────────────────────────────────

export interface Country {
  code:  string
  name:  string
  count: number
}

export interface ESimPackage {
  packageCode:  string
  name:         string
  dataLabel:    string
  dataType:     number   // 1=total data, 2=daily (volume resets each day)
  duration:     number
  durationUnit: string
  speed:        string
  ipExport:     string   // country code(s) of IP exit, e.g. "UK"
  price:        number   // wholesale units (для invoice payload)
  stars:        number
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

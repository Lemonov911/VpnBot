'use client'

import {
  AreaChart, Area, LineChart, Line,
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Legend,
} from 'recharts'

// ── Types ─────────────────────────────────────────────────────────────────────

type SubsDay    = { day: string; trials: number; paid: number }
type LatencyRow = { server_id: number; name: string; flag: string | null; ts: string; avg_ms: number | null; uptime_pct: number }
type StripRow   = { server_id: number; bucket: number; up_n: number; total_n: number }
type PlanRow    = { plan: string; n: number }
type ServerInfo = { id: number; name: string; flag: string | null }

// ── Helpers ───────────────────────────────────────────────────────────────────

const PLAN_LABELS: Record<string, string> = {
  vpn_base: 'База', vpn_max: 'Макс', vpn_trial: 'Триал',
  vpn_start: 'Старт', vpn_popular: 'Популярный', vpn_pro: 'Про', vpn_family: 'Семейный',
  vpn_base_12m: 'База 12м', vpn_max_12m: 'Макс 12м',
  vpn_base_3m: 'База 3м', vpn_max_3m: 'Макс 3м',
  vpn_base_6m: 'База 6м', vpn_max_6m: 'Макс 6м',
}

const SERVER_COLORS = ['#2481cc', '#10b981', '#f59e0b', '#8b5cf6', '#ef4444']

function fmtDay(iso: string) {
  const d = new Date(iso)
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })
}

function fmtHour(iso: string) {
  return iso.slice(11, 16) // "HH:00"
}

const tooltipStyle = {
  contentStyle: { background: '#171717', border: '1px solid #262626', borderRadius: 8, fontSize: 12 },
  labelStyle: { color: '#a3a3a3' },
  itemStyle: { color: '#fff' },
}

// ── Subscriber Growth Chart ───────────────────────────────────────────────────

export function SubsGrowthChart({ data }: { data: SubsDay[] }) {
  const formatted = data.map(d => ({ ...d, label: fmtDay(d.day) }))
  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5">
      <div className="text-sm font-semibold mb-1">Новые подписки</div>
      <div className="text-xs text-neutral-500 mb-4">последние 14 дней</div>
      {formatted.length === 0 ? (
        <div className="h-[180px] flex items-center justify-center text-sm text-neutral-600">нет данных</div>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <AreaChart data={formatted} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
            <defs>
              <linearGradient id="gradPaid" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#2481cc" stopOpacity={0.35} />
                <stop offset="100%" stopColor="#2481cc" stopOpacity={0} />
              </linearGradient>
              <linearGradient id="gradTrial" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#10b981" stopOpacity={0.25} />
                <stop offset="100%" stopColor="#10b981" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#262626" />
            <XAxis dataKey="label" tick={{ fill: '#737373', fontSize: 10 }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
            <YAxis tick={{ fill: '#737373', fontSize: 10 }} tickLine={false} axisLine={false} allowDecimals={false} />
            <Tooltip {...tooltipStyle} formatter={(v, name) => [v, name === 'paid' ? 'Платных' : 'Триалов']} />
            <Area type="monotone" dataKey="paid"   stroke="#2481cc" strokeWidth={2} fill="url(#gradPaid)"  dot={false} name="paid" />
            <Area type="monotone" dataKey="trials" stroke="#10b981" strokeWidth={2} fill="url(#gradTrial)" dot={false} name="trials" />
            <Legend formatter={(v) => v === 'paid' ? 'Платных' : 'Триалов'} wrapperStyle={{ fontSize: 11, color: '#a3a3a3' }} />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

// ── Latency Chart ─────────────────────────────────────────────────────────────

export function LatencyChart({ data, servers }: { data: LatencyRow[]; servers: ServerInfo[] }) {
  // Pivot: one entry per hour, one key per server
  const tsSet = [...new Set(data.map(r => r.ts))].sort()
  const pivoted = tsSet.map(ts => {
    const entry: Record<string, string | number | null> = { label: fmtHour(ts) }
    for (const srv of servers) {
      const row = data.find(r => r.ts === ts && r.server_id === srv.id)
      entry[`srv_${srv.id}`] = row?.avg_ms ?? null
    }
    return entry
  })

  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5">
      <div className="text-sm font-semibold mb-1">Latency по серверам</div>
      <div className="text-xs text-neutral-500 mb-4">последние 24 часа, среднее по часу (мс)</div>
      {pivoted.length === 0 ? (
        <div className="h-[180px] flex items-center justify-center text-sm text-neutral-600">нет данных</div>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={pivoted} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#262626" />
            <XAxis dataKey="label" tick={{ fill: '#737373', fontSize: 10 }} tickLine={false} axisLine={false} interval={3} />
            <YAxis tick={{ fill: '#737373', fontSize: 10 }} tickLine={false} axisLine={false} unit=" ms" />
            <Tooltip {...tooltipStyle} formatter={(v, name) => [`${v} ms`, name]} />
            {servers.map((srv, i) => (
              <Line
                key={srv.id}
                type="monotone"
                dataKey={`srv_${srv.id}`}
                name={`${srv.flag || ''} ${srv.name}`.trim()}
                stroke={SERVER_COLORS[i % SERVER_COLORS.length]}
                strokeWidth={2}
                dot={false}
                connectNulls
              />
            ))}
            <Legend wrapperStyle={{ fontSize: 11, color: '#a3a3a3' }} />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

// ── Plan Distribution Chart ───────────────────────────────────────────────────

export function PlanDistChart({ data }: { data: PlanRow[] }) {
  const formatted = data
    .filter(d => d.plan !== 'vpn_trial')
    .map(d => ({ name: PLAN_LABELS[d.plan] ?? d.plan, n: d.n }))

  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5">
      <div className="text-sm font-semibold mb-1">Активные тарифы</div>
      <div className="text-xs text-neutral-500 mb-4">платящие + grace</div>
      {formatted.length === 0 ? (
        <div className="h-[160px] flex items-center justify-center text-sm text-neutral-600">нет данных</div>
      ) : (
        <ResponsiveContainer width="100%" height={Math.max(120, formatted.length * 36)}>
          <BarChart data={formatted} layout="vertical" margin={{ top: 0, right: 24, left: 0, bottom: 0 }}>
            <XAxis type="number" tick={{ fill: '#737373', fontSize: 10 }} tickLine={false} axisLine={false} allowDecimals={false} />
            <YAxis type="category" dataKey="name" tick={{ fill: '#a3a3a3', fontSize: 11 }} tickLine={false} axisLine={false} width={72} />
            <Tooltip {...tooltipStyle} formatter={(v) => [v, 'подписок']} />
            <Bar dataKey="n" fill="#2481cc" radius={[0, 4, 4, 0]} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

// ── Uptime Strip ──────────────────────────────────────────────────────────────

export function UptimeStrip({ serverId, stripData }: { serverId: number; stripData: StripRow[] }) {
  const rows = stripData.filter(r => r.server_id === serverId)
  const bucketMap: Record<number, { up_n: number; total_n: number }> = {}
  rows.forEach(r => { bucketMap[r.bucket] = { up_n: r.up_n, total_n: r.total_n } })

  return (
    <div className="flex gap-[1.5px] items-center mt-1.5">
      {Array.from({ length: 48 }, (_, i) => {
        const b = bucketMap[i]
        let cls = 'bg-neutral-800'
        if (b) {
          const pct = b.up_n / b.total_n
          cls = pct >= 0.9 ? 'bg-emerald-500' : pct >= 0.5 ? 'bg-yellow-500' : 'bg-rose-500'
        }
        const label = b
          ? `${Math.round(i / 2)}–${Math.round(i / 2) + 1}ч: ${Math.round((b.up_n / b.total_n) * 100)}% up`
          : `${Math.round(i / 2)}–${Math.round(i / 2) + 1}ч: нет данных`
        return (
          <div
            key={i}
            className={`h-4 flex-1 rounded-sm ${cls} transition-colors`}
            title={label}
          />
        )
      })}
    </div>
  )
}

// ── Server Latency Sparkline ──────────────────────────────────────────────────

export function LatencySparkline({ serverId, data }: { serverId: number; data: LatencyRow[] }) {
  const rows = data
    .filter(r => r.server_id === serverId && r.avg_ms != null)
    .map(r => ({ t: fmtHour(r.ts), v: r.avg_ms! }))

  if (rows.length < 2) {
    return <span className="text-neutral-700 text-xs">—</span>
  }

  return (
    <ResponsiveContainer width={120} height={32}>
      <LineChart data={rows} margin={{ top: 2, right: 2, left: 2, bottom: 2 }}>
        <Line type="monotone" dataKey="v" stroke="#2481cc" strokeWidth={1.5} dot={false} />
        <Tooltip
          contentStyle={{ background: '#171717', border: '1px solid #262626', borderRadius: 6, fontSize: 11, padding: '2px 6px' }}
          labelFormatter={(l) => `${l}`}
          formatter={(v) => [`${v} ms`, '']}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

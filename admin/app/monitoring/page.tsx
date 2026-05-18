import { requireSession } from '@/lib/auth'
import {
  monitoringSnapshot, newSubsPerDay, latencyHistory24h,
  uptimeStrip24h, activeSubsByPlan,
} from '@/lib/db'
import { redirect } from 'next/navigation'
import AdminNav from '../_components/AdminNav'
import {
  SubsGrowthChart, LatencyChart, PlanDistChart,
  UptimeStrip, LatencySparkline,
} from './Charts'

function StatCard({ label, value, warn, hint }: { label: string; value: string | number; warn?: boolean; hint?: string }) {
  return (
    <div className={`bg-neutral-900 border rounded-2xl p-5 ${warn ? 'border-amber-500/40 border-l-2 border-l-amber-500' : 'border-neutral-800'}`}>
      <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-1">{label}</div>
      <div className={`text-2xl font-bold ${warn ? 'text-amber-400' : 'text-white'}`}>{value}</div>
      {hint && <div className="text-xs text-neutral-500 mt-1">{hint}</div>}
    </div>
  )
}

export default async function Monitoring() {
  const session = await requireSession()
  if (!session) redirect('/login')

  const s       = monitoringSnapshot()
  const subs14d = newSubsPerDay(14)
  const latency = latencyHistory24h()
  const strip   = uptimeStrip24h()
  const plans   = activeSubsByPlan()

  const loadBucket = (peers: number, capacity: number) => {
    if (capacity === 0) return { pct: 0, cls: 'bg-neutral-700' }
    const pct = Math.round((peers / capacity) * 100)
    if (pct >= 90) return { pct, cls: 'bg-rose-500' }
    if (pct >= 70) return { pct, cls: 'bg-amber-500' }
    return { pct, cls: 'bg-emerald-500' }
  }

  const serverInfos = s.servers.map(sv => ({ id: sv.id, name: sv.name, flag: sv.flag }))

  return (
    <div className="min-h-screen p-6 max-w-6xl mx-auto space-y-8">
      <AdminNav />

      {/* KPI cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Active конфигов"   value={s.active_configs} />
        <StatCard label="Пустых слотов"      value={s.empty_slots}    hint="ждут активации" />
        <StatCard label="Тикеты открыты"     value={s.open_tickets}   warn={s.open_tickets > 0} hint={`${s.closed_tickets} закрыто`} />
        <StatCard label="Истекают за 3 дня"  value={s.expiring_3d}    warn={s.expiring_3d > 0} hint={`${s.expiring_1d} за 24ч`} />
      </div>

      {/* Charts — subscriber growth + latency */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="md:col-span-2">
          <SubsGrowthChart data={subs14d} />
        </div>
        <PlanDistChart data={plans} />
      </div>

      <LatencyChart data={latency} servers={serverInfos} />

      {/* Server health table with uptime strip + sparkline */}
      <div className="bg-neutral-900 border border-neutral-800 rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-neutral-800 flex items-baseline justify-between">
          <div className="font-semibold text-sm">VPN-серверы</div>
          <div className="text-xs text-neutral-500">
            Публичный{' '}
            <a href="/status" target="_blank" rel="noopener" className="text-sky-400 underline">/status</a>
            {' '}— внешний пользователский view
          </div>
        </div>
        {s.servers.length === 0 ? (
          <div className="px-5 py-8 text-center text-sm text-neutral-500">нет серверов в БД</div>
        ) : (
          <div className="divide-y divide-neutral-800">
            {s.servers.map(srv => {
              const load      = loadBucket(srv.active_peers, srv.capacity)
              const liveStatus = srv.last_probe_status ?? (srv.is_active ? 'unknown' : 'down')
              const liveDot   = liveStatus === 'up' ? 'bg-emerald-500' : liveStatus === 'down' ? 'bg-rose-500 animate-pulse' : 'bg-neutral-600'
              const liveTxt   = liveStatus === 'up' ? 'text-emerald-400' : liveStatus === 'down' ? 'text-rose-400' : 'text-neutral-600'
              const uptimeColor =
                srv.uptime_24h_pct == null ? 'text-neutral-600'
                : srv.uptime_24h_pct >= 99 ? 'text-emerald-400'
                : srv.uptime_24h_pct >= 95 ? 'text-amber-400'
                : 'text-rose-400'

              return (
                <div key={srv.id} className="px-5 py-4 hover:bg-neutral-800/20 transition-colors">
                  {/* Main row */}
                  <div className="flex items-center gap-4">
                    {/* Live dot + label */}
                    <div className="flex items-center gap-1.5 w-14 shrink-0">
                      <span className={`inline-block w-2.5 h-2.5 rounded-full shrink-0 ${liveDot}`} />
                      <span className={`text-[10px] font-bold ${liveTxt}`}>
                        {liveStatus === 'up' ? 'UP' : liveStatus === 'down' ? 'DOWN' : '—'}
                      </span>
                    </div>

                    {/* Server name */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-sm">{srv.flag || '🌍'} {srv.name}</span>
                        <span className={`text-xs px-1.5 py-0.5 rounded-full font-mono ${
                          srv.protocol === 'awg'   ? 'bg-blue-900/40 text-blue-300 border border-blue-800/40' :
                          srv.protocol === 'vless' ? 'bg-violet-900/40 text-violet-300 border border-violet-800/40' :
                          'bg-neutral-800 text-neutral-400'
                        }`}>{srv.protocol}</span>
                        {!srv.is_active && (
                          <span className="text-xs px-1.5 py-0.5 bg-red-900/30 text-red-400 border border-red-800/40 rounded-full">дренирован</span>
                        )}
                      </div>
                      <div className="text-[10px] text-neutral-600 mt-0.5">{srv.city || '—'} · {srv.host}</div>
                    </div>

                    {/* Uptime 24h */}
                    <div className="shrink-0 text-center w-16">
                      <div className="text-[10px] text-neutral-600 uppercase tracking-wide mb-0.5">24h up</div>
                      <div className={`text-sm font-mono font-semibold ${uptimeColor}`}>
                        {srv.uptime_24h_pct == null ? '—' : `${srv.uptime_24h_pct}%`}
                      </div>
                    </div>

                    {/* Latency */}
                    <div className="shrink-0 text-center w-16">
                      <div className="text-[10px] text-neutral-600 uppercase tracking-wide mb-0.5">Latency</div>
                      <div className="text-sm font-mono text-neutral-300">
                        {srv.last_probe_latency != null ? `${srv.last_probe_latency}ms` : '—'}
                      </div>
                    </div>

                    {/* Latency sparkline */}
                    <div className="shrink-0 w-[120px]">
                      <LatencySparkline serverId={srv.id} data={latency} />
                    </div>

                    {/* Load bar */}
                    <div className="w-24 shrink-0">
                      <div className="flex justify-between mb-1">
                        <span className="text-[10px] text-neutral-600 uppercase tracking-wide">Load</span>
                        <span className={`text-[10px] font-mono ${load.pct > 80 ? 'text-amber-400' : 'text-neutral-400'}`}>{load.pct}%</span>
                      </div>
                      <div className="h-2 bg-neutral-800 rounded-full overflow-hidden">
                        <div className={`h-full ${load.cls} rounded-full`} style={{ width: `${Math.min(100, load.pct)}%` }} />
                      </div>
                      <div className="text-[10px] text-neutral-700 text-right mt-0.5 font-mono">{srv.active_peers}/{srv.capacity}</div>
                    </div>
                  </div>

                  {/* Uptime strip below */}
                  <div className="mt-2 pl-[4.5rem]">
                    <div className="text-[9px] text-neutral-700 mb-1">Uptime 24h (по 30 мин)</div>
                    <UptimeStrip serverId={srv.id} stripData={strip} />
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      <div className="text-xs text-neutral-600 text-center">
        Проба каждые 60с · auto-deactivate после 10 down подряд ·{' '}
        <a href="/status" target="_blank" rel="noopener" className="text-sky-400">публичный /status</a>
      </div>
    </div>
  )
}

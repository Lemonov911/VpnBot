import Link from 'next/link'
import { requireSession } from '@/lib/auth'
import { monitoringSnapshot } from '@/lib/db'
import { redirect } from 'next/navigation'

function StatCard({ label, value, warn, hint }: { label: string; value: string | number; warn?: boolean; hint?: string }) {
  return (
    <div className={`bg-neutral-900 border rounded-2xl p-5 ${warn ? 'border-amber-500/40' : 'border-neutral-800'}`}>
      <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-1">{label}</div>
      <div className={`text-2xl font-bold ${warn ? 'text-amber-400' : 'text-white'}`}>{value}</div>
      {hint && <div className="text-xs text-neutral-500 mt-1">{hint}</div>}
    </div>
  )
}

export default async function Monitoring() {
  const session = await requireSession()
  if (!session) redirect('/login')

  const s = monitoringSnapshot()

  const loadBucket = (peers: number, capacity: number) => {
    if (capacity === 0) return { pct: 0, cls: 'bg-neutral-700' }
    const pct = Math.round((peers / capacity) * 100)
    if (pct >= 90) return { pct, cls: 'bg-rose-500' }
    if (pct >= 70) return { pct, cls: 'bg-amber-500' }
    return { pct, cls: 'bg-emerald-500' }
  }

  return (
    <div className="min-h-screen p-6 max-w-6xl mx-auto space-y-8">
      <div className="flex items-center justify-between pt-2">
        <div>
          <div className="text-xl font-extrabold tracking-tight">Мониторинг</div>
          <div className="text-xs text-neutral-500 mt-0.5">Инфра + операционные сигналы</div>
        </div>
        <div className="flex gap-4 items-center">
          <Link href="/"           className="text-xs text-neutral-500 hover:text-neutral-300">Дашборд</Link>
          <Link href="/analytics"  className="text-xs text-neutral-500 hover:text-neutral-300">Аналитика</Link>
          <Link href="/clients"    className="text-xs text-neutral-500 hover:text-neutral-300">Клиенты</Link>
          <Link href="/tickets"    className="text-xs text-neutral-500 hover:text-neutral-300">Обращения</Link>
          <Link href="/servers"    className="text-xs text-neutral-500 hover:text-neutral-300">Серверы</Link>
        </div>
      </div>

      {/* Headline */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Active конфигов"   value={s.active_configs} />
        <StatCard label="Пустых слотов"      value={s.empty_slots}    hint="ждут активации" />
        <StatCard label="Тикеты открыты"     value={s.open_tickets}   warn={s.open_tickets > 0} hint={`${s.closed_tickets} закрыто`} />
        <StatCard label="Истекают за 3 дня"  value={s.expiring_3d}    warn={s.expiring_3d > 0} hint={`${s.expiring_1d} за 24ч`} />
      </div>

      {/* Server list */}
      <div className="bg-neutral-900 border border-neutral-800 rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-neutral-800 flex items-baseline justify-between">
          <div className="font-semibold text-sm">VPN-серверы</div>
          <div className="text-xs text-neutral-500">
            Публичный <a href="/status" target="_blank" rel="noopener" className="text-sky-400 underline">/status</a> — внешний пользователский view
          </div>
        </div>
        {s.servers.length === 0 ? (
          <div className="px-5 py-8 text-center text-sm text-neutral-500">нет серверов в БД</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-neutral-500 uppercase tracking-wide">
                <tr className="border-b border-neutral-800">
                  <th className="text-left px-4 py-2 font-medium w-12">Live</th>
                  <th className="text-left px-4 py-2 font-medium">Сервер</th>
                  <th className="text-left px-4 py-2 font-medium">Протокол</th>
                  <th className="text-left px-4 py-2 font-medium">Нагрузка</th>
                  <th className="text-right px-4 py-2 font-medium">Пиров</th>
                  <th className="text-left px-4 py-2 font-medium">Agent URL</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-800">
                {s.servers.map(srv => {
                  const load = loadBucket(srv.active_peers, srv.capacity)
                  return (
                    <tr key={srv.id} className="hover:bg-neutral-800/30 transition-colors">
                      <td className="px-4 py-2">
                        <span className={`inline-block w-2.5 h-2.5 rounded-full ${srv.is_active ? 'bg-emerald-500' : 'bg-neutral-600'}`} />
                      </td>
                      <td className="px-4 py-2">
                        <div className="font-medium">{srv.flag || '🌍'} {srv.name}</div>
                        <div className="text-[10px] text-neutral-500">{srv.city || '—'} · {srv.host}</div>
                      </td>
                      <td className="px-4 py-2 text-neutral-400 font-mono text-xs uppercase">{srv.protocol}</td>
                      <td className="px-4 py-2 w-[150px]">
                        <div className="h-1.5 bg-neutral-800 rounded-full overflow-hidden">
                          <div className={`h-full ${load.cls}`} style={{ width: `${Math.min(100, load.pct)}%` }} />
                        </div>
                        <div className="text-[10px] text-neutral-500 mt-1">{load.pct}%</div>
                      </td>
                      <td className="px-4 py-2 text-right font-mono text-xs">
                        {srv.active_peers} <span className="text-neutral-600">/ {srv.capacity}</span>
                      </td>
                      <td className="px-4 py-2 text-[10px] text-neutral-500 font-mono truncate max-w-[200px]">
                        {srv.agent_url || <span className="text-neutral-700">— нет агента</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Hint */}
      <div className="text-xs text-neutral-600 text-center px-4">
        ⚠ Эта страница — снэпшот из БД. Реальный live-статус агентов смотри на <a href="/status" target="_blank" rel="noopener" className="text-sky-400 underline">/status</a>.
        Health-checker (auto-deactivate dead servers) пока не реализован — см. P1#6 в roadmap.
      </div>
    </div>
  )
}

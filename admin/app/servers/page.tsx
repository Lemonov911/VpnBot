'use client'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import AdminNav from '../_components/AdminNav'

type Server = {
  id: number
  name: string
  flag: string
  city: string
  host: string
  agent_url: string
  protocol: string
  capacity: number
  active_peers: number
  is_active: number
  wg_pubkey: string
  created_at: string
}

export default function ServersPage() {
  const [servers, setServers] = useState<Server[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const router = useRouter()

  const [form, setForm] = useState({
    name: '', flag: '🌍', city: '', host: '',
    agent_url: '', agent_token: '', protocol: 'awg', capacity: '100',
  })

  async function load() {
    const r = await fetch('/admin/api/servers')
    if (r.status === 401) { router.push('/admin/login'); return }
    setServers(await r.json())
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  async function addServer(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError('')
    const r = await fetch('/admin/api/servers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...form, capacity: parseInt(form.capacity) }),
    })
    const data = await r.json()
    if (!r.ok) { setError(data.error); setSaving(false); return }
    setSaving(false)
    setShowForm(false)
    setForm({ name: '', flag: '🌍', city: '', host: '', agent_url: '', agent_token: '', protocol: 'awg', capacity: '100' })
    load()
  }

  async function disableServer(id: number) {
    if (!confirm('Drain server — новые пиры на него не идут. Существующие работают. Продолжить?')) return
    await fetch(`/admin/api/servers?id=${id}`, { method: 'DELETE' })
    load()
  }

  async function enableServer(id: number) {
    await fetch(`/admin/api/servers?id=${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_active: 1 }),
    })
    load()
  }

  const [confirmDelete, setConfirmDelete] = useState<number | null>(null)

  async function deleteServer(id: number, name: string) {
    if (confirmDelete !== id) {
      setConfirmDelete(id)
      setTimeout(() => setConfirmDelete(c => c === id ? null : c), 5000)
      return
    }
    setConfirmDelete(null)
    const r = await fetch(`/admin/api/servers/${id}`, { method: 'DELETE' })
    const data = await r.json().catch(() => ({}))
    if (!r.ok) {
      alert(`Не удалось удалить «${name}»: ${data.error || r.statusText}`)
      return
    }
    load()
  }

  async function setCapacity(id: number, current: number) {
    const raw = prompt('Новый capacity (1..10000):', String(current))
    if (raw === null) return
    const n = parseInt(raw, 10)
    if (!Number.isFinite(n) || n < 1 || n > 10000) { alert('1..10000'); return }
    const r = await fetch(`/admin/api/servers?id=${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ capacity: n }),
    })
    if (!r.ok) { const d = await r.json().catch(() => ({})); alert(d.error || 'error') }
    load()
  }

  const [backfilling, setBackfilling] = useState<number | null>(null)
  const [confirmMigrate, setConfirmMigrate] = useState<number | null>(null)
  const [migrating, setMigrating] = useState<number | null>(null)

  async function migrateConfigs(id: number, name: string) {
    if (confirmMigrate !== id) {
      setConfirmMigrate(id)
      setTimeout(() => setConfirmMigrate(c => c === id ? null : c), 5000)
      return
    }
    setConfirmMigrate(null)
    setMigrating(id)
    try {
      const r = await fetch(`/admin/api/servers/${id}/migrate`, { method: 'POST' })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) { alert(`Ошибка: ${data.error || r.statusText}`); return }
      const failTail = data.failed > 0 ? `\nОшибок: ${data.failed}` : ''
      const vlessTail = data.reset_vless > 0 ? `\nVLESS сброшено: ${data.reset_vless}` : ''
      alert(`Готово.\nAWG мигрировано: ${data.migrated}${vlessTail}${failTail}`)
      load()
    } finally {
      setMigrating(null)
    }
  }

  async function backfillVless(id: number, name: string) {
    if (!confirm(
      `Прокинуть существующие VLESS-слоты на сервер «${name}»?\n\n` +
      `Каждой active/grace подписке добавится локация на этом сервере ` +
      `(тот же UUID — юзеру не надо переимпортировать подписку). ` +
      `Может занять минуты при большом числе слотов.`,
    )) return
    setBackfilling(id)
    try {
      const r = await fetch(`/admin/api/servers/${id}/backfill-vless`, { method: 'POST' })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) { alert(`Ошибка: ${data.error || r.statusText}`); return }
      const failTail = data.failed > 0 ? `\nОшибок: ${data.failed}` : ''
      alert(`Готово.\nСлотов проверено: ${data.scanned}\nСоздано: ${data.created}${failTail}`)
      load()
    } finally {
      setBackfilling(null)
    }
  }

  function loadPct(s: Server) {
    return s.capacity > 0 ? Math.round((s.active_peers / s.capacity) * 100) : 0
  }

  const protocolBadge = (p: string) =>
    p === 'awg'   ? 'bg-blue-900/40 text-blue-300 border border-blue-800/40' :
    p === 'vless' ? 'bg-violet-900/40 text-violet-300 border border-violet-800/40' :
                    'bg-neutral-800 text-neutral-400 border border-neutral-700'

  return (
    <div className="min-h-screen p-6 max-w-5xl mx-auto space-y-6">
      <AdminNav />

      {/* Add form */}
      {showForm && (
        <form onSubmit={addServer} className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 space-y-4">
          <div className="font-semibold text-sm pb-3 border-b border-neutral-800">Новый сервер</div>
          {error && <div className="text-red-400 text-xs bg-red-400/10 px-3 py-2 rounded-lg">{error}</div>}
          <div className="grid grid-cols-2 gap-3">
            <input className={inp} placeholder="Название (напр. Нидерланды)" value={form.name}
              onChange={e => setForm(f => ({...f, name: e.target.value}))} required />
            <input className={inp} placeholder="Флаг (эмодзи) 🇳🇱" value={form.flag}
              onChange={e => setForm(f => ({...f, flag: e.target.value}))} />
            <input className={inp} placeholder="Город (Amsterdam)" value={form.city}
              onChange={e => setForm(f => ({...f, city: e.target.value}))} />
            <input className={inp} placeholder="IP сервера (1.2.3.4)" value={form.host}
              onChange={e => setForm(f => ({...f, host: e.target.value}))} required />
            <input className={`${inp} col-span-2`} placeholder="Agent URL (http://1.2.3.4:9000)" value={form.agent_url}
              onChange={e => setForm(f => ({...f, agent_url: e.target.value}))} required />
            <input className={`${inp} col-span-2`} placeholder="Agent Token (секрет из .env)" value={form.agent_token}
              onChange={e => setForm(f => ({...f, agent_token: e.target.value}))} required />
            <select className={inp} value={form.protocol}
              onChange={e => setForm(f => ({...f, protocol: e.target.value}))}>
              <option value="awg">WireGuard (AWG)</option>
              <option value="vless">VLess</option>
            </select>
            <input className={inp} type="number" placeholder="Capacity (100)" value={form.capacity}
              onChange={e => setForm(f => ({...f, capacity: e.target.value}))} />
          </div>
          <div className="flex gap-2 pt-1">
            <button type="submit" disabled={saving}
              className="min-w-[140px] px-4 py-2 bg-[#2481cc] hover:bg-[#1a6db3] rounded-lg text-sm font-medium disabled:opacity-50 transition-colors">
              {saving ? 'Проверяем агента...' : 'Сохранить'}
            </button>
            <button type="button" onClick={() => { setShowForm(false); setError('') }}
              className="px-4 py-2 bg-neutral-800 hover:bg-neutral-700 rounded-lg text-sm transition-colors">
              Отмена
            </button>
          </div>
        </form>
      )}

      {/* Servers list */}
      <div className="bg-neutral-900 border border-neutral-800 rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-neutral-800 flex items-center justify-between">
          <div className="font-semibold text-sm">
            Серверы
            <span className="ml-2 text-xs font-normal text-neutral-500">
              {servers.filter(s => s.is_active).length} активных
              {servers.filter(s => !s.is_active).length > 0 && ` · ${servers.filter(s => !s.is_active).length} дренированных`}
            </span>
          </div>
          <button
            onClick={() => setShowForm(!showForm)}
            className="text-xs bg-[#2481cc] hover:bg-[#1a6db3] px-3 py-1.5 rounded-lg transition-colors"
          >
            + Добавить
          </button>
        </div>
        {loading ? (
          <div className="px-5 py-8 text-center text-sm text-neutral-600">Загрузка...</div>
        ) : servers.length === 0 ? (
          <div className="px-5 py-8 text-center text-sm text-neutral-600">
            Серверов нет. Добавьте первый сервер с запущенным vpnctl агентом.
          </div>
        ) : (
          <div className="divide-y divide-neutral-800">
            {servers.map(s => (
              <div
                key={s.id}
                className={`px-5 py-4 flex items-center gap-4 transition-colors ${
                  !s.is_active ? 'bg-red-500/[0.03]' : 'hover:bg-neutral-800/30'
                }`}
              >
                <div className={`text-2xl shrink-0 ${!s.is_active ? 'grayscale opacity-60' : ''}`}>
                  {s.flag}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`font-medium text-sm ${!s.is_active ? 'text-neutral-400' : ''}`}>
                      {s.name}
                    </span>
                    {s.city && <span className="text-xs text-neutral-500">{s.city}</span>}
                    <span className={`text-xs px-2 py-0.5 rounded-full ${protocolBadge(s.protocol)}`}>
                      {s.protocol}
                    </span>
                    {!s.is_active && (
                      <span className="text-xs px-2 py-0.5 bg-red-900/30 text-red-400 border border-red-800/40 rounded-full">
                        дренирован
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-neutral-600 mt-0.5">{s.host} · {s.agent_url}</div>
                  {s.wg_pubkey && (
                    <div className="text-xs text-neutral-700 font-mono mt-0.5 truncate max-w-xs">{s.wg_pubkey}</div>
                  )}
                </div>

                {/* Load bar */}
                <div className="w-24 shrink-0">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[10px] text-neutral-600 uppercase tracking-wide">Load</span>
                    <button
                      onClick={() => setCapacity(s.id, s.capacity)}
                      className={`text-xs hover:text-white transition-colors font-mono ${
                        loadPct(s) > 80 ? 'text-yellow-400' : 'text-neutral-400'
                      }`}
                      title="Изменить capacity"
                    >
                      {loadPct(s)}%
                    </button>
                  </div>
                  <div className="h-2 bg-neutral-800 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all ${loadPct(s) > 80 ? 'bg-yellow-500' : 'bg-[#2481cc]'}`}
                      style={{ width: `${loadPct(s)}%` }}
                    />
                  </div>
                  <div className="text-[10px] text-neutral-700 text-right mt-0.5 font-mono">
                    {s.active_peers}/{s.capacity}
                  </div>
                </div>

                {/* Action buttons */}
                <div className="flex items-center gap-1 shrink-0">
                  {s.is_active && s.protocol === 'vless' && (
                    <button
                      onClick={() => backfillVless(s.id, s.name)}
                      disabled={backfilling !== null}
                      className="text-xs px-2 py-1 rounded-md bg-neutral-800 text-neutral-400 hover:bg-[#2481cc]/20 hover:text-[#5aa6e0] disabled:opacity-50 transition-colors"
                      title="Прокинуть multi-location пиры существующих подписок на этот сервер"
                    >
                      {backfilling === s.id ? '...' : 'Backfill'}
                    </button>
                  )}
                  {s.is_active ? (
                    <button
                      onClick={() => disableServer(s.id)}
                      className="text-xs px-2 py-1 rounded-md bg-neutral-800 text-neutral-500 hover:bg-red-900/30 hover:text-red-400 transition-colors"
                    >
                      Drain
                    </button>
                  ) : (
                    <>
                      <button
                        onClick={() => enableServer(s.id)}
                        className="text-xs px-2 py-1 rounded-md bg-emerald-900/30 text-emerald-400 border border-emerald-800/30 hover:bg-emerald-900/50 transition-colors"
                      >
                        Включить
                      </button>
                      <button
                        onClick={() => migrateConfigs(s.id, s.name)}
                        disabled={migrating !== null}
                        className={`text-xs px-2 py-1 rounded-md transition-colors disabled:opacity-50 ${
                          confirmMigrate === s.id
                            ? 'bg-yellow-900/40 text-yellow-300 border border-yellow-700/40 ring-1 ring-yellow-500/40'
                            : 'bg-neutral-800 text-neutral-500 hover:bg-yellow-900/30 hover:text-yellow-400'
                        }`}
                        title="Мигрировать конфиги с этого сервера на доступные"
                      >
                        {migrating === s.id ? '...' : confirmMigrate === s.id ? 'Точно?' : 'Мигрировать'}
                      </button>
                      <button
                        onClick={() => deleteServer(s.id, s.name)}
                        className={`text-xs px-2 py-1 rounded-md transition-colors ${
                          confirmDelete === s.id
                            ? 'bg-red-900/40 text-red-300 border border-red-700/40 ring-1 ring-red-500/40'
                            : 'bg-neutral-800 text-neutral-600 hover:bg-red-900/30 hover:text-red-400'
                        }`}
                        title="Удалить сервер из БД (только если drained и нет active configs)"
                      >
                        {confirmDelete === s.id ? 'Точно?' : 'Удалить'}
                      </button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

const inp = "bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2 text-sm w-full focus:outline-none focus:border-[#2481cc] focus:ring-2 focus:ring-[#2481cc]/20 placeholder:text-neutral-600"

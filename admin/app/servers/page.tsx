'use client'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'

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

  // Двухступенчатое подтверждение для destructive ops — стандартный паттерн
  // в админке (`/admin/clients/[id]`-проба, audit-suggestion 16.05). Первый
  // тап ставит state.confirmDelete = id, второй (на той же кнопке)
  // действительно удаляет. State сбрасывается через 5 сек.
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
      alert(
        `Готово.\nAWG мигрировано: ${data.migrated}${vlessTail}${failTail}`,
      )
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
      alert(
        `Готово.\nСлотов проверено: ${data.scanned}\n` +
        `Создано: ${data.created}${failTail}`,
      )
      load()
    } finally {
      setBackfilling(null)
    }
  }

  function loadPct(s: Server) {
    return s.capacity > 0 ? Math.round((s.active_peers / s.capacity) * 100) : 0
  }

  return (
    <div className="min-h-screen p-6 max-w-5xl mx-auto space-y-6">
      <div className="flex items-center justify-between pt-2">
        <div>
          <div className="text-xl font-extrabold tracking-tight">MAX VPN &amp; eSIM</div>
          <div className="text-xs text-neutral-500 mt-0.5">Серверы</div>
        </div>
        <div className="flex gap-3 items-center">
          <a href="/admin" className="text-xs text-neutral-500 hover:text-neutral-300">← Дашборд</a>
          <button
            onClick={() => setShowForm(!showForm)}
            className="text-xs bg-[#2481cc] hover:bg-[#1a6db3] px-3 py-1.5 rounded-lg transition-colors"
          >
            + Добавить сервер
          </button>
        </div>
      </div>

      {/* Add form */}
      {showForm && (
        <form onSubmit={addServer} className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 space-y-4">
          <div className="font-semibold text-sm mb-2">Новый сервер</div>
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
              className="px-4 py-2 bg-[#2481cc] hover:bg-[#1a6db3] rounded-lg text-sm font-medium disabled:opacity-50 transition-colors">
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
        <div className="px-5 py-4 border-b border-neutral-800">
          <div className="font-semibold text-sm">Серверы ({servers.filter(s => s.is_active).length} активных)</div>
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
              <div key={s.id} className={`px-5 py-4 flex items-center gap-4 ${!s.is_active ? 'opacity-40' : ''}`}>
                <div className="text-2xl">{s.flag}</div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm">{s.name}</span>
                    {s.city && <span className="text-xs text-neutral-500">{s.city}</span>}
                    <span className="text-xs px-1.5 py-0.5 bg-neutral-800 rounded text-neutral-400">{s.protocol}</span>
                    {!s.is_active && <span className="text-xs px-1.5 py-0.5 bg-red-900/40 text-red-400 rounded">отключён</span>}
                  </div>
                  <div className="text-xs text-neutral-500 mt-0.5">{s.host} · {s.agent_url}</div>
                  {s.wg_pubkey && (
                    <div className="text-xs text-neutral-600 font-mono mt-0.5 truncate max-w-xs">{s.wg_pubkey}</div>
                  )}
                </div>
                {/* Load bar — click capacity to edit */}
                <div className="w-24 shrink-0">
                  <button onClick={() => setCapacity(s.id, s.capacity)}
                    className="text-xs text-neutral-400 mb-1 text-right w-full hover:text-white"
                    title="Изменить capacity">
                    {s.active_peers}/{s.capacity}
                  </button>
                  <div className="h-1.5 bg-neutral-800 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all ${loadPct(s) > 80 ? 'bg-yellow-500' : 'bg-[#2481cc]'}`}
                      style={{ width: `${loadPct(s)}%` }}
                    />
                  </div>
                </div>
                {s.is_active && s.protocol === 'vless' && (
                  <button
                    onClick={() => backfillVless(s.id, s.name)}
                    disabled={backfilling !== null}
                    className="text-xs text-[#2481cc] hover:text-[#5aa6e0] disabled:opacity-50 transition-colors shrink-0"
                    title="Прокинуть multi-location пиры существующих подписок на этот сервер"
                  >
                    {backfilling === s.id ? '...' : 'Backfill'}
                  </button>
                )}
                {s.is_active ? (
                  <button onClick={() => disableServer(s.id)}
                    className="text-xs text-neutral-600 hover:text-red-400 transition-colors shrink-0">
                    Drain
                  </button>
                ) : (
                  <>
                    <button onClick={() => enableServer(s.id)}
                      className="text-xs text-emerald-500 hover:text-emerald-400 transition-colors shrink-0">
                      Включить
                    </button>
                    {/* Migrate: re-provision AWG конфиги с мёртвого сервера. Двойной тап. */}
                    <button
                      onClick={() => migrateConfigs(s.id, s.name)}
                      disabled={migrating !== null}
                      className={`text-xs transition-colors shrink-0 disabled:opacity-50 ${
                        confirmMigrate === s.id
                          ? 'text-yellow-400 font-semibold animate-pulse'
                          : 'text-neutral-500 hover:text-yellow-400'
                      }`}
                      title="Мигрировать конфиги с этого сервера на доступные">
                      {migrating === s.id ? '...' : confirmMigrate === s.id ? 'Точно?' : 'Мигрировать'}
                    </button>
                    {/* Hard-delete только для drained servers. Двойной тап.
                        Backend дополнительно блочит если есть active configs. */}
                    <button onClick={() => deleteServer(s.id, s.name)}
                      className={`text-xs transition-colors shrink-0 ${
                        confirmDelete === s.id
                          ? 'text-red-400 font-semibold animate-pulse'
                          : 'text-neutral-600 hover:text-red-400'
                      }`}
                      title="Удалить сервер из БД (только если drained и нет active configs)">
                      {confirmDelete === s.id ? 'Точно?' : 'Удалить'}
                    </button>
                  </>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

const inp = "bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2 text-sm w-full focus:outline-none focus:border-[#2481cc] placeholder:text-neutral-600"

'use client'

import { useEffect, useState } from 'react'

const API = '/admin/api'

// Должно соответствовать VPN_PLANS в bot/services/plans.py.
// Список планов сюда мы прокидываем хардкодом — клиенту достаточно
// видеть человекочитаемые имена. Бэкенд всё равно валидирует plan_key.
const PLANS: { key: string; label: string }[] = [
  { key: 'vpn_base',     label: 'База (30 дн)' },
  { key: 'vpn_max',      label: 'Макс (30 дн)' },
  { key: 'vpn_base_3m',  label: 'База 3 мес' },
  { key: 'vpn_base_6m',  label: 'База 6 мес' },
  { key: 'vpn_base_12m', label: 'База 1 год' },
  { key: 'vpn_max_3m',   label: 'Макс 3 мес' },
  { key: 'vpn_max_6m',   label: 'Макс 6 мес' },
  { key: 'vpn_max_12m',  label: 'Макс 1 год' },
]

type Grant = {
  id: number
  user_id: number
  user_username: string | null
  subscription_id: number | null
  granted_by_admin_id: number | null
  tx_id: string | null
  created_at: string
  sub_plan: string | null
  sub_status: string | null
  sub_expires_at: string | null
}

type Result =
  | { ok: true; subscription_id: number; expires_at: string; action: 'created' | 'extended'; notified?: boolean }
  | { error: string }

function fmt(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso.replace(' ', 'T'))
  if (isNaN(d.getTime())) return iso
  return d.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

export default function GrantForm({ adminId }: { adminId: number }) {
  const [targetId, setTargetId] = useState('')
  const [username, setUsername] = useState('')
  const [planKey, setPlanKey] = useState('vpn_base')
  const [days, setDays] = useState(30)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<Result | null>(null)
  const [grants, setGrants] = useState<Grant[]>([])

  const loadGrants = async () => {
    try {
      const r = await fetch(`${API}/grant?limit=50`, { cache: 'no-store' })
      const j = await r.json()
      if (Array.isArray(j.grants)) setGrants(j.grants)
    } catch {/* ignore */}
  }

  useEffect(() => { loadGrants() }, [])

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setResult(null)
    const tid = parseInt(targetId, 10)
    if (!Number.isFinite(tid) || tid <= 0) {
      setResult({ error: 'telegram_id должен быть положительным числом' })
      return
    }
    if (!Number.isFinite(days) || days < 1 || days > 365) {
      setResult({ error: 'дней: 1..365' })
      return
    }
    setBusy(true)
    try {
      const r = await fetch(`${API}/grant`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_telegram_id: tid,
          plan_key: planKey,
          days,
          reason: reason.trim() || undefined,
          target_username: username.trim() || undefined,
        }),
      })
      const j: Result = await r.json()
      setResult(j)
      if ('ok' in j && j.ok) {
        setTargetId(''); setUsername(''); setReason('')
        await loadGrants()
      }
    } catch (err) {
      setResult({ error: err instanceof Error ? err.message : String(err) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-6 space-y-6">
      <form
        onSubmit={onSubmit}
        className="bg-neutral-900 border border-neutral-800 rounded-lg p-5 space-y-4"
      >
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="text-xs text-neutral-400 block mb-1">Telegram ID <span className="text-rose-400">*</span></label>
            <input
              type="number"
              required
              min={1}
              value={targetId}
              onChange={e => setTargetId(e.target.value)}
              placeholder="например 123456789"
              className="w-full bg-neutral-950 border border-neutral-800 rounded-md px-3 py-2 text-sm focus:outline-none focus:border-sky-500"
            />
            <p className="text-[10px] text-neutral-600 mt-1">
              Узнать ID: попроси юзера написать @userinfobot или открыть @username_to_id_bot
            </p>
          </div>
          <div>
            <label className="text-xs text-neutral-400 block mb-1">@username <span className="text-neutral-600">(опционально)</span></label>
            <input
              type="text"
              value={username}
              onChange={e => setUsername(e.target.value)}
              placeholder="ivanov"
              className="w-full bg-neutral-950 border border-neutral-800 rounded-md px-3 py-2 text-sm focus:outline-none focus:border-sky-500"
            />
            <p className="text-[10px] text-neutral-600 mt-1">
              Только для пометки в логе — на выдачу не влияет.
            </p>
          </div>
          <div>
            <label className="text-xs text-neutral-400 block mb-1">Тариф <span className="text-rose-400">*</span></label>
            <select
              value={planKey}
              onChange={e => setPlanKey(e.target.value)}
              className="w-full bg-neutral-950 border border-neutral-800 rounded-md px-3 py-2 text-sm focus:outline-none focus:border-sky-500"
            >
              {PLANS.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-neutral-400 block mb-1">Дней <span className="text-rose-400">*</span></label>
            <input
              type="number"
              required
              min={1}
              max={365}
              value={days}
              onChange={e => setDays(parseInt(e.target.value, 10) || 0)}
              className="w-full bg-neutral-950 border border-neutral-800 rounded-md px-3 py-2 text-sm focus:outline-none focus:border-sky-500"
            />
            <div className="flex gap-2 mt-1">
              {[7, 14, 30, 90, 365].map(d => (
                <button
                  key={d}
                  type="button"
                  onClick={() => setDays(d)}
                  className="text-[10px] text-neutral-500 hover:text-sky-400"
                >
                  {d}д
                </button>
              ))}
            </div>
          </div>
        </div>
        <div>
          <label className="text-xs text-neutral-400 block mb-1">Причина / комментарий <span className="text-neutral-600">(опционально)</span></label>
          <textarea
            value={reason}
            onChange={e => setReason(e.target.value)}
            rows={2}
            maxLength={500}
            placeholder="напр. компенсация за simout 18.05, друг семьи, тестер UX, ..."
            className="w-full bg-neutral-950 border border-neutral-800 rounded-md px-3 py-2 text-sm focus:outline-none focus:border-sky-500"
          />
        </div>
        <div className="flex items-center justify-between pt-2">
          <div className="text-[10px] text-neutral-600">
            Выдача от admin_id: <code className="text-neutral-400">{adminId}</code>
          </div>
          <button
            type="submit"
            disabled={busy}
            className="px-4 py-2 rounded-md text-sm bg-sky-500 text-white hover:bg-sky-400 disabled:opacity-50"
          >
            {busy ? '...' : '🎁 Выдать'}
          </button>
        </div>

        {result && (
          <div className={`text-xs rounded-md px-3 py-2 ${
            'ok' in result && result.ok
              ? 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/30'
              : 'bg-rose-500/10 text-rose-300 border border-rose-500/30'
          }`}>
            {'ok' in result && result.ok ? (
              <>
                ✅ <b>{result.action === 'extended' ? 'Подписка продлена' : 'Подписка создана'}</b>
                {' '}— sub #{result.subscription_id}, до {fmt(result.expires_at)}
                {result.notified === false && (
                  <span className="text-neutral-500"> (юзер ещё не /start'нул — увидит при первом запуске)</span>
                )}
              </>
            ) : (
              <>❌ {('error' in result ? result.error : 'неизвестная ошибка')}</>
            )}
          </div>
        )}
      </form>

      <div>
        <h2 className="text-sm font-semibold text-neutral-300 mb-3">Последние 50 выдач</h2>
        <div className="bg-neutral-900 border border-neutral-800 rounded-lg overflow-hidden">
          <table className="w-full text-xs">
            <thead className="bg-neutral-950 text-neutral-500">
              <tr>
                <th className="text-left px-3 py-2 font-medium">Когда</th>
                <th className="text-left px-3 py-2 font-medium">Кому</th>
                <th className="text-left px-3 py-2 font-medium">Тариф</th>
                <th className="text-left px-3 py-2 font-medium">До</th>
                <th className="text-left px-3 py-2 font-medium">Статус</th>
                <th className="text-left px-3 py-2 font-medium">Выдал admin</th>
              </tr>
            </thead>
            <tbody>
              {grants.length === 0 && (
                <tr><td colSpan={6} className="px-3 py-6 text-center text-neutral-600">Пока нет выдач</td></tr>
              )}
              {grants.map(g => (
                <tr key={g.id} className="border-t border-neutral-800/60">
                  <td className="px-3 py-2 text-neutral-400">{fmt(g.created_at)}</td>
                  <td className="px-3 py-2">
                    <div className="font-mono text-neutral-300">{g.user_id}</div>
                    {g.user_username && (
                      <a
                        href={`https://t.me/${g.user_username}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-sky-400 hover:underline"
                      >
                        @{g.user_username}
                      </a>
                    )}
                  </td>
                  <td className="px-3 py-2 text-neutral-300">{g.sub_plan ?? '—'}</td>
                  <td className="px-3 py-2 text-neutral-400">{fmt(g.sub_expires_at)}</td>
                  <td className="px-3 py-2">
                    {g.sub_status === 'active' ? (
                      <span className="text-emerald-400">● active</span>
                    ) : g.sub_status === 'grace' ? (
                      <span className="text-amber-400">● grace</span>
                    ) : g.sub_status === 'expired' ? (
                      <span className="text-neutral-500">● expired</span>
                    ) : (
                      <span className="text-neutral-600">{g.sub_status ?? '—'}</span>
                    )}
                  </td>
                  <td className="px-3 py-2 font-mono text-neutral-400">{g.granted_by_admin_id ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getIncidentHistory, type Incident } from '../api'
import { useT, type TKey } from '../i18n'

type TFn = (k: TKey) => string

/**
 * Полная история инцидентов. Открывается с /status → "Все инциденты →".
 * Без auth (public endpoint). Пагинация по 50 в странице.
 */
export default function StatusIncidents() {
  const t = useT()
  const [incidents, setIncidents] = useState<Incident[] | null>(null)
  const [total,     setTotal]     = useState(0)
  const [offset,    setOffset]    = useState(0)
  const [err,       setErr]       = useState('')
  const [loading,   setLoading]   = useState(false)
  const LIMIT = 50

  useEffect(() => {
    document.title = 'MAX VPN Status — incident history'
  }, [])

  useEffect(() => {
    setLoading(true)
    getIncidentHistory(LIMIT, offset)
      .then(r => { setIncidents(r.incidents); setTotal(r.total); setErr('') })
      .catch(e => setErr(String(e?.message || e)))
      .finally(() => setLoading(false))
  }, [offset])

  return (
    <div className="page gap-3 pt-2">
      <div className="flex items-center justify-between px-1">
        <Link to="/status" className="text-[12px] text-primary hover:underline">← {t('status_back')}</Link>
        <div className="text-[11px] text-[var(--tg-theme-hint-color)]">
          {total > 0 ? `${total} ${t('status_total_incidents')}` : ''}
        </div>
      </div>

      <div className="rounded-[16px] p-3 bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)]">
        <div className="text-[13px] font-bold text-[var(--tg-theme-text-color)] mb-2 px-1">
          {t('status_incidents')}
        </div>

        {err && !incidents && (
          <div className="text-rose-500 text-[12px] py-2 px-1">⚠ {err}</div>
        )}

        {!err && incidents === null && (
          <div className="flex flex-col gap-1">
            {[...Array(8)].map((_, i) => <div key={i} className="skeleton h-[24px] rounded-[6px]" />)}
          </div>
        )}

        {incidents !== null && incidents.length === 0 && (
          <div className="text-[12px] text-[var(--tg-theme-hint-color)] px-1 py-3 text-center">
            {t('status_no_incidents')}
          </div>
        )}

        {incidents !== null && incidents.length > 0 && (
          <div className="flex flex-col gap-1">
            {incidents.map(inc => <IncidentDetailRow key={inc.id} inc={inc} t={t} />)}
          </div>
        )}
      </div>

      {/* Pagination */}
      {total > LIMIT && (
        <div className="flex justify-between items-center px-2 text-[11px]">
          <button
            disabled={offset === 0 || loading}
            onClick={() => setOffset(Math.max(0, offset - LIMIT))}
            className="px-3 py-1.5 rounded-md bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] disabled:opacity-30 cursor-pointer"
          >
            ← {t('status_prev')}
          </button>
          <span className="text-[var(--tg-theme-hint-color)]">
            {offset + 1}–{Math.min(offset + LIMIT, total)} / {total}
          </span>
          <button
            disabled={offset + LIMIT >= total || loading}
            onClick={() => setOffset(offset + LIMIT)}
            className="px-3 py-1.5 rounded-md bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] disabled:opacity-30 cursor-pointer"
          >
            {t('status_next')} →
          </button>
        </div>
      )}
    </div>
  )
}

function IncidentDetailRow({ inc, t }: { inc: Incident; t: TFn }) {
  const started = new Date(inc.started_at.replace(' ', 'T') + 'Z')
  const resolved = inc.resolved_at ? new Date(inc.resolved_at.replace(' ', 'T') + 'Z') : null
  const isOpen = !resolved
  const durStr = (() => {
    if (isOpen) return t('status_inc_ongoing')
    const sec = inc.duration_sec ?? 0
    if (sec < 60) return `${sec}с`
    if (sec < 3600) return `${Math.round(sec / 60)} мин`
    if (sec < 86400) return `${(sec / 3600).toFixed(1)} ч`
    return `${(sec / 86400).toFixed(1)} дн`
  })()

  return (
    <div className="flex items-center gap-2 text-[11px] px-2 py-1.5 rounded-md hover:bg-[var(--tg-theme-bg-color,#111)] border border-transparent">
      <span className={`w-[6px] h-[6px] rounded-full shrink-0 ${isOpen ? 'bg-rose-500 animate-pulse' : 'bg-neutral-500'}`} />
      <span className="text-[var(--tg-theme-text-color)] min-w-[120px] truncate">
        {inc.flag} {inc.server_name}
      </span>
      <span className="flex-1 text-[var(--tg-theme-hint-color)] font-mono">
        {started.toLocaleString('ru-RU', { year: '2-digit', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
      </span>
      <span className={`font-mono shrink-0 ${isOpen ? 'text-rose-500' : 'text-[var(--tg-theme-hint-color)]'}`}>
        {durStr}
      </span>
    </div>
  )
}

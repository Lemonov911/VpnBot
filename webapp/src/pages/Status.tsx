import { useEffect, useState } from 'react'
import { getPublicStatus, type PublicStatus, type PublicServerStatus, type Incident } from '../api'
import { useT, type TKey } from '../i18n'

type TFn = (k: TKey) => string

/**
 * Публичная страница статуса. Без auth — открывается без Telegram.
 *
 * Trust-signal: когда у юзера ломается интернет, естественный рефлекс —
 * глянуть status.maxvpnesim.com и понять «у меня или у них». Поднимает
 * доверие без админ-доступа в нашу систему.
 *
 * Что показывает:
 * - агрегированный header (все ок / частичный простой)
 * - бот-статус
 * - для каждого сервера: статус, latency, uptime % за 24h/7d/30d,
 *   24-часовой dot-strip
 * - последние 5 incidents с длительностью
 */
export default function Status() {
  const t = useT()
  const [data, setData] = useState<PublicStatus | null>(null)
  const [err,  setErr]  = useState('')

  useEffect(() => {
    document.title = 'MAX VPN Status — uptime & incidents'

    const load = () => {
      getPublicStatus().then(setData).catch(e => setErr(String(e?.message || e)))
    }
    load()
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [])

  if (err && !data) {
    return (
      <div className="page gap-3 pt-2">
        <div className="rounded-[16px] p-4 bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)]">
          <div className="text-sm font-bold text-rose-500">⚠ {t('status_load_err')}</div>
          <div className="text-[12px] text-[var(--tg-theme-hint-color)] mt-1">{err}</div>
        </div>
      </div>
    )
  }
  if (!data) {
    return (
      <div className="page gap-3 pt-2">
        <div className="skeleton h-[80px] rounded-[16px]" />
        <div className="skeleton h-[160px] rounded-[16px]" />
      </div>
    )
  }

  const ok = data.summary.all_ok
  const headerBg = ok ? 'from-emerald-500 to-cyan-500' : 'from-amber-500 to-rose-500'
  const headerIcon = ok ? '✅' : '⚠'
  const headerText = ok ? t('status_all_ok') : t('status_partial')

  const updated = new Date(data.updated)
  const updatedStr = updated.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

  return (
    <div className="page gap-3 pt-2">

      {/* Header */}
      <div className={`fade-in rounded-[20px] p-4 bg-gradient-to-br ${headerBg} text-white shadow-[0_8px_24px_rgba(14,165,233,0.25)]`}>
        <div className="flex items-center gap-2">
          <div className="text-2xl">{headerIcon}</div>
          <div className="flex-1">
            <div className="text-base font-bold">{headerText}</div>
            <div className="text-[11px] opacity-85">
              {data.summary.up}/{data.summary.total} {t('status_up_count')} · {t('status_updated')} {updatedStr}
            </div>
          </div>
        </div>
      </div>

      {/* Bot card */}
      <div className="rounded-[16px] p-3 bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] flex items-center gap-3">
        <span className="w-[10px] h-[10px] rounded-full bg-emerald-500 shrink-0" />
        <div className="flex-1">
          <div className="text-sm font-bold text-[var(--tg-theme-text-color)]">{t('status_bot')}</div>
          <div className="text-[11px] text-[var(--tg-theme-hint-color)]">@maxvpnesim_bot</div>
        </div>
        <div className="text-[11px] font-bold text-emerald-500">{t('status_up')}</div>
      </div>

      {/* Servers */}
      <div className="rounded-[16px] p-3 bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)]">
        <div className="text-[11px] font-bold uppercase tracking-wide text-[var(--tg-theme-hint-color)] mb-2 px-1">
          {t('status_servers')}
        </div>
        {data.servers.length === 0 ? (
          <div className="text-[12px] text-[var(--tg-theme-hint-color)] px-1 py-2">{t('status_empty')}</div>
        ) : (
          <div className="flex flex-col gap-3">
            {data.servers.map(s => <ServerCard key={s.id} s={s} t={t} />)}
          </div>
        )}
      </div>

      {/* Incidents */}
      {data.incidents.length > 0 && (
        <div className="rounded-[16px] p-3 bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)]">
          <div className="text-[11px] font-bold uppercase tracking-wide text-[var(--tg-theme-hint-color)] mb-2 px-1">
            {t('status_incidents')}
          </div>
          <div className="flex flex-col gap-2">
            {data.incidents.map(inc => <IncidentRow key={inc.id} inc={inc} t={t} />)}
          </div>
        </div>
      )}

      {/* Footer hint */}
      <div className="px-2 text-center text-[10px] text-[var(--tg-theme-hint-color)]">
        {t('status_autorefresh')}
      </div>
    </div>
  )
}

function ServerCard({ s, t }: { s: PublicServerStatus; t: TFn }) {
  const dotCls = s.status === 'up'   ? 'bg-emerald-500'
               : s.status === 'down' ? 'bg-rose-500 animate-pulse'
               :                       'bg-gray-400'
  const statusText = s.status === 'up'   ? 'text-emerald-500'
                   : s.status === 'down' ? 'text-rose-500'
                   :                       'text-gray-400'
  const statusLabel = s.status === 'up'   ? t('status_up')
                    : s.status === 'down' ? t('status_down')
                    :                       t('status_unknown')

  return (
    <div className="px-1">
      <div className="flex items-center gap-3 mb-2">
        <span className={`w-[10px] h-[10px] rounded-full shrink-0 ${dotCls}`} />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold text-[var(--tg-theme-text-color)] truncate">
            {s.flag} {s.name}
          </div>
          <div className="text-[10px] uppercase tracking-wide text-[var(--tg-theme-hint-color)]">
            {s.location} · {s.protocol}
          </div>
        </div>
        <div className="text-right">
          <div className={`text-[11px] font-bold ${statusText}`}>{statusLabel}</div>
          {s.latency_ms !== null && (
            <div className="text-[10px] text-[var(--tg-theme-hint-color)]">{s.latency_ms} ms</div>
          )}
        </div>
      </div>

      {/* 24h strip */}
      <div className="flex gap-[2px] mb-2 h-[10px]">
        {s.strip_24h.map((status, i) => {
          const cls = status === 'up'   ? 'bg-emerald-500'
                    : status === 'down' ? 'bg-rose-500'
                    :                     'bg-neutral-700/40'
          return <div key={i} className={`flex-1 rounded-sm ${cls}`} title={`-${24-i}h: ${status}`} />
        })}
      </div>

      {/* Uptime % for three windows */}
      <div className="flex gap-2 text-[10px] text-[var(--tg-theme-hint-color)]">
        <UptimeChip label="24ч" w={s.uptime['24h']} />
        <UptimeChip label="7д"  w={s.uptime['7d']}  />
        <UptimeChip label="30д" w={s.uptime['30d']} />
      </div>
    </div>
  )
}

function UptimeChip({ label, w }: { label: string; w: { pct: number | null; samples: number } }) {
  if (w.pct === null) {
    return <span className="opacity-50">{label} —</span>
  }
  const colour = w.pct >= 99   ? 'text-emerald-500'
               : w.pct >= 95   ? 'text-amber-400'
               :                 'text-rose-500'
  return <span><span className="opacity-60">{label}</span> <span className={colour}>{w.pct}%</span></span>
}

function IncidentRow({ inc, t }: { inc: Incident; t: TFn }) {
  const started = new Date(inc.started_at.replace(' ', 'T') + 'Z')
  const isOpen = inc.resolved_at === null
  const durStr = (() => {
    if (isOpen) return t('status_inc_ongoing')
    const sec = inc.duration_sec ?? 0
    if (sec < 60) return `${sec}с`
    if (sec < 3600) return `${Math.round(sec / 60)} мин`
    return `${(sec / 3600).toFixed(1)} ч`
  })()

  return (
    <div className="flex items-center gap-2 text-[11px] px-1 py-1">
      <span className={`w-[6px] h-[6px] rounded-full shrink-0 ${isOpen ? 'bg-rose-500 animate-pulse' : 'bg-neutral-500'}`} />
      <span className="text-[var(--tg-theme-text-color)] truncate">
        {inc.flag} {inc.server_name}
      </span>
      <span className="flex-1 text-[var(--tg-theme-hint-color)]">
        {started.toLocaleString('ru-RU', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
      </span>
      <span className={`font-mono ${isOpen ? 'text-rose-500' : 'text-[var(--tg-theme-hint-color)]'}`}>
        {durStr}
      </span>
    </div>
  )
}

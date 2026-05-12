import { useEffect, useState } from 'react'
import { getPublicStatus, type PublicStatus } from '../api'
import { useT } from '../i18n'

/**
 * Публичная страница статуса. Без auth — открывается без Telegram.
 *
 * Зачем: trust-signal. Когда у юзера ломается интернет, естественный рефлекс —
 * глянуть status.maxvpnesim.com и понять "у меня или у них". Поднимает доверие
 * без админ-доступа в нашу систему.
 */
export default function Status() {
  const t = useT()
  const [data, setData] = useState<PublicStatus | null>(null)
  const [err,  setErr]  = useState('')

  useEffect(() => {
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

  const dotCls = (s: string) =>
    s === 'up'   ? 'bg-emerald-500'  :
    s === 'down' ? 'bg-rose-500 animate-pulse' :
                   'bg-gray-400'

  const statusLabel = (s: string) =>
    s === 'up'   ? t('status_up') :
    s === 'down' ? t('status_down') :
                   t('status_unknown')

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
          <div className="flex flex-col">
            {data.servers.map((s, i) => (
              <div
                key={`${s.name}-${i}`}
                className={`flex items-center gap-3 py-2 px-1 ${i > 0 ? 'border-t border-[var(--card-border)]' : ''}`}
              >
                <span className={`w-[10px] h-[10px] rounded-full shrink-0 ${dotCls(s.status)}`} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-semibold text-[var(--tg-theme-text-color)] truncate">
                    {s.flag} {s.name}
                  </div>
                  <div className="text-[10px] uppercase tracking-wide text-[var(--tg-theme-hint-color)]">
                    {s.location} · {s.protocol}
                  </div>
                </div>
                <div className="text-right">
                  <div className={`text-[11px] font-bold ${
                    s.status === 'up'   ? 'text-emerald-500' :
                    s.status === 'down' ? 'text-rose-500'   : 'text-gray-400'
                  }`}>
                    {statusLabel(s.status)}
                  </div>
                  {s.latency_ms !== null && (
                    <div className="text-[10px] text-[var(--tg-theme-hint-color)]">{s.latency_ms} ms</div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Footer hint */}
      <div className="px-2 text-center text-[10px] text-[var(--tg-theme-hint-color)]">
        {t('status_autorefresh')}
      </div>
    </div>
  )
}

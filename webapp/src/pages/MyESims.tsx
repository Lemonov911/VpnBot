import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import { getMyESims, type MyESim } from '../api'
import { useT, type TKey } from '../i18n'

function flagEmoji(code: string | null): string {
  if (!code) return '🌐'
  if (code === 'EU-42') return '🇪🇺'
  if (!/^[A-Z]{2}$/i.test(code)) return '🌐'
  return [...code.toUpperCase()].map(c => String.fromCodePoint(0x1f1e6 + c.charCodeAt(0) - 65)).join('')
}

function fmtBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

function CopyRow({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true)
      WebApp.HapticFeedback.notificationOccurred('success')
      setTimeout(() => setCopied(false), 1500)
    })
  }
  return (
    <div className="flex items-center gap-2 py-[7px]">
      <span className="text-[11px] text-[var(--tg-theme-hint-color)] w-10 shrink-0">{label}</span>
      <span className="text-[11px] font-mono text-[var(--tg-theme-text-color)] flex-1 min-w-0 truncate">
        {value}
      </span>
      <button
        onClick={copy}
        className="shrink-0 text-[11px] font-semibold border-none bg-transparent cursor-pointer px-1.5 py-0.5 rounded-md transition-colors"
        style={{ color: copied ? 'var(--color-success)' : 'var(--tg-theme-link-color,#2481cc)' }}
      >
        {copied ? '✓' : 'copy'}
      </button>
    </div>
  )
}

function ESimCard({ sim, t }: { sim: MyESim; t: (k: TKey) => string }) { // eslint-disable-line @typescript-eslint/no-explicit-any
  const statusClass =
    sim.status === 'ready'   ? 'text-success' :
    sim.status === 'pending' ? 'text-warning'  : 'text-danger'

  const statusLabel =
    sim.status === 'ready'   ? t('myesim_status_ready') :
    sim.status === 'pending' ? t('myesim_status_pending') : t('myesim_status_failed')

  const iconBg =
    sim.status === 'ready'   ? 'bg-primary' :
    sim.status === 'pending' ? 'bg-warning'  : 'bg-danger'

  return (
    <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">

      {/* Header row */}
      <div className="py-[13px] px-4 flex items-center gap-[14px]">
        <div className={`w-10 h-10 rounded-xl shrink-0 flex items-center justify-center ${iconBg}`}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
            <rect x="5" y="2" width="14" height="20" rx="2" stroke="#fff" strokeWidth="2"/>
            <path d="M9 8h6M9 12h6M9 16h4" stroke="#fff" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[17px] shrink-0">{flagEmoji(sim.locationCode)}</span>
            <span className="text-[15px] font-semibold text-[var(--tg-theme-text-color)] truncate">
              {sim.packageName}
            </span>
          </div>
          <div className="text-xs text-[var(--tg-theme-hint-color)] mt-px">
            {fmtDate(sim.createdAt)}
            {sim.expireAt && sim.status === 'ready' && (
              <> · {t('myesim_expires')} {fmtDate(sim.expireAt)}</>
            )}
          </div>
        </div>

        <span className={`text-[12px] font-semibold shrink-0 ${statusClass}`}>
          {statusLabel}
        </span>
      </div>

      {/* Data usage bar */}
      {sim.status === 'ready' && sim.totalBytes > 0 && (
        <div className="px-4 pb-[13px]">
          <div className="h-[3px] rounded-full bg-[var(--card-border)] overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${sim.usedPct > 80 ? 'bg-danger' : 'bg-primary'}`}
              style={{ width: `${Math.min(sim.usedPct, 100)}%` }}
            />
          </div>
          <div className="text-[11px] text-[var(--tg-theme-hint-color)] mt-1 text-right">
            {fmtBytes(sim.usedBytes)} / {fmtBytes(sim.totalBytes)} {t('myesim_used')}
          </div>
        </div>
      )}

      {/* Pending / error message */}
      {sim.status === 'pending' && (
        <div className="px-4 pb-[13px] text-[13px] text-[var(--tg-theme-hint-color)]">
          SIM готовится — уведомим в боте как только будет готова
        </div>
      )}
      {sim.status === 'failed' && (
        <div className="px-4 pb-[13px] text-[13px] text-[var(--tg-theme-hint-color)]">
          При выпуске возникла ошибка. Напиши в поддержку — разберёмся.
        </div>
      )}

      {/* Activation details */}
      {sim.status === 'ready' && (sim.iccid || sim.ac || sim.qrUrl) && (
        <div className="border-t border-[var(--card-border)] px-4 pt-1 pb-3">
          {sim.iccid && <CopyRow label="ICCID" value={sim.iccid} />}
          {sim.ac    && <CopyRow label="AC"    value={sim.ac} />}
          {sim.qrUrl && (
            <button
              onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); WebApp.openLink(sim.qrUrl!) }}
              className="mt-1 w-full py-[9px] rounded-[10px] border-none bg-primary text-white text-[13px] font-semibold cursor-pointer"
            >
              📷 {t('myesim_show_qr')}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

export default function MyESims() {
  const nav     = useNavigate()
  const t       = useT()
  const [sims, setSims]       = useState<MyESim[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState('')

  useEffect(() => {
    WebApp.BackButton.show()
    const goBack = () => nav('/esim')
    WebApp.BackButton.onClick(goBack)
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(goBack) }
  }, [nav])

  useEffect(() => {
    getMyESims()
      .then(setSims)
      .catch(() => setError(t('esim_err_load')))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="page pb-[calc(env(safe-area-inset-bottom)+96px)] gap-2.5">

      {loading && (
        <p className="text-[var(--tg-theme-hint-color)] text-center text-sm py-10">
          Загружаем…
        </p>
      )}

      {error && (
        <p className="text-[var(--tg-theme-hint-color)] text-center text-sm py-10">{error}</p>
      )}

      {!loading && !error && sims.length === 0 && (
        <div className="fade-in flex flex-col items-center px-6 pt-12 pb-6 text-center gap-3">
          <div className="w-[72px] h-[72px] rounded-[20px] bg-primary/10 flex items-center justify-center text-[32px]">
            📱
          </div>
          <div className="font-bold text-[18px] text-[var(--tg-theme-text-color)]">
            {t('myesim_empty')}
          </div>
          <div className="text-[14px] text-[var(--tg-theme-hint-color)] max-w-[260px] leading-[1.5]">
            {t('myesim_empty_sub')}
          </div>
          <button
            onClick={() => { WebApp.HapticFeedback.impactOccurred('medium'); nav('/esim') }}
            className="mt-2 py-[13px] px-8 rounded-[14px] border-none bg-[var(--tg-theme-button-color,#2481cc)] text-[var(--tg-theme-button-text-color,#fff)] font-bold text-[15px] cursor-pointer"
          >
            {t('myesim_buy')}
          </button>
        </div>
      )}

      {!loading && sims.map(sim => (
        <ESimCard key={sim.id} sim={sim} t={t} />
      ))}
    </div>
  )
}

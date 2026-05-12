import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import { getMyESims, type MyESim } from '../api'
import { useT } from '../i18n'

function flagEmoji(code: string | null): string {
  if (!code) return '🌐'
  if (code === 'EU-42') return '🇪🇺'
  if (!/^[A-Z]{2}$/i.test(code)) return '🌐'
  return [...code.toUpperCase()].map(c => String.fromCodePoint(0x1f1e6 + c.charCodeAt(0) - 65)).join('')
}

function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true)
      WebApp.HapticFeedback.notificationOccurred('success')
      setTimeout(() => setCopied(false), 1500)
    })
  }
  return (
    <button
      onClick={copy}
      style={{
        border: 'none', background: 'none', padding: '2px 6px',
        borderRadius: 6, cursor: 'pointer',
        fontSize: 11, fontWeight: 600,
        color: copied ? 'var(--tg-theme-link-color,#2481cc)' : 'var(--tg-theme-hint-color)',
        transition: 'color 0.2s',
        flexShrink: 0,
      }}
    >
      {copied ? '✓' : label}
    </button>
  )
}

function ESimCard({ sim, t }: { sim: MyESim; t: (k: string) => string }) {
  const statusColor =
    sim.status === 'ready'   ? '#34c759' :
    sim.status === 'pending' ? '#ff9500' : '#ff3b30'

  const statusLabel =
    sim.status === 'ready'   ? t('myesim_status_ready') :
    sim.status === 'pending' ? t('myesim_status_pending') : t('myesim_status_failed')

  return (
    <div style={{
      background: 'var(--tg-theme-section-bg-color)',
      border: '1px solid var(--card-border)',
      borderRadius: 16,
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{ padding: '14px 16px 12px', display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <span style={{ fontSize: 32, lineHeight: 1, flexShrink: 0, marginTop: 2 }}>
          {flagEmoji(sim.locationCode)}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <div style={{
              fontWeight: 700, fontSize: 15,
              color: 'var(--tg-theme-text-color)',
              flex: 1, minWidth: 0,
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              {sim.packageName}
            </div>
            <div style={{
              fontSize: 12, fontWeight: 600,
              color: statusColor,
              flexShrink: 0,
            }}>
              {statusLabel}
            </div>
          </div>
          <div style={{ fontSize: 12, color: 'var(--tg-theme-hint-color)', marginTop: 2 }}>
            {fmtDate(sim.createdAt)}
            {sim.expireAt && sim.status === 'ready' && (
              <> · {t('myesim_expires')} {fmtDate(sim.expireAt)}</>
            )}
          </div>
        </div>
      </div>

      {/* Data usage */}
      {sim.status === 'ready' && sim.totalBytes > 0 && (
        <div style={{ padding: '0 16px 14px' }}>
          <div style={{
            height: 4, borderRadius: 2,
            background: 'var(--card-border)',
            overflow: 'hidden', marginBottom: 5,
          }}>
            <div style={{
              height: '100%',
              width: `${Math.min(sim.usedPct, 100)}%`,
              background: sim.usedPct > 80 ? '#ff3b30' : 'var(--tg-theme-button-color,#2481cc)',
              borderRadius: 2,
              transition: 'width 0.4s',
            }} />
          </div>
          <div style={{ fontSize: 11, color: 'var(--tg-theme-hint-color)', textAlign: 'right' }}>
            {fmtBytes(sim.usedBytes)} / {fmtBytes(sim.totalBytes)} {t('myesim_used')}
          </div>
        </div>
      )}

      {/* Pending message */}
      {sim.status === 'pending' && (
        <div style={{
          padding: '0 16px 14px',
          fontSize: 13, color: 'var(--tg-theme-hint-color)',
        }}>
          SIM готовится — уведомим в боте как только будет готова
        </div>
      )}

      {/* Error message */}
      {sim.status === 'failed' && (
        <div style={{
          padding: '0 16px 14px',
          fontSize: 13, color: 'var(--tg-theme-hint-color)',
        }}>
          При выпуске возникла ошибка. Напиши в поддержку — разберёмся.
        </div>
      )}

      {/* Activation details */}
      {sim.status === 'ready' && (sim.iccid || sim.ac || sim.qrUrl) && (
        <div style={{
          borderTop: '1px solid var(--card-border)',
          padding: '10px 16px 12px',
          display: 'flex', flexDirection: 'column', gap: 6,
        }}>
          {sim.iccid && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 11, color: 'var(--tg-theme-hint-color)', width: 42, flexShrink: 0 }}>ICCID</span>
              <span style={{
                fontSize: 11, fontFamily: 'monospace',
                color: 'var(--tg-theme-text-color)',
                flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {sim.iccid}
              </span>
              <CopyButton value={sim.iccid} label="copy" />
            </div>
          )}
          {sim.ac && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 11, color: 'var(--tg-theme-hint-color)', width: 42, flexShrink: 0 }}>AC</span>
              <span style={{
                fontSize: 11, fontFamily: 'monospace',
                color: 'var(--tg-theme-text-color)',
                flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {sim.ac}
              </span>
              <CopyButton value={sim.ac} label="copy" />
            </div>
          )}
          {sim.qrUrl && (
            <button
              onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); WebApp.openLink(sim.qrUrl!) }}
              style={{
                marginTop: 4,
                padding: '9px 16px', borderRadius: 10, border: 'none',
                background: 'var(--tg-theme-button-color,#2481cc)',
                color: 'var(--tg-theme-button-text-color,#fff)',
                fontWeight: 600, fontSize: 13, cursor: 'pointer',
                width: '100%',
              }}
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
  const nav  = useNavigate()
  const t    = useT()
  const [sims, setSims]     = useState<MyESim[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState('')

  useEffect(() => {
    WebApp.BackButton.show()
    const goBack = () => nav('/esim')
    WebApp.BackButton.onClick(goBack)
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(goBack) }
  }, [nav])

  useEffect(() => {
    getMyESims()
      .then(setSims)
      .catch(() => setError('Не удалось загрузить список eSIM'))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="page" style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 96px)', gap: 10 }}>
      {loading && (
        <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--tg-theme-hint-color)', fontSize: 14 }}>
          Загружаем…
        </div>
      )}

      {error && (
        <div style={{
          textAlign: 'center', padding: '40px 24px',
          color: 'var(--tg-theme-hint-color)', fontSize: 14,
        }}>
          {error}
        </div>
      )}

      {!loading && !error && sims.length === 0 && (
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center',
          padding: '48px 24px', gap: 12, textAlign: 'center',
        }}>
          <div style={{
            width: 72, height: 72, borderRadius: 20,
            background: 'rgba(0,122,255,0.10)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 32,
          }}>
            📱
          </div>
          <div style={{ fontWeight: 700, fontSize: 18, color: 'var(--tg-theme-text-color)' }}>
            {t('myesim_empty')}
          </div>
          <div style={{ fontSize: 14, color: 'var(--tg-theme-hint-color)', maxWidth: 260, lineHeight: 1.5 }}>
            {t('myesim_empty_sub')}
          </div>
          <button
            onClick={() => { WebApp.HapticFeedback.impactOccurred('medium'); nav('/esim') }}
            style={{
              marginTop: 8,
              padding: '13px 32px', borderRadius: 12, border: 'none',
              background: 'var(--tg-theme-button-color,#2481cc)',
              color: 'var(--tg-theme-button-text-color,#fff)',
              fontWeight: 700, fontSize: 15, cursor: 'pointer',
            }}
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

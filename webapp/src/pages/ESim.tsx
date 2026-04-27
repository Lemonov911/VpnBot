import { useEffect, useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import { getESimCountries, type Country } from '../api'
import { useT, usePlural } from '../i18n'

function flagEmoji(code: string): string {
  if (!/^[A-Z]{2}$/i.test(code)) return '🌐'
  return [...code.toUpperCase()].map(c => String.fromCodePoint(0x1f1e6 + c.charCodeAt(0) - 65)).join('')
}

export default function ESim() {
  const nav = useNavigate()
  const t = useT()
  const plural = usePlural()
  const [countries, setCountries] = useState<Country[]>([])
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState('')
  const [search, setSearch]       = useState('')
  const [tab, setTab]             = useState<'ru' | 'travel'>('ru')

  useEffect(() => {
    WebApp.BackButton.show()
    WebApp.BackButton.onClick(() => nav('/'))
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(() => nav('/')) }
  }, [nav])

  useEffect(() => {
    getESimCountries()
      .then(setCountries)
      .catch(() => setError(t('esim_err_load')))
      .finally(() => setLoading(false))
  }, [])

  const travelCountries = useMemo(() => {
    const q = search.trim().toLowerCase()
    return countries
      .filter(c => c.code !== 'RU')
      .filter(c => !q || c.name.toLowerCase().includes(q))
  }, [countries, search])

  const ruEntry = useMemo(() => countries.find(c => c.code === 'RU'), [countries])

  const goToCountry = (code: string, name: string, ruCompatible = false) =>
    nav(`/esim/${code}`, { state: { name, ruCompatible } })

  return (
    <div className="page" style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 90px)' }}>

      {/* Header */}
      <div style={{ padding: '6px 4px 2px' }}>
        <div style={{ fontWeight: 800, fontSize: 24, color: 'var(--text)', marginBottom: 4 }}>eSIM</div>
        <div style={{ fontSize: 13, color: 'var(--hint)' }}>{t('esim_sub')}</div>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          onClick={() => setTab('ru')}
          style={{
            flex: 1, padding: '10px 0', borderRadius: 12, border: 'none',
            background: tab === 'ru' ? 'var(--btn)' : 'var(--section-bg)',
            color: tab === 'ru' ? 'var(--btn-text)' : 'var(--text)',
            fontWeight: 600, fontSize: 14, cursor: 'pointer',
          }}
        >
          {t('esim_tab_ru')}
        </button>
        <button
          onClick={() => setTab('travel')}
          style={{
            flex: 1, padding: '10px 0', borderRadius: 12, border: 'none',
            background: tab === 'travel' ? 'var(--btn)' : 'var(--section-bg)',
            color: tab === 'travel' ? 'var(--btn-text)' : 'var(--text)',
            fontWeight: 600, fontSize: 14, cursor: 'pointer',
          }}
        >
          {t('esim_tab_travel')}
        </button>
      </div>

      {/* ── TAB: Russia ─────────────────────────────────── */}
      {tab === 'ru' && (
        <>
          {/* How it works */}
          <div style={{ background: 'var(--section-bg)', border: '1px solid var(--card-border)', borderRadius: 16, overflow: 'hidden' }}>
            {[
              { color: '#2481cc', title: t('esim_insert'), sub: t('esim_insert_sub'), icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><rect x="5" y="2" width="14" height="20" rx="2" stroke="#fff" strokeWidth="2"/><path d="M9 8h6M9 12h6M9 16h4" stroke="#fff" strokeWidth="1.5" strokeLinecap="round"/></svg> },
              { color: '#27ae60', title: t('esim_traffic'), sub: t('esim_traffic_sub'), icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="#fff" strokeWidth="2"/><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" stroke="#fff" strokeWidth="2"/></svg> },
              { color: '#8e44ad', title: t('esim_calls'), sub: t('esim_calls_sub'), icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 12a19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 3.6 1.27h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L7.91 8.91a16 16 0 0 0 6 6l.91-.91a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z" stroke="#fff" strokeWidth="2"/></svg> },
            ].map(({ color, title, sub, icon }, i, arr) => (
              <div key={i} style={{ padding: '13px 16px', display: 'flex', alignItems: 'center', gap: 14, borderBottom: i < arr.length - 1 ? '1px solid rgba(128,128,128,0.1)' : 'none' }}>
                <div style={{ width: 36, height: 36, borderRadius: 10, flexShrink: 0, background: color, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{icon}</div>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)', lineHeight: 1.3 }}>{title}</div>
                  <div style={{ fontSize: 12, color: 'var(--hint)', marginTop: 2 }}>{sub}</div>
                </div>
              </div>
            ))}
          </div>

          {loading && <p style={{ color: 'var(--hint)', textAlign: 'center', padding: 24 }}>{t('esim_loading')}</p>}

          {/* Russia entry */}
          {ruEntry && (
            <div style={{ background: 'var(--section-bg)', border: '1px solid var(--card-border)', borderRadius: 16, overflow: 'hidden' }}>
              <div
                onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); goToCountry('RU', t('esim_russia'), true) }}
                style={{ padding: '13px 16px', display: 'flex', alignItems: 'center', gap: 14, cursor: 'pointer' }}
              >
                <span style={{ fontSize: 28, width: 36, textAlign: 'center', flexShrink: 0 }}>🇷🇺</span>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)' }}>{t('esim_russia')}</div>
                  <div style={{ fontSize: 12, color: 'var(--hint)', marginTop: 1 }}>{plural(ruEntry.count, { ru: ['пакет', 'пакета', 'пакетов'], en: 'packages' })} · {t('esim_russia_ip')}</div>
                </div>
                <svg width="7" height="12" viewBox="0 0 7 12" fill="none">
                  <path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </div>
            </div>
          )}

          {/* FAQ */}
          <div
            onClick={() => nav('/esim/faq')}
            style={{ background: 'var(--section-bg)', border: '1px solid var(--card-border)', borderRadius: 16, overflow: 'hidden', cursor: 'pointer' }}
          >
            <div style={{ padding: '13px 16px', display: 'flex', alignItems: 'center', gap: 14 }}>
              <div style={{ width: 36, height: 36, borderRadius: 10, flexShrink: 0, background: '#e67e22', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                  <path d="M12 22C6.48 22 2 17.52 2 12S6.48 2 12 2s10 4.48 10 10-4.48 10-10 10z" stroke="#fff" strokeWidth="2"/>
                  <path d="M12 8c0-1.1.9-2 2-2s2 .9 2 2c0 1.5-2 2-2 3" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
                  <circle cx="12" cy="17" r="1" fill="#fff"/>
                </svg>
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)' }}>{t('esim_faq_title')}</div>
                <div style={{ fontSize: 12, color: 'var(--hint)', marginTop: 1 }}>{t('esim_faq_sub')}</div>
              </div>
              <svg width="7" height="12" viewBox="0 0 7 12" fill="none"><path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>
            </div>
          </div>
        </>
      )}

      {/* ── TAB: Travel ─────────────────────────────────── */}
      {tab === 'travel' && (
        <>
          <input
            type="search"
            placeholder={t('esim_search')}
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{
              width: '100%', padding: '12px 14px', borderRadius: 12, border: 'none',
              background: 'var(--section-bg)', color: 'var(--text)', fontSize: 15, outline: 'none',
            }}
          />

          {loading && <p style={{ color: 'var(--hint)', textAlign: 'center', padding: 24 }}>{t('esim_loading')}</p>}
          {error   && <p style={{ color: 'var(--tg-theme-destructive-text-color,#ff3b30)', textAlign: 'center' }}>{error}</p>}

          <div style={{ background: 'var(--section-bg)', border: '1px solid var(--card-border)', borderRadius: 16, overflow: 'hidden' }}>
            {travelCountries.map((c, i) => (
              <div
                key={c.code}
                onClick={() => goToCountry(c.code, c.name)}
                style={{
                  padding: '13px 16px', display: 'flex', alignItems: 'center', gap: 14,
                  cursor: 'pointer',
                  borderBottom: i < travelCountries.length - 1 ? '1px solid rgba(128,128,128,0.1)' : 'none',
                }}
              >
                <span style={{ fontSize: 30, width: 36, textAlign: 'center', flexShrink: 0 }}>{flagEmoji(c.code)}</span>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)' }}>{c.name}</div>
                  <div style={{ fontSize: 12, color: 'var(--hint)', marginTop: 1 }}>{plural(c.count, { ru: ['пакет', 'пакета', 'пакетов'], en: 'packages' })}</div>
                </div>
                <svg width="7" height="12" viewBox="0 0 7 12" fill="none"><path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>
              </div>
            ))}
          </div>

          {!loading && !error && travelCountries.length === 0 && (
            <p style={{ color: 'var(--hint)', textAlign: 'center', padding: 24 }}>{t('esim_no_results')}</p>
          )}
        </>
      )}
    </div>
  )
}
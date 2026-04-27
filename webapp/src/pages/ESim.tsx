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
    const goBack = () => nav('/')
    WebApp.BackButton.onClick(goBack)
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(goBack) }
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
    <div className="page" style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 96px)' }}>

      <div className="px-1 pt-1.5 pb-0.5 flex items-center gap-2.5">
        <img src={import.meta.env.BASE_URL + 'logo.webp'} alt="MAX" className="w-9 h-9 rounded-[10px] object-cover shrink-0" />
        <div>
          <div className="text-2xl font-extrabold text-[var(--tg-theme-text-color)]">eSIM</div>
          <div className="text-[13px] text-[var(--tg-theme-hint-color)]">{t('esim_sub')}</div>
        </div>
      </div>

      <div className="flex gap-2">
        <button
          onClick={() => setTab('ru')}
          className={`flex-1 py-2.5 rounded-xl border-none font-semibold text-sm cursor-pointer ${
            tab === 'ru'
              ? 'bg-[var(--tg-theme-button-color,#2481cc)] text-[var(--tg-theme-button-text-color,#fff)]'
              : 'bg-[var(--tg-theme-section-bg-color)] text-[var(--tg-theme-text-color)]'
          }`}
        >
          {t('esim_tab_ru')}
        </button>
        <button
          onClick={() => setTab('travel')}
          className={`flex-1 py-2.5 rounded-xl border-none font-semibold text-sm cursor-pointer ${
            tab === 'travel'
              ? 'bg-[var(--tg-theme-button-color,#2481cc)] text-[var(--tg-theme-button-text-color,#fff)]'
              : 'bg-[var(--tg-theme-section-bg-color)] text-[var(--tg-theme-text-color)]'
          }`}
        >
          {t('esim_tab_travel')}
        </button>
      </div>

      {tab === 'ru' && (
        <>
          <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
            {[
              { color: 'bg-primary', title: t('esim_insert'), sub: t('esim_insert_sub'), icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><rect x="5" y="2" width="14" height="20" rx="2" stroke="#fff" strokeWidth="2"/><path d="M9 8h6M9 12h6M9 16h4" stroke="#fff" strokeWidth="1.5" strokeLinecap="round"/></svg> },
              { color: 'bg-success', title: t('esim_traffic'), sub: t('esim_traffic_sub'), icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="#fff" strokeWidth="2"/><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" stroke="#fff" strokeWidth="2"/></svg> },
              { color: 'bg-purple', title: t('esim_calls'), sub: t('esim_calls_sub'), icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 12a19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 3.6 1.27h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L7.91 8.91a16 16 0 0 0 6 6l.91-.91a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z" stroke="#fff" strokeWidth="2"/></svg> },
            ].map(({ color, title, sub, icon }, i, arr) => (
              <div key={i} className={`py-[13px] px-4 flex items-center gap-[14px] ${i < arr.length - 1 ? 'border-b border-solid border-[var(--card-border)]' : ''}`}>
                <div className={`w-9 h-9 rounded-[10px] shrink-0 ${color} flex items-center justify-center`}>{icon}</div>
                <div>
                  <div className="text-sm font-semibold text-[var(--tg-theme-text-color)] leading-snug">{title}</div>
                  <div className="text-xs text-[var(--tg-theme-hint-color)] mt-0.5">{sub}</div>
                </div>
              </div>
            ))}
          </div>

          {loading && <p className="text-[var(--tg-theme-hint-color)] text-center py-6">{t('esim_loading')}</p>}

          {ruEntry && (
            <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
              <div
                onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); goToCountry('RU', t('esim_russia'), true) }}
                className="py-[13px] px-4 flex items-center gap-[14px] cursor-pointer"
              >
                <span className="text-[28px] w-9 text-center shrink-0">🇷🇺</span>
                <div className="flex-1">
                  <div className="text-[15px] font-semibold text-[var(--tg-theme-text-color)]">{t('esim_russia')}</div>
                  <div className="text-xs text-[var(--tg-theme-hint-color)] mt-px">{plural(ruEntry.count, { ru: ['пакет', 'пакета', 'пакетов'], en: ['package', 'packages'] })} · {t('esim_russia_ip')}</div>
                </div>
                <svg width="7" height="12" viewBox="0 0 7 12" fill="none">
                  <path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </div>
            </div>
          )}

          <div
            onClick={() => nav('/esim/faq')}
            className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden cursor-pointer"
          >
            <div className="py-[13px] px-4 flex items-center gap-[14px]">
              <div className="w-9 h-9 rounded-[10px] shrink-0 bg-warning flex items-center justify-center">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                  <path d="M12 22C6.48 22 2 17.52 2 12S6.48 2 12 2s10 4.48 10 10-4.48 10-10 10z" stroke="#fff" strokeWidth="2"/>
                  <path d="M12 8c0-1.1.9-2 2-2s2 .9 2 2c0 1.5-2 2-2 3" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
                  <circle cx="12" cy="17" r="1" fill="#fff"/>
                </svg>
              </div>
              <div className="flex-1">
                <div className="text-[15px] font-semibold text-[var(--tg-theme-text-color)]">{t('esim_faq_title')}</div>
                <div className="text-xs text-[var(--tg-theme-hint-color)] mt-px">{t('esim_faq_sub')}</div>
              </div>
              <svg width="7" height="12" viewBox="0 0 7 12" fill="none"><path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>
            </div>
          </div>
        </>
      )}

      {tab === 'travel' && (
        <>
          <input
            type="search"
            placeholder={t('esim_search')}
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full py-3 px-[14px] rounded-xl border-none bg-[var(--tg-theme-section-bg-color)] text-[var(--tg-theme-text-color)] text-[15px] outline-none"
          />

          {loading && <p className="text-[var(--tg-theme-hint-color)] text-center py-6">{t('esim_loading')}</p>}
          {error   && <p className="text-[var(--tg-theme-destructive-text-color,#ff3b30)] text-center">{error}</p>}

          <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
            {travelCountries.map((c, i) => (
              <div
                key={c.code}
                onClick={() => goToCountry(c.code, c.name)}
                className={`py-[13px] px-4 flex items-center gap-[14px] cursor-pointer ${i < travelCountries.length - 1 ? 'border-b border-solid border-[var(--card-border)]' : ''}`}
              >
                <span className="text-[30px] w-9 text-center shrink-0">{flagEmoji(c.code)}</span>
                <div className="flex-1">
                  <div className="text-[15px] font-semibold text-[var(--tg-theme-text-color)]">{c.name}</div>
                  <div className="text-xs text-[var(--tg-theme-hint-color)] mt-px">{plural(c.count, { ru: ['пакет', 'пакета', 'пакетов'], en: ['package', 'packages'] })}</div>
                </div>
                <svg width="7" height="12" viewBox="0 0 7 12" fill="none"><path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>
              </div>
            ))}
          </div>

          {!loading && !error && travelCountries.length === 0 && (
            <p className="text-[var(--tg-theme-hint-color)] text-center py-6">{t('esim_no_results')}</p>
          )}
        </>
      )}
    </div>
  )
}
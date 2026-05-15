import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, useLocation } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import { LanguageProvider, useT } from './i18n'
import BottomNav from './components/BottomNav'
import LangSwitch from './components/LangSwitch'
import ErrorBoundary from './components/ErrorBoundary'

import Home         from './pages/Home'
import VPN          from './pages/VPN'
import Plans        from './pages/Plans'
import Configs      from './pages/Configs'
import Instructions from './pages/Instructions'
import ESim         from './pages/ESim'
import ESimCountry  from './pages/ESimCountry'
import ESimFAQ      from './pages/ESimFAQ'
import MyESims      from './pages/MyESims'
import Support      from './pages/Support'
import Referral     from './pages/Referral'
import Status            from './pages/Status'
import StatusIncidents   from './pages/StatusIncidents'

function GlobalHeader() {
  const t    = useT()
  const { pathname } = useLocation()

  const info: Record<string, { title: string; sub: string }> = {
    '/':             { title: t('home_hero_title'),  sub: t('home_hero_sub').split('\n')[0] },
    '/vpn':          { title: t('nav_vpn'),          sub: t('vpn_sub') },
    '/vpn/plans':    { title: t('plans_title').replace(/^\S+\s/, ''), sub: '' },
    '/configs':      { title: t('configs_title').replace(/^\S+\s/, ''), sub: '' },
    '/instructions': { title: t('instr_title'),      sub: '' },
    '/esim':         { title: t('esim_title').replace(/^\S+\s/, ''), sub: t('esim_sub') },
    '/esim/my':      { title: t('myesim_title'), sub: '' },
    '/esim/faq':     { title: 'FAQ', sub: '' },
    '/support':      { title: t('support_title'),    sub: t('support_sub') },
    '/referral':     { title: t('ref_title'),        sub: t('ref_sub') },
    '/status':             { title: 'Статус сервисов',     sub: '' },
    '/status/incidents':   { title: 'История инцидентов',   sub: '' },
  }

  const page = info[pathname] ?? info['/']

  return (
    <div
      className="fixed top-0 left-0 right-0 z-[100] flex items-start gap-3 px-3 h-[52px] pt-3"
      style={{ background: 'var(--tg-theme-bg-color, #fff)' }}
    >
      <img
        src={import.meta.env.BASE_URL + 'logo.png'}
        alt="MAX"
        style={{ width: 32, height: 32, borderRadius: 9, objectFit: 'cover', flexShrink: 0 }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontWeight: 800, fontSize: 18, lineHeight: 1.2,
          color: 'var(--tg-theme-text-color)',
          letterSpacing: '-0.2px',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {page.title}
        </div>
        {page.sub && (
          <div style={{
            fontSize: 12, lineHeight: 1.2, marginTop: 1,
            color: 'var(--tg-theme-hint-color)',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>
            {page.sub}
          </div>
        )}
      </div>
      <LangSwitch />
    </div>
  )
}

export default function App() {
  useEffect(() => {
    WebApp.ready()
    WebApp.expand()
    const syncDark = () => {
      document.documentElement.classList.toggle('dark', WebApp.colorScheme === 'dark')
    }
    syncDark()
    WebApp.onEvent('themeChanged', syncDark)
    return () => WebApp.offEvent('themeChanged', syncDark)
  }, [])

  return (
    <LanguageProvider>
      <BrowserRouter>
        <GlobalHeader />

        <ErrorBoundary>
        <Routes>
          {/* VPN */}
          <Route path="/vpn"          element={<VPN />} />
          <Route path="/vpn/plans"    element={<Plans />} />
          <Route path="/configs"      element={<Configs />} />
          <Route path="/instructions" element={<Instructions />} />

          {/* eSIM — отключаемы через VITE_SHOW_ESIM=false. Без guard'а
              юзер мог вручную ввести /esim в URL и попасть на мёртвый
              функционал (API endpoints тоже guarded на бэкенде). */}
          {import.meta.env.VITE_SHOW_ESIM !== 'false' && <>
            <Route path="/esim"         element={<ESim />} />
            <Route path="/esim/my"      element={<MyESims />} />
            <Route path="/esim/faq"     element={<ESimFAQ />} />
            <Route path="/esim/:code"   element={<ESimCountry />} />
          </>}

          {/* Support & Referral */}
          <Route path="/support"      element={<Support />} />
          <Route path="/referral"     element={<Referral />} />

          {/* Public status page — no auth */}
          <Route path="/status"            element={<Status />} />
          <Route path="/status/incidents"  element={<StatusIncidents />} />

          {/* Главная */}
          <Route path="/"             element={<Home />} />
        </Routes>
        </ErrorBoundary>
        <BottomNav />
      </BrowserRouter>
    </LanguageProvider>
  )
}

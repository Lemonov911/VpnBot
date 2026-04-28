import { useEffect } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import { LanguageProvider } from './i18n'
import BottomNav from './components/BottomNav'
import LangSwitch from './components/LangSwitch'

import Home         from './pages/Home'
import VPN          from './pages/VPN'
import Plans        from './pages/Plans'
import Configs      from './pages/Configs'
import Instructions from './pages/Instructions'
import ESim         from './pages/ESim'
import ESimCountry  from './pages/ESimCountry'
import ESimFAQ      from './pages/ESimFAQ'
import Support      from './pages/Support'
import Referral     from './pages/Referral'

function GlobalHeader() {
  return (
    <div className="fixed top-0 left-0 right-0 z-[100] flex items-center px-3 h-[52px]"
      style={{ background: 'var(--tg-theme-bg-color, #fff)', borderBottom: '1px solid var(--card-border)' }}>
      <img
        src={import.meta.env.BASE_URL + 'logo.png'}
        alt="MAX"
        style={{ width: 32, height: 32, borderRadius: 9, objectFit: 'cover', flexShrink: 0 }}
      />
      <span style={{
        marginLeft: 9, fontWeight: 800, fontSize: 16,
        color: 'var(--tg-theme-text-color)',
        letterSpacing: '-0.3px', lineHeight: 1,
      }}>
        MAX VPN &amp; eSIM
      </span>
      <div style={{ marginLeft: 'auto' }}>
        <LangSwitch />
      </div>
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

        <Routes>
          {/* VPN */}
          <Route path="/vpn"          element={<VPN />} />
          <Route path="/vpn/plans"    element={<Plans />} />
          <Route path="/configs"      element={<Configs />} />
          <Route path="/instructions" element={<Instructions />} />

          {/* eSIM */}
          <Route path="/esim"         element={<ESim />} />
          <Route path="/esim/faq"     element={<ESimFAQ />} />
          <Route path="/esim/:code"   element={<ESimCountry />} />

          {/* Support & Referral */}
          <Route path="/support"      element={<Support />} />
          <Route path="/referral"     element={<Referral />} />

          {/* Главная */}
          <Route path="/"             element={<Home />} />
        </Routes>
        <BottomNav />
      </BrowserRouter>
    </LanguageProvider>
  )
}

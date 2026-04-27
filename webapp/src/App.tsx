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
        {/* Global language switcher — top-right on every page */}
        <div style={{
          position: 'fixed', top: 12, right: 16, zIndex: 200,
        }}>
          <LangSwitch />
        </div>

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

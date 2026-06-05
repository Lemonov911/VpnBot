import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, useLocation } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import { LanguageProvider, useT } from './i18n'
import { setNetworkAlertMessage } from './api'
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

  // W1 #3: подкидываем актуальный перевод network-alert в api/index.ts.
  // Локаль может смениться в рантайме через LangSwitch — useEffect отлавливает.
  useEffect(() => {
    setNetworkAlertMessage(t('bot_err_network'))
  }, [t])

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
    '/status':             { title: t('status_page_title'),     sub: '' },
    '/status/incidents':   { title: t('status_incidents_title'), sub: '' },
  }

  const page = info[pathname] ?? info['/']

  return (
    <div
      className="fixed top-0 left-0 right-0 z-[100] h-[52px] pt-3"
      style={{ background: 'var(--tg-theme-bg-color, #fff)' }}
    >
      <div className="max-w-[var(--app-max-width)] mx-auto flex items-start gap-3 px-3">
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
    </div>
  )
}

// iOS Safari quirk: без явного touch-listener'а на window CSS `:active`
// не срабатывает на тапах в Telegram WebView (transform/scale не отображается).
// Пустой passive-listener активирует event-режим — :active начинает работать.
if (typeof window !== 'undefined') {
  window.addEventListener('touchstart', () => {}, { passive: true })
}

export default function App() {
  useEffect(() => {
    WebApp.ready()
    WebApp.expand()

    const applyTheme = () => {
      // 1) dark-класс на <html> — для Tailwind dark:варианты
      document.documentElement.classList.toggle('dark', WebApp.colorScheme === 'dark')

      // 2) Telegram-хедер и фон должны совпадать с цветом нашего webview,
      //    иначе виден шов между Telegram-chrome и Mini App.
      //    Используем `bg_color` (= bg-color webview) для обоих —
      //    современные TG-клиенты понимают строковые шаблоны 'bg_color' /
      //    'secondary_bg_color', fallback — hex от themeParams.
      try {
        const bg = WebApp.themeParams.bg_color
        if (typeof WebApp.setHeaderColor === 'function') {
          WebApp.setHeaderColor(bg ? (bg as `#${string}`) : 'bg_color')
        }
        if (typeof WebApp.setBackgroundColor === 'function') {
          WebApp.setBackgroundColor(bg ? (bg as `#${string}`) : 'bg_color')
        }
        // BottomBar (TG 7.10+) — окраска нижней системной полоски (iOS home indicator).
        // SDK свежий, метод есть, но проверка typeof — на случай старых клиентов.
        if (typeof WebApp.setBottomBarColor === 'function') {
          const bottom = WebApp.themeParams.secondary_bg_color || bg
          WebApp.setBottomBarColor(bottom ? (bottom as `#${string}`) : 'secondary_bg_color')
        }
      } catch (e) {
        // Старые TG-клиенты могут не поддерживать setHeaderColor — silently ignore
        // eslint-disable-next-line no-console
        console.warn('theme apply failed:', e)
      }
    }

    applyTheme()
    WebApp.onEvent('themeChanged', applyTheme)
    return () => WebApp.offEvent('themeChanged', applyTheme)
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
              функционал (API endpoints тоже guarded на бэкенде).
              TODO (audit W1 #12): миграция на runtime-флаг через
              getFeatures() из api/index.ts (`/api/health` уже отдаёт
              `features.esim`, бэк-эндпоинт существует). Сейчас оставлено
              import-time, т.к. при выключении надо пересобирать webapp,
              и change-rate этого флага близкий к нулю. */}
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

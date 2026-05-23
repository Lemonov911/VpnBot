// ─── Shared plan metadata (W3 dedup) ─────────────────────────────────
// Раньше Plans.tsx и VPN.tsx держали свои копии PLAN_ICONS / PLAN_TW
// (skull-pixel-identical карты). Drift почти случился 22.05 — в Plans
// vpn_pro иконка обновилась, в VPN — нет. Теперь один источник правды.
//
// Импортируется в Plans.tsx, VPN.tsx (на 23.05). Если новый компонент
// нуждается в plan-визуальном — тоже импортит сюда, никаких локальных
// копий не плодим.

import type { JSX } from 'react'
import type { TKey } from '../i18n'

// ── Иконки в круглой плашке (bg-цвет + SVG) ──────────────────────────
// `bg` — hex для inline (используем там где Tailwind dynamic class не
// рендерится через JIT). `icon` — готовый JSX, белая обводка на цветной
// плашке. Размеры 18×18 / viewBox 24×24 — фиксированы во всех plan-card
// раскладках (44×44 контейнер с центрированием).
export const PLAN_ICONS: Record<string, { bg: string; icon: JSX.Element }> = {
  // v2 — по скорости (актуальные)
  vpn_base: { bg: '#2481cc', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg> },
  vpn_max:  { bg: '#af52de', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M13 2L3 14h7v8l10-12h-7V2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg> },
  // legacy — для юзеров с устаревшим планом в БД (vpn_start/popular/pro/family),
  // показываем их иконку чтобы карточка «Ваш текущий» не была дефолтной vpn_base.
  vpn_start:   { bg: '#5ac8fa', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg> },
  vpn_popular: { bg: '#2481cc', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg> },
  vpn_pro:     { bg: '#5856d6', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" fill="#ffffff33" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg> },
  vpn_family:  { bg: '#ff2d55', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><circle cx="9" cy="7" r="3" stroke="#fff" strokeWidth="2"/><path d="M3 19c0-3 2.686-5 6-5s6 2 6 5" stroke="#fff" strokeWidth="2" strokeLinecap="round"/><circle cx="17" cy="7" r="2.5" stroke="#fff" strokeWidth="1.8"/><path d="M21 19c0-2.5-1.8-4-4-4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/></svg> },
}

// ── Tailwind background + shadow для plan-плашки (44×44).
// `bg-[#hex]` arbitrary value — Tailwind JIT генерит class на сборку. Для
// vpn_base и vpn_popular используем `bg-primary` чтобы через --color-primary
// автоматически подхватывался --tg-theme-button-color (Telegram дарк/лайт).
// vpn_max — `glow-pulse` (дышащая тень, см. @keyframes в index.css).
export const PLAN_TW: Record<string, { bg: string; shadow: string }> = {
  vpn_base: { bg: 'bg-primary',    shadow: 'shadow-[0_4px_12px_rgba(36,129,204,0.55)]' },
  /* glow-pulse — дышащая тень на Max-плитке (рекомендованный план).
     Не моргает, едва заметно (см. @keyframes glow-pulse в index.css). */
  vpn_max:  { bg: 'bg-[#af52de]',  shadow: 'glow-pulse' },
  // legacy — те же три legacy ключа что в PLAN_ICONS
  vpn_start:   { bg: 'bg-info',       shadow: 'shadow-[0_4px_12px_rgba(90,200,250,0.55)]' },
  vpn_popular: { bg: 'bg-primary',    shadow: 'shadow-[0_4px_12px_rgba(36,129,204,0.55)]' },
  vpn_pro:     { bg: 'bg-[#5856d6]',   shadow: 'shadow-[0_4px_12px_rgba(88,86,214,0.55)]' },
  vpn_family:  { bg: 'bg-[#ff2d55]',   shadow: 'shadow-[0_4px_12px_rgba(255,45,85,0.55)]' },
}

// ── Имя плана как i18n-ключ (резолвится t() в месте использования). ──
// Раньше VPN.tsx собирал готовый Record<string, string> через t(), а
// Plans.tsx — Record<string, TKey> и звал t() на сайте. Теперь у нас
// один источник правды (ключи), а резолв оставлен компоненту — это
// сохраняет hooks-правильность (нельзя звать t() вне React-функции).
export const PLAN_NAME_KEY: Record<string, TKey> = {
  vpn_base:        'vpn_plan_base',
  vpn_base_3m:     'vpn_plan_base_3m',
  vpn_base_6m:     'vpn_plan_base_6m',
  vpn_base_12m:    'vpn_plan_base_12m',
  vpn_max:         'vpn_plan_max',
  vpn_max_3m:      'vpn_plan_max_3m',
  vpn_max_6m:      'vpn_plan_max_6m',
  vpn_max_12m:     'vpn_plan_max_12m',
  vpn_trial:       'vpn_plan_trial',
  vpn_start:       'vpn_plan_start',
  vpn_popular:     'vpn_plan_popular',
  vpn_pro:         'vpn_plan_pro',
  vpn_family:      'vpn_plan_family',
}

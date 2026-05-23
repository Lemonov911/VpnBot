import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import {
  getActiveSubscription, getUserStats,
  getTrialStatus, claimTrial,
  redeemReferralBonus,
  type Subscription, type UserStats, type TrialStatus,
} from '../api'
import { useT, usePlural, useLang } from '../i18n'
import TrialSuccessSheet from '../components/TrialSuccessSheet'
import { SkeletonPage } from '../components/Skeleton'

// "2026-05-30 21:00:58" / "2026-05-30T21:00:58.123" → "30 мая" / "May 30"
function formatNiceDate(iso: string, lang: 'ru' | 'en'): string {
  try {
    // SQLite формат может быть с пробелом → даже встроенный Date парсит ISO.
    const d = new Date(iso.replace(' ', 'T'))
    if (isNaN(d.getTime())) return ''
    return new Intl.DateTimeFormat(lang === 'ru' ? 'ru-RU' : 'en-US', {
      day: 'numeric', month: 'short', year: 'numeric',
    }).format(d)
  } catch { return '' }
}

// Feature flag — VITE_SHOW_ESIM=false скрывает eSIM-блок (parity с BottomNav).
const SHOW_ESIM = import.meta.env.VITE_SHOW_ESIM !== 'false'

export default function Home() {
  const nav    = useNavigate()
  const t      = useT()
  const p      = usePlural()
  const { lang } = useLang()

  const [sub,       setSub]       = useState<Subscription | null | undefined>(undefined)
  const [stats,     setStats]     = useState<UserStats | null>(null)
  const [trial,     setTrial]     = useState<TrialStatus | null>(null)
  const [claiming,  setClaiming]  = useState(false)
  const [trialErr,  setTrialErr]  = useState('')
  const [trialDone, setTrialDone] = useState(false)
  const [trialSheet, setTrialSheet] = useState(false)

  const busyRef = useRef(false)
  const mountedRef = useRef(true)
  const [redeeming, setRedeeming] = useState(false)
  useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false } }, [])

  // Activate accumulated referral bonus days on the current subscription.
  // Backend redeem_referral_bonus() требует active/grace sub — иначе 400.
  // На Home показываем кнопку только когда обе условия выполнены, без sub
  // её вообще не рисуем (или дисэйблим с подсказкой).
  const handleRedeemBonus = async () => {
    if (redeeming || !stats || stats.bonus_days <= 0) return
    if (!sub) {
      WebApp.showAlert(t('home_bonus_redeem_no_sub' as never))
      return
    }
    setRedeeming(true)
    WebApp.HapticFeedback.impactOccurred('medium')
    try {
      const res = await redeemReferralBonus()
      WebApp.HapticFeedback.notificationOccurred('success')
      const dateStr = new Date(res.new_expires_at).toLocaleDateString(
        lang === 'en' ? 'en-US' : 'ru-RU',
        { day: '2-digit', month: 'long', year: 'numeric' })
      WebApp.showAlert(
        (t('home_bonus_redeem_done' as never) as string)
          .replace('{days}', String(res.days_applied))
          .replace('{date}', dateStr)
      )
      // refresh sub + stats после redeem.
      // W2 bonus: критичная sub-refresh после успешного redeem — если
      // network fail, юзер видит stale expires_at и думает что redeem не
      // прошёл (хотя на бэке всё OK). Алертим, но не падаем — redeem уже
      // done на бэке.
      const [s, st] = await Promise.all([
        getActiveSubscription().catch(e => {
          console.error('home_redeem_refresh_sub', e)
          return null
        }),
        getUserStats().catch(e => {
          console.error('home_redeem_refresh_stats', e)
          return null
        }),
      ])
      if (mountedRef.current) {
        setSub(s)
        setStats(st)
      }
    } catch {
      WebApp.HapticFeedback.notificationOccurred('error')
      WebApp.showAlert(t('home_bonus_redeem_err' as never))
    } finally {
      if (mountedRef.current) setRedeeming(false)
    }
  }

  useEffect(() => {
    // W2 #10 — mountedRef.current check ВЕЗДЕ. `cancelled` локальный был
    // OK для самого useEffect, но handleRedeemBonus / handleClaimTrial ниже
    // используют mountedRef, и единый источник правды легче поддерживать.
    // VPN.tsx:171 — оригинальный шаблон с `cancelled` ref-флагом.
    let cancelled = false
    // MD-F-r2: distinguish transient errors (429 rate-limit) from genuine
    // "no subscription". Previously any error → setSub(null) → Home UI
    // collapses to the buy-flow CTA while the backend was just throttling
    // the visibility-refresh storm.
    getActiveSubscription().then(s => {
      if (cancelled || !mountedRef.current) return
      setSub(s)
    }).catch(e => {
      if (cancelled || !mountedRef.current) return
      if (e instanceof Error && e.message === 'rate_limit') return  // keep current state
      // W2 bonus: критичный поток (sub). Раньше silently глоталось — теперь
      // алертим юзеру что network не ОК, чтобы он не сидел на пустом Home.
      // 429 уже отфильтрован выше, остаются настоящие network/5xx — alert OK.
      setSub(null)
      WebApp.showAlert(t('bot_err_network' as never))
    })
    getUserStats()
      .then(s => { if (!cancelled && mountedRef.current) setStats(s) })
      .catch(e => {
        // W2 bonus: не-критичная метрика (stats). Не мешаем юзеру алертами,
        // просто логируем и продолжаем рендер без stats-карточек.
        console.error('home_stats_load', e)
      })
    getTrialStatus()
      .then(s => { if (!cancelled && mountedRef.current) setTrial(s) })
      .catch(e => {
        // W2 bonus: не-критичный trial probe. Если 500'нул — скрываем
        // trial-banner, юзер увидит обычный buy-flow CTA.
        console.error('home_trial_load', e)
      })
    return () => { cancelled = true }
  }, [t])

  // MD-F3: refresh sub/stats when user returns to tab. Other devices may
  // have purchased / cancelled / used a slot — without this the Home
  // header (plan badge, days_left, traffic stats) shows yesterday's state.
  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState !== 'visible') return
      // MD-F-r2: preserve cached sub on 429 — visibility refreshes can
      // hammer the backend rate-limit window.
      getActiveSubscription().then(s => {
        if (mountedRef.current) setSub(s)
      }).catch(e => {
        if (!mountedRef.current) return
        if (e instanceof Error && e.message === 'rate_limit') return
        // W2 #10 — visibility-refresh path. Не алертим тут (юзер не
        // инициировал действие — это фоновое обновление), просто скидываем
        // в null. Следующий focus попробует ещё раз.
        setSub(null)
      })
      // W2 bonus: visibility-refresh stats fetch. console.error и идём дальше.
      getUserStats()
        .then(s => { if (mountedRef.current) setStats(s) })
        .catch(e => { console.error('home_stats_refresh', e) })
    }
    document.addEventListener('visibilitychange', refresh)
    window.addEventListener('focus', refresh)
    return () => {
      document.removeEventListener('visibilitychange', refresh)
      window.removeEventListener('focus', refresh)
    }
  }, [])

  const handleClaimTrial = async () => {
    if (busyRef.current) return
    busyRef.current = true
    setClaiming(true)
    setTrialErr('')
    WebApp.HapticFeedback.impactOccurred('medium')
    try {
      await claimTrial()
      WebApp.HapticFeedback.notificationOccurred('success')
      // W2 #10 — guards для setState после await. Юзер мог уйти со страницы
      // пока летел claimTrial (он рилейтед ~2-3s).
      if (!mountedRef.current) return
      setTrialDone(true)
      setTrialSheet(true)
      // refresh subscription card — теперь юзер с активным trial
      getActiveSubscription().then(s => { if (mountedRef.current) setSub(s) })
        .catch(e => { console.error('home_trial_refresh', e) })
      setTrial({ eligible: false, duration_days: trial?.duration_days ?? 3 })
    } catch (e: unknown) {
      if (!mountedRef.current) return
      WebApp.HapticFeedback.notificationOccurred('error')
      const err = e as { message?: string }
      const msg = err.message || ''
      if (msg.includes('active_subscription'))     setTrialErr(t('trial_err_active'))
      else if (msg.includes('already_claimed'))    setTrialErr(t('trial_err_used'))
      else if (msg.includes('no_server'))          setTrialErr(t('trial_err_no_server'))
      else                                          setTrialErr(t('trial_err_generic'))
    } finally {
      busyRef.current = false
      if (mountedRef.current) setClaiming(false)
    }
  }

  const planLabel = (key: string) => {
    const map: Record<string, string> = {
      vpn_base:       t('vpn_plan_base'),
      vpn_base_3m:    t('vpn_plan_base_3m' as never),
      vpn_base_6m:    t('vpn_plan_base_6m' as never),
      vpn_base_12m:   t('vpn_plan_base_12m' as never),
      vpn_max:        t('vpn_plan_max'),
      vpn_max_3m:     t('vpn_plan_max_3m' as never),
      vpn_max_6m:     t('vpn_plan_max_6m' as never),
      vpn_max_12m:    t('vpn_plan_max_12m' as never),
      vpn_trial:      t('vpn_plan_trial'),
      vpn_start:      t('vpn_plan_start'),
      vpn_popular:    t('vpn_plan_popular'),
      vpn_pro:        t('vpn_plan_pro'),
      vpn_family:     t('vpn_plan_family'),
    }
    return map[key] ?? key
  }

  // EU-F6: RUB-paying users (CryptoBot/OxaPay/Lava) have stars_spent=0; include rub_spent in card visibility.
  // invited вернули в stats — у юзера без активной sub полезно видеть свои lifetime
  // метрики (сколько пригласил, сколько копится). Это «портфель», должно жить отдельно
  // от subscription state.
  const hasStats = stats && (stats.stars_spent > 0 || (stats.rub_spent ?? 0) > 0 || stats.bonus_days > 0 || stats.invited > 0)

  // W2 #15 — first-load skeleton page. Если все три фетча ещё не вернулись —
  // показываем полный skeleton-layout вместо «псевдо-пустой» страницы с
  // одиночной skeleton-карточкой VPN. На медленной сети это выглядит как
  // ровный loading-стейт, а не как сломанный Home (как было до W2 patch).
  // Триггер: sub=undefined (initial) И stats=null (initial) И trial=null.
  // После любого fetch'а — рендерим реальный layout с inline-плейсхолдерами.
  if (sub === undefined && stats === null && trial === null) {
    return <SkeletonPage />
  }

  const quickActions = [
    {
      color: '#27ae60',
      shadow: '0 4px 10px rgba(39,174,96,0.28)',
      label: t('home_configs'),
      action: () => nav('/configs'),
      icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
        <path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
        <rect x="9" y="3" width="6" height="4" rx="1" stroke="#fff" strokeWidth="2"/>
        <path d="M9 12h6M9 16h4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/>
      </svg>,
    },
    {
      color: '#8e44ad',
      shadow: '0 4px 10px rgba(142,68,173,0.28)',
      label: t('home_guide'),
      action: () => nav('/instructions'),
      icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
        <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
        <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
        <path d="M9 7h6M9 11h4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/>
      </svg>,
    },
    {
      color: '#e67e22',
      shadow: '0 4px 10px rgba(230,126,34,0.28)',
      label: t('home_support'),
      action: () => nav('/support'),
      icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"
          stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
      </svg>,
    },
  ]

  return (
    <>
      <div className="page gap-3">

        {/* Hero — показываем когда нет trial banner и нет trial-success */}
        {!trial?.eligible && !trialDone && sub === null && (
          <div className="fade-in pt-2 pb-1 px-1">
            <div className="text-[22px] font-extrabold text-[var(--tg-theme-text-color)] tracking-tight">
              {t('home_hero_title')}
            </div>
            <div className="text-[13px] text-[var(--tg-theme-hint-color)] mt-0.5 leading-snug whitespace-pre-line">
              {t('home_hero_sub')}
            </div>
          </div>
        )}

        {/* ── Trial CTA banner — shown only if eligible & no active sub ── */}
        {trial?.eligible && sub === null && !trialDone && (
          <div className="fade-in rounded-[20px] p-4 bg-gradient-to-br from-[#16a34a] to-[#0ea5e9] text-white shadow-[0_8px_24px_rgba(14,165,233,0.35)]">
            <div className="text-base font-bold mb-1">{t('trial_banner_title')}</div>
            <div className="text-[12px] opacity-90 mb-3 leading-snug">{t('trial_banner_sub')}</div>
            <button
              onClick={handleClaimTrial}
              disabled={claiming}
              className="w-full py-2.5 rounded-[12px] border-none bg-white/95 text-[#16a34a] text-sm font-bold cursor-pointer disabled:opacity-60"
            >
              {claiming ? t('trial_claiming') : t('trial_banner_btn')}
            </button>
            {trialErr && (
              <div className="mt-2 text-[11px] bg-white/15 rounded px-2 py-1">{trialErr}</div>
            )}
          </div>
        )}

        {trialDone && (
          <div className="fade-in rounded-[20px] p-4 bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)]">
            <div className="text-base font-bold mb-1 text-[var(--tg-theme-text-color)]">{t('trial_success_title')}</div>
            <div className="text-[12px] text-[var(--tg-theme-hint-color)] leading-snug mb-3">{t('trial_success_sub')}</div>
            <button
              onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); setTrialSheet(true) }}
              className="w-full py-[10px] rounded-[10px] border-none bg-primary text-white text-[13px] font-bold cursor-pointer mb-2"
            >
              📥 {t('trial_open_configs')}
            </button>
            <button
              onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); nav('/vpn/plans') }}
              className="w-full min-h-[44px] py-2.5 rounded-[10px] border-none bg-primary/[0.13] text-primary text-[12px] font-medium cursor-pointer"
            >
              {t('trial_success_upgrade')}
            </button>
          </div>
        )}

        {/* ── Service cards ── (VPN-only layout — full-width VPN-карточка) */}
        <div className={`grid ${SHOW_ESIM ? 'grid-cols-2' : 'grid-cols-1'} gap-2.5`}>

          {/* VPN card */}
          {sub === undefined ? (
            <div className={`skeleton ${SHOW_ESIM ? 'h-[178px]' : 'h-[160px]'} rounded-[20px]`} />
          ) : SHOW_ESIM ? (
            // Compact-mode (parity с eSIM-карточкой рядом)
            <div className="fade-in rounded-[20px] overflow-hidden bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] flex flex-col">
              <div className="h-[3px] bg-gradient-to-r from-primary to-[#5856d6] shrink-0" />
              <div className="px-[14px] pt-[14px] pb-4 flex flex-col flex-1 min-h-[158px]">
                <div className="w-[42px] h-[42px] rounded-[13px] bg-gradient-to-br from-primary to-[#5856d6] flex items-center justify-center mb-[11px] shrink-0 shadow-[0_4px_14px_rgba(36,129,204,0.4)]">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                    <path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z"
                      stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                    {sub && <path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>}
                  </svg>
                </div>

                <div className="text-[10px] font-bold uppercase tracking-[0.7px] mb-1.5 text-[var(--tg-theme-hint-color)]">VPN</div>

                {sub ? (
                  <>
                    <div className="flex items-center gap-[5px] mb-[3px]">
                      <span className={`w-[7px] h-[7px] rounded-full shrink-0 block ${
                        sub.status === 'expired' ? 'bg-danger'
                        : sub.status === 'grace' ? 'bg-amber-500'
                        : sub.plan === 'vpn_trial' ? 'bg-warning'
                        : 'bg-success'
                      }`} />
                      <span className={`text-xs font-bold ${
                        sub.status === 'expired' ? 'text-danger'
                        : sub.status === 'grace' ? 'text-amber-500'
                        : sub.plan === 'vpn_trial' ? 'text-warning'
                        : 'text-success'
                      }`}>
                        {sub.status === 'expired' ? t('home_expired_badge' as never)
                          : sub.status === 'grace' ? t('home_grace' as never)
                          : sub.plan === 'vpn_trial' ? t('home_trial_badge' as never)
                          : t('home_active')}
                      </span>
                    </div>
                    <div className="text-sm font-bold text-[var(--tg-theme-text-color)] mb-[2px]">{planLabel(sub.plan)}</div>
                    <div className="text-[11px] text-[var(--tg-theme-hint-color)]">
                      {sub.status === 'expired' ? (
                        <>{t('home_expired_ago_prefix' as never)} {p(Math.max(1, Math.abs(sub.days_remaining)), { ru: [t('home_days_left_1'), t('home_days_left_2'), t('days')], en: ['day', 'days'] })} {t('home_expired_ago_suffix' as never)}</>
                      ) : (
                        <>{p(sub.days_remaining, { ru: [t('home_days_left_1'), t('home_days_left_2'), t('days')], en: ['day', 'days'] })}
                          {sub.expires_at && <> · {t('home_until')} {formatNiceDate(sub.expires_at, lang)}</>}</>
                      )}
                    </div>
                    {sub.pending_plan && sub.pending_plan !== sub.plan && (
                      <div className="text-[10px] text-warning mt-0.5 font-medium">
                        ⏳ {t('home_pending_next' as never)} {planLabel(sub.pending_plan)}
                      </div>
                    )}
                    <div className="flex-1 min-h-[20px]" />
                    <button
                      onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); nav('/vpn') }}
                      className="press-fb w-full py-2 rounded-[10px] border-none bg-primary/[0.13] text-primary text-xs font-bold cursor-pointer"
                    >
                      {t('home_manage')} →
                    </button>
                  </>
                ) : (
                  <>
                    <div className="flex items-center gap-[5px] mb-[3px]">
                      <span className="w-[7px] h-[7px] rounded-full bg-gray-500/35 shrink-0 block" />
                      <span className="text-xs font-semibold text-[var(--tg-theme-hint-color)]">{t('home_no_sub')}</span>
                    </div>
                    <div className="text-[11px] text-[var(--tg-theme-hint-color)]">{t('home_sub_from')}</div>
                    <div className="flex-1 min-h-[20px]" />
                    <button
                      onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); nav('/vpn') }}
                      className="w-full py-2 rounded-[10px] border-none bg-gradient-to-br from-primary to-[#5856d6] text-white text-xs font-bold cursor-pointer"
                    >
                      {t('home_buy_vpn')}
                    </button>
                  </>
                )}
              </div>
            </div>
          ) : (
            // Hero-mode — full-width VPN-карточка когда eSIM скрыт.
            // Icon крупнее (56px), горизонтальный layout, акцент на статус.
            <div className="fade-in rounded-[24px] overflow-hidden bg-gradient-to-br from-primary/[0.08] to-[#5856d6]/[0.05] border border-primary/15">
              <div className="h-[4px] bg-gradient-to-r from-primary to-[#5856d6]" />
              <div className="px-5 py-[18px]">
                <div className="flex items-start gap-[14px]">
                  <div className="w-[56px] h-[56px] rounded-[16px] bg-gradient-to-br from-primary to-[#5856d6] flex items-center justify-center shrink-0 shadow-[0_6px_20px_rgba(36,129,204,0.45)]">
                    <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
                      <path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z"
                        stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                      {sub && <path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"/>}
                    </svg>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[10px] font-bold uppercase tracking-[0.7px] text-[var(--tg-theme-hint-color)]">VPN</div>
                    {sub ? (
                      <>
                        <div className="flex items-center gap-[6px] mt-1">
                          <span className={`w-[8px] h-[8px] rounded-full shrink-0 ${
                            sub.status === 'expired' ? 'bg-danger'
                            : sub.status === 'grace' ? 'bg-amber-500'
                            : sub.plan === 'vpn_trial' ? 'bg-warning'
                            : 'bg-success'
                          }`} />
                          <span className={`text-[13px] font-bold ${
                            sub.status === 'expired' ? 'text-danger'
                            : sub.status === 'grace' ? 'text-amber-500'
                            : sub.plan === 'vpn_trial' ? 'text-warning'
                            : 'text-success'
                          }`}>
                            {sub.status === 'expired' ? t('home_expired_badge' as never)
                              : sub.status === 'grace' ? t('home_grace' as never)
                              : sub.plan === 'vpn_trial' ? t('home_trial_badge' as never)
                              : t('home_active')}
                          </span>
                        </div>
                        <div className="text-[18px] font-extrabold text-[var(--tg-theme-text-color)] mt-1 leading-tight">
                          {planLabel(sub.plan)}
                        </div>
                        <div className="text-[12px] text-[var(--tg-theme-hint-color)] mt-0.5">
                          {sub.status === 'expired' ? (
                            <>{t('home_expired_ago_prefix' as never)} {p(Math.max(1, Math.abs(sub.days_remaining)), { ru: [t('home_days_left_1'), t('home_days_left_2'), t('days')], en: ['day', 'days'] })} {t('home_expired_ago_suffix' as never)}</>
                          ) : (
                            <>{p(sub.days_remaining, { ru: [t('home_days_left_1'), t('home_days_left_2'), t('days')], en: ['day', 'days'] })}
                              {sub.expires_at && <> · {t('home_until')} {formatNiceDate(sub.expires_at, lang)}</>}</>
                          )}
                        </div>
                        {sub.pending_plan && sub.pending_plan !== sub.plan && (
                          <div className="text-[11px] text-warning mt-1 font-medium">
                            ⏳ {t('home_pending_next' as never)} {planLabel(sub.pending_plan)}
                          </div>
                        )}
                      </>
                    ) : (
                      <>
                        <div className="flex items-center gap-[6px] mt-1">
                          <span className="w-[8px] h-[8px] rounded-full bg-gray-500/40 shrink-0" />
                          <span className="text-[13px] font-semibold text-[var(--tg-theme-hint-color)]">{t('home_no_sub')}</span>
                        </div>
                        <div className="text-[16px] font-bold text-[var(--tg-theme-text-color)] mt-1 leading-tight">
                          {t('home_card_pitch_no_sub' as never)}
                        </div>
                      </>
                    )}
                  </div>
                </div>
                <button
                  onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); nav('/vpn') }}
                  className={`press-fb w-full py-[11px] rounded-[12px] border-none text-[14px] font-bold cursor-pointer mt-[14px] ${
                    sub
                      ? 'bg-primary/[0.13] text-primary'
                      : 'bg-gradient-to-br from-primary to-[#5856d6] text-white shadow-[0_4px_14px_rgba(36,129,204,0.35)]'
                  }`}
                >
                  {sub ? `${t('home_manage')} →` : t('home_buy_vpn')}
                </button>
              </div>
            </div>
          )}

          {/* eSIM card — скрыта при SHOW_ESIM=false. Сетка grid-cols-2 выше
              остаётся, VPN-карточка просто растягивается на 1 ряд (Tailwind
              grid auto-fills). Если в будущем будут другие side-продукты —
              можно добавить заглушку «Скоро…». */}
          {SHOW_ESIM && (
          <div className="fade-in rounded-[20px] overflow-hidden bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] flex flex-col">
            <div className="h-[3px] bg-gradient-to-r from-success to-[#00b4d8] shrink-0" />
            <div className="px-[14px] pt-[14px] pb-4 flex flex-col flex-1 min-h-[158px]">
              <div className="w-[42px] h-[42px] rounded-[13px] bg-gradient-to-br from-success to-[#00b4d8] flex items-center justify-center mb-[11px] shadow-[0_4px_14px_rgba(39,174,96,0.4)]">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                  <rect x="5" y="2" width="14" height="20" rx="3" stroke="#fff" strokeWidth="2"/>
                  <path d="M9 8h6M9 12h6M9 16h4" stroke="#fff" strokeWidth="1.6" strokeLinecap="round"/>
                </svg>
              </div>

              <div className="text-[10px] font-bold uppercase tracking-[0.7px] mb-1.5 text-[var(--tg-theme-hint-color)]">
                eSIM
              </div>

              <div className="text-sm font-bold text-[var(--tg-theme-text-color)] mb-[2px]">
                {t('home_esim_title')}
              </div>
              <div className="text-[11px] text-[var(--tg-theme-hint-color)]">
                {t('home_esim_sub')}
              </div>

              <div className="flex-1 min-h-[20px]" />
              <button
                onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); nav('/esim') }}
                className="w-full py-2 rounded-[10px] border-none bg-gradient-to-br from-success to-[#00b4d8] text-white text-xs font-bold cursor-pointer"
              >
                {t('home_esim_browse')}
              </button>
            </div>
          </div>
          )}
        </div>

        {/* ── Trial → upgrade CTA — отдельная панель чтобы триал-юзер видел
            urgency и кнопку купить, пока триал ещё активен. ── */}
        {sub && sub.plan === 'vpn_trial' && (
          <div className="fade-in rounded-xl p-3 bg-[var(--tg-theme-section-bg-color,#f1f1f1)] border border-warning/30">
            <div className="text-xs opacity-70 mb-2 text-[var(--tg-theme-text-color)]">{t('home_trial_card_note' as never)}</div>
            <button
              className="w-full py-2.5 rounded-[10px] border-none bg-[var(--tg-theme-button-color,#2481cc)] text-[var(--tg-theme-button-text-color,#fff)] text-sm font-semibold cursor-pointer"
              onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); nav('/vpn/plans') }}
            >
              {t('home_trial_buy_cta' as never)}
            </button>
          </div>
        )}

        {/* ── Expired → renew CTA — sub существует но истекла. Параллель trial-CTA. ── */}
        {sub && sub.status === 'expired' && (
          <div className="fade-in rounded-xl p-3 bg-[var(--tg-theme-secondary-bg-color)] border border-danger/30">
            <div className="text-xs opacity-70 mb-2 text-[var(--tg-theme-text-color)]">{t('home_expired_card_note' as never)}</div>
            <button
              className="w-full py-2.5 rounded-[10px] border-none bg-[var(--tg-theme-button-color,#2481cc)] text-[var(--tg-theme-button-text-color,#fff)] text-sm font-semibold cursor-pointer"
              onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); nav('/vpn/plans') }}
            >
              {t('home_expired_buy_cta' as never)}
            </button>
          </div>
        )}

        {/* ── Quick actions ── */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
          {quickActions.map(({ color, shadow, label, action, icon }) => (
            <button
              key={label}
              onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); action() }}
              style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                gap: 8, padding: '14px 6px 12px',
                background: 'var(--tg-theme-section-bg-color)',
                border: '1px solid var(--card-border)',
                borderRadius: 16, cursor: 'pointer',
                minHeight: 86,
              }}
            >
              <div style={{
                width: 44, height: 44, borderRadius: 13, flexShrink: 0,
                background: color, boxShadow: shadow,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                {icon}
              </div>
              <span style={{
                fontSize: 11, fontWeight: 600,
                color: 'var(--tg-theme-text-color)',
                lineHeight: 1.2, textAlign: 'center',
              }}>{label}</span>
            </button>
          ))}
        </div>

        {/* ── Referral banner ── */}
        {/* Реф-баннер — generic CTA, всегда «Пригласи друга».  Прогресс
            (сколько пригласил / бонусы / ожидание оплаты) показываем только
            на странице /referral, чтобы Home оставалась чистой dashboard'ой
            без дублирования метрик. */}
        <button
          type="button"
          onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); nav('/referral') }}
          className="w-full text-left bg-[var(--tg-theme-section-bg-color)] rounded-2xl py-[14px] px-4 flex items-center gap-3.5 cursor-pointer border-[1.5px] border-warning/20"
        >
          <div className="w-11 h-11 rounded-[13px] shrink-0 bg-warning flex items-center justify-center shadow-[0_4px_12px_rgba(230,126,34,0.35)]">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
              <path d="M20 12v10H4V12" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M22 7H2v5h20V7z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M12 22V7" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
              <path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-bold text-[var(--tg-theme-text-color)] mb-[2px]">
              {t('home_invite')}
            </div>
            <div className="text-xs text-[var(--tg-theme-hint-color)]">
              {t('home_invite_sub')}
            </div>
          </div>
          <svg width="7" height="12" viewBox="0 0 7 12" fill="none">
            <path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </button>

        {/* ── Stats ── */}
        {hasStats && (
          <div className="fade-in grid grid-cols-3 gap-2">
            {[
              // EU-F6: prefer stars display if user has paid stars; otherwise show RUB.
              { value: stats!.stars_spent > 0
                  ? `${stats!.stars_spent} ⭐`
                  : `${stats!.rub_spent ?? 0} ₽`,
                label: t('home_stars_spent_label'),
                show: stats!.stars_spent > 0 || (stats!.rub_spent ?? 0) > 0 },
              // Используем plural — даёт правильное «+12 дней» / «+1 день» / «+2 дня».
              // Без пробела между числом и единицей выглядело как «+12дн.»
              { value: `+${p(stats!.bonus_days, { ru: [t('home_days_left_1'), t('home_days_left_2'), t('days')], en: ['day', 'days'] })}`,
                label: t('home_bonus_label'),  show: stats!.bonus_days > 0 },
              // Lifetime метрика — сколько привёл друзей. Видна даже на expired sub
              // (юзер без подписки тоже хочет помнить что у него уже есть N
              // приглашённых, копящих бонус — мотивирует продлить).
              { value: String(stats!.invited),       label: t('home_invited_label'),      show: stats!.invited > 0     },
            ].filter(x => x.show).map(({ value, label }) => (
              <div key={label} className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-[14px] px-2 py-3 text-center">
                <div className="text-base font-extrabold text-[var(--tg-theme-text-color)]">{value}</div>
                <div className="text-[10px] text-[var(--tg-theme-hint-color)] mt-[3px] leading-[1.3]">{label}</div>
              </div>
            ))}
          </div>
        )}

        {/* Накоплены реф-бонусные дни → CTA «Активировать»
            (redeem_referral_bonus добавляет дни к expires_at текущей sub).
            Если sub нет — кнопка disabled с alert'ом «сначала продли». */}
        {stats && stats.bonus_days > 0 && (
          <button
            type="button"
            disabled={redeeming}
            onClick={handleRedeemBonus}
            className="w-full fade-in flex items-center justify-between gap-3 rounded-2xl py-3 px-4 cursor-pointer disabled:opacity-50 border-[1.5px] border-success/30 bg-success/10"
          >
            <div className="flex items-center gap-3 min-w-0">
              <div className="w-10 h-10 rounded-[12px] shrink-0 bg-success flex items-center justify-center shadow-[0_4px_10px_rgba(39,174,96,0.3)]">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                  <path d="M12 8v8M8 12h8" stroke="#fff" strokeWidth="2.4" strokeLinecap="round"/>
                </svg>
              </div>
              <div className="text-left">
                <div className="text-sm font-bold text-[var(--tg-theme-text-color)]">
                  {(t('home_bonus_redeem_title' as never) as string)
                    .replace('{days}', p(stats.bonus_days, { ru: [t('home_days_left_1'), t('home_days_left_2'), t('days')], en: ['day', 'days'] }))}
                </div>
                <div className="text-xs text-[var(--tg-theme-hint-color)] mt-0.5">
                  {sub ? t('home_bonus_redeem_sub' as never) : t('home_bonus_redeem_no_sub_hint' as never)}
                </div>
              </div>
            </div>
            <span className="text-[13px] font-semibold text-success shrink-0">
              {redeeming ? t('home_bonus_redeem_loading' as never) : t('home_bonus_redeem_btn' as never)}
            </span>
          </button>
        )}

      </div>

      {trialSheet && (
        <TrialSuccessSheet
          onClose={() => setTrialSheet(false)}
        />
      )}
    </>
  )
}
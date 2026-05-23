import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import { getReferralStats, redeemReferralBonus, type ReferralStats } from '../api'
import { useT, useLang } from '../i18n'
import { copyText } from '../utils/clipboard'
import { InitDataGate } from '../components/InitDataGate'

export default function Referral() {
  const nav    = useNavigate()
  const t      = useT()
  const lang   = useLang().lang

  const STEPS = [
    { num: '1', color: '#2481cc', title: t('ref_how1_title'), sub: t('ref_how1_sub') },
    { num: '2', color: '#27ae60', title: t('ref_how2_title'), sub: t('ref_how2_sub') },
    { num: '3', color: '#e67e22', title: t('ref_how3_title'), sub: t('ref_how3_sub') },
  ]

  const [stats,    setStats]   = useState<ReferralStats | null>(null)
  const [loading,  setLoading] = useState(true)
  const [copied,   setCopied]  = useState(false)
  const [redeemLoading, setRedeemLoading] = useState(false)
  // Cooldown на share — без него юзер при двойном тапе открывает TG share-sheet
  // дважды (вторая инстанция перебивает первую), и TG-чат флудит дубликатами
  // приглашений если юзер быстро тапает 5 раз.
  const [shareLock, setShareLock] = useState(false)

  const mountedRef = useRef(true)
  useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false } }, [])

  const refreshStats = () => {
    return getReferralStats()
      .then(s => { if (mountedRef.current) setStats(s) })
      .catch(e => {
        // W2 bonus — silent .catch audit. До этого ошибку просто глотали и
        // показывали generic ref_error в UI. Теперь хотя бы логируем причину
        // (на проде в DevTools видно: timeout / 5xx / json-parse) — это
        // подскажет нам debug-trace когда юзер пишет «реферальная страница
        // не открывается».
        console.error('referral_stats_load', e)
        if (mountedRef.current) setStats(null)
      })
  }

  const handleRedeem = async () => {
    if (!stats || redeemLoading) return
    if (stats.bonus_days_pending <= 0) return  // защита; кнопка disabled
    setRedeemLoading(true)
    WebApp.HapticFeedback.impactOccurred('medium')
    try {
      const res = await redeemReferralBonus()
      // Backend сам решает какой action возможен (extended / reactivated /
      // no_eligible_sub). Не дублируем client-side проверку — UI отображает
      // то что вернул сервер.
      if (res.action === 'no_eligible_sub') {
        WebApp.HapticFeedback.notificationOccurred('warning')
        WebApp.showAlert(t('ref_redeem_no_eligible_sub' as never))
        await refreshStats()
        return
      }
      WebApp.HapticFeedback.notificationOccurred('success')
      const dateStr = res.new_expires_at
        ? new Date(res.new_expires_at).toLocaleDateString(
            lang === 'en' ? 'en-US' : 'ru-RU',
            { day: '2-digit', month: 'long', year: 'numeric' })
        : ''
      const tmplKey = res.action === 'reactivated'
        ? 'ref_redeem_done_reactivate'
        : 'ref_redeem_done'
      WebApp.showAlert(
        (t(tmplKey as never) as string)
          .replace('{days}', String(res.days_applied ?? 0))
          .replace('{date}', dateStr)
      )
      await refreshStats()
    } catch {
      WebApp.HapticFeedback.notificationOccurred('error')
      WebApp.showAlert(t('ref_redeem_err' as never))
    } finally {
      if (mountedRef.current) setRedeemLoading(false)
    }
  }

  useEffect(() => {
    WebApp.BackButton.show()
    const goBack = () => nav('/')
    WebApp.BackButton.onClick(goBack)
    refreshStats()
      .finally(() => { if (mountedRef.current) setLoading(false) })
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(goBack) }
  }, [nav])

  const handleCopy = () => {
    if (!stats) return
    WebApp.HapticFeedback.impactOccurred('light')
    copyText(stats.ref_link, () => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  const handleShare = () => {
    if (!stats || shareLock) return
    setShareLock(true)
    setTimeout(() => setShareLock(false), 3000)
    WebApp.HapticFeedback.impactOccurred('light')
    // Раньше тут был список конкретных заблокированных сервисов («Instagram,
    // YouTube, ChatGPT»). 149-ФЗ (запрет рекламы VPN в РФ с сент 2025)
    // прицельно бьёт по упоминаниям обхода блокировок конкретных ресурсов
    // — это легко цитируется при подаче на блок. Обобщённая формулировка
    // ниже не реклама обхода, а просто описание категории софта.
    const text = encodeURIComponent(lang === 'ru'
      ? `🛡 MAX VPN — быстрый VPN для телефона и компьютера`
      : `🛡 MAX VPN — fast VPN for mobile and desktop`
    )
    WebApp.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(stats.ref_link)}&text=${text}`)
  }

  return (
    <InitDataGate>
    <div className="page" style={{ gap: 12 }}>

      {/* Hero — без неё страница начиналась с «Как это работает» без контекста.
          Градиент → text-shadow для контраста (WCAG: white на #feca57 фейлит
          AA без тени, на ярком экране заголовок сливается с жёлтым). */}
      <div className="fade-in rounded-[20px] p-4 bg-gradient-to-br from-[#ff6b6b] to-[#f7a93a] text-white shadow-[0_8px_24px_rgba(255,107,107,0.25)]">
        <div className="text-base font-bold" style={{ textShadow: '0 1px 2px rgba(0,0,0,0.18)' }}>{t('ref_title')}</div>
        <div className="text-[12px] mt-0.5 opacity-95" style={{ textShadow: '0 1px 2px rgba(0,0,0,0.18)' }}>{t('ref_sub2')}</div>
      </div>

      {/* How it works */}
      <span className="section-title">{t('ref_how_title')}</span>
      <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
        {STEPS.map(({ num, color, title, sub }, i) => (
          <div key={i} className={`py-[13px] px-4 flex items-center gap-[14px] ${i < STEPS.length - 1 ? 'border-b border-solid border-[var(--card-border)]' : ''}`}>
            <div className="w-9 h-9 rounded-[10px] shrink-0 flex items-center justify-center font-extrabold text-base text-white" style={{ background: color }}>
              {num}
            </div>
            <div>
              <div className="text-[15px] font-semibold text-[var(--tg-theme-text-color)] leading-[1.3]">{title}</div>
              <div className="text-xs text-[var(--tg-theme-hint-color)] mt-0.5">{sub}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Loading / Error / Content
          Структура после 2026-05-23 (Олег): counter+bonuses block рендерятся
          ВСЕГДА (включая trial-юзеров и юзеров без paid sub). Только сама
          реф-ссылка спрятана за paid-only gate (`can_refer`) — trial-юзеры
          не могут приглашать (бэк отклоняет в /start ref_<id>).
          Бонусы видимы всем: trial может копить bank (по идее они не должны,
          но если у юзера ранее был paid sub и его рефералы конвертили — bank
          мог наполниться) и теперь сможет применить через reactivate flow. */}
      {loading ? (
        <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-[14px] py-3 px-[14px]">
          <div className="h-1 rounded bg-[rgba(128,128,128,0.12)] overflow-hidden">
            <div className="h-full rounded bg-gradient-to-r from-transparent via-[var(--tg-theme-button-color,#2481cc)] to-transparent animate-[progress-slide_1.4s_ease-in-out_infinite] w-1/2" />
          </div>
        </div>
      ) : stats ? (
        <>
          {/* 1) Реф-ссылка / paid-only gate — здесь и только здесь мы делим
              UX по can_refer. Trial-юзер видит «Купи подписку чтобы делиться». */}
          {!stats.can_refer ? (
            <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl py-5 px-4 text-center">
              <div className="text-3xl mb-2">🔒</div>
              <div className="text-[15px] font-semibold text-[var(--tg-theme-text-color)] mb-1">
                {t('ref_locked_title' as never)}
              </div>
              <div className="text-[12px] text-[var(--tg-theme-hint-color)] mb-4 leading-snug">
                {t('ref_locked_sub' as never)}
              </div>
              <button
                onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); nav('/vpn/plans') }}
                className="btn !w-full !py-3 !text-[14px]"
              >
                {t('ref_locked_btn' as never)}
              </button>
            </div>
          ) : (
            <>
              <span className="section-title">{t('ref_link_title')}</span>
              <div className="bg-[var(--tg-theme-section-bg-color)] rounded-[14px] py-3 px-[14px] flex items-center gap-[10px]">
                <span className="flex-1 text-[13px] text-[var(--tg-theme-hint-color)] overflow-hidden text-ellipsis whitespace-nowrap">
                  {stats.ref_link}
                </span>
                <button onClick={handleCopy} className={`py-[7px] px-[14px] rounded-[10px] border-none text-white text-xs font-semibold cursor-pointer shrink-0 transition-colors ${copied ? 'bg-success' : 'bg-[var(--tg-theme-button-color,#2481cc)]'}`}>
                  {copied ? t('ref_copied') : t('ref_copy')}
                </button>
              </div>

              <button
                onClick={handleShare}
                disabled={shareLock}
                className="w-full py-[13px] rounded-[14px] border-none text-white text-[15px] font-semibold cursor-pointer flex items-center justify-center gap-2.5 transition-opacity disabled:opacity-55 disabled:cursor-not-allowed"
                style={{ background: 'var(--tg-theme-button-color, #2481cc)', color: 'var(--tg-theme-button-text-color, #fff)' }}
              >
                <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M9.78 18.65l.28-4.23 7.68-6.92c.34-.31-.07-.46-.52-.19L7.74 13.3 3.64 12c-.88-.25-.89-.86.2-1.3l15.97-6.16c.73-.27 1.4.18 1.12 1.3l-2.72 12.81c-.19.91-.74 1.13-1.5.71L12.6 16.3l-1.99 1.93c-.23.23-.42.42-.83.42z"/>
                </svg>
                {t('ref_share')}
              </button>
            </>
          )}

          {/* 2) Мои бонусы — ВСЕГДА визибл (вне can_refer gate, 2026-05-23).
              4 состояния redeem-кнопки (по сравнению с прошлой версией добавлен
              4-й — reactivate). Backend сам решает какое действие применить
              (extend vs reactivate vs no_eligible_sub), поэтому client-side
              мы делаем кнопку active при bank>0 всегда — bei click backend
              скажет если применить некуда (no_eligible_sub → alert). */}
          {(() => {
            const bonus = stats.bonus_days_pending
            const hasBonus = bonus > 0
            // Client-side state:
            //   bank=0 → disabled, hint «Пригласи друга»
            //   bank>0 + has_active_sub → active extend, label «Активировать +N»
            //   bank>0 + !has_active_sub → active reactivate-attempt; label
            //     «Активировать +N (вернёт подписку)». Если бэк ответит
            //     no_eligible_sub — покажем alert «Купи подписку».
            const canRedeem = hasBonus
            // Tone: зелёный для positive bank, нейтральный для пустого
            const cardClass = hasBonus
              ? 'bg-gradient-to-br from-[#27ae60]/15 to-[#2ecc71]/8 border border-[#27ae60]/30'
              : 'bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)]'
            const valueClass = hasBonus
              ? 'text-success'
              : 'text-[var(--tg-theme-hint-color)]'
            const btnClass = canRedeem
              ? 'bg-success text-white'
              : 'bg-[var(--tg-theme-section-bg-color)] text-[var(--tg-theme-hint-color)] border border-[var(--card-border)]'
            const hint = !hasBonus
              ? t('ref_my_bonuses_hint_empty' as never)
              : stats.has_active_sub
                ? t('ref_my_bonuses_hint_active' as never)
                : (t('ref_my_bonuses_hint_reactivate' as never) as string).replace('{days}', String(bonus))
            const btnLabel = redeemLoading
              ? '…'
              : !hasBonus
                ? t('ref_redeem_btn_empty' as never)
                : stats.has_active_sub
                  ? (t('ref_redeem_btn' as never) as string).replace('{days}', String(bonus))
                  : (t('ref_redeem_btn_reactivate' as never) as string).replace('{days}', String(bonus))
            return (
              <>
                <span className="section-title">🎁 {t('ref_my_bonuses' as never)}</span>
                <div className={`${cardClass} rounded-2xl py-4 px-4`}>
                  <div className="flex items-baseline justify-between mb-2">
                    <span className="text-[13px] text-[var(--tg-theme-hint-color)]">
                      {t('ref_my_bonuses_label' as never)}
                    </span>
                    <span className={`text-[24px] font-extrabold leading-none ${valueClass}`}>
                      {hasBonus ? `+${bonus}` : '0'}
                    </span>
                  </div>
                  <div className="text-[11px] text-[var(--tg-theme-hint-color)] mb-3 leading-snug">
                    {hint}
                  </div>
                  <button
                    onClick={handleRedeem}
                    disabled={redeemLoading || !canRedeem}
                    className={`w-full py-2.5 rounded-[10px] text-[14px] font-semibold cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed ${btnClass}`}
                  >
                    {btnLabel}
                  </button>
                </div>
              </>
            )
          })()}

          {/* 3) Статистика — invited + converted. Тоже ВСЕГДА визибл
              (вне can_refer gate, 2026-05-23). Bonus_days НЕ показываем тут
              отдельно (есть отдельный блок «Мои бонусы» выше с redeem-кнопкой).
              Olej feedback 23.05: показываем ВСЕГДА (даже когда 0/0) — юзер
              должен видеть «приглашено: 0, купили: 0» как явный counter,
              чтобы мотивация двигалась. Раньше блок скрывался за can_refer —
              trial-юзеры вообще не видели свои числа. */}
          <span className="section-title">{t('ref_stats')}</span>
          <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
            {[
              {
                color: '#2481cc',
                icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><circle cx="9" cy="7" r="3.5" stroke="#fff" strokeWidth="2"/><path d="M2 20c0-3.314 3.134-6 7-6s7 2.686 7 6" stroke="#fff" strokeWidth="2" strokeLinecap="round"/><circle cx="17" cy="7.5" r="2.5" stroke="#fff" strokeWidth="1.8"/><path d="M22 20c0-2.761-2.239-5-5-5" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/></svg>,
                label: t('ref_invited'),
                value: stats.invited,
              },
              {
                color: '#27ae60',
                icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><circle cx="12" cy="12" r="10" stroke="#fff" strokeWidth="2"/></svg>,
                label: t('ref_bought'),
                value: stats.converted,
              },
            ].map(({ color, icon, label, value }, i, arr) => (
              <div key={label} className={`py-[13px] px-4 flex items-center gap-[14px] ${i < arr.length - 1 ? 'border-b border-solid border-[var(--card-border)]' : ''}`}>
                <div className="w-9 h-9 rounded-[10px] shrink-0 flex items-center justify-center" style={{ background: color }}>
                  {icon}
                </div>
                <span className="flex-1 text-[15px] font-medium text-[var(--tg-theme-text-color)]">{label}</span>
                <span className="text-lg font-bold text-[var(--tg-theme-text-color)]">{value}</span>
              </div>
            ))}
          </div>
        </>
      ) : (
        <p className="text-[var(--tg-theme-destructive-text-color,#ff3b30)] text-center break-words">
          {t('ref_error')}
        </p>
      )}

    </div>
    </InitDataGate>
  )
}

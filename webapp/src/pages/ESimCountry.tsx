import { useEffect, useState } from 'react'
import { useNavigate, useParams, useLocation } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import { getESimPackages, createESimInvoice, type ESimPackage } from '../api'
import { useT } from '../i18n'

function filterEssential(pkgs: ESimPackage[]): ESimPackage[] {
  if (pkgs.length <= 5) return pkgs

  const byDuration: Record<number, ESimPackage[]> = {}
  for (const p of pkgs) {
    const days = p.durationUnit.toLowerCase().startsWith('day') ? p.duration : p.duration * 30
    if (!byDuration[days]) byDuration[days] = []
    byDuration[days].push(p)
  }

  const preferred = [30, 15, 7, 14, 21]
  let bucket: ESimPackage[] = []
  for (const d of preferred) {
    if (byDuration[d]?.length >= 2) { bucket = byDuration[d]; break }
  }
  if (!bucket.length) {
    bucket = Object.values(byDuration).sort((a, b) => b.length - a.length)[0] ?? pkgs
  }

  bucket.sort((a, b) => a.stars - b.stars)
  if (bucket.length <= 5) return bucket

  const result: ESimPackage[] = []
  const step = (bucket.length - 1) / 4
  for (let i = 0; i < 5; i++) result.push(bucket[Math.round(i * step)])
  return result
}

function popularIndex(pkgs: ESimPackage[]): number {
  return Math.floor(pkgs.length / 2)
}

function priceToRub(price: number): number {
  return Math.round(price / 10_000 * 1.45 * 90)
}

function PaymentSheet({
  pkg, onClose, onPay, paying,
}: {
  pkg: ESimPackage
  onClose: () => void
  onPay: () => void
  paying: boolean
}) {
  const t = useT()
  const isDaily = pkg.dataType === 2
  const durationStr = isDaily
    ? `${pkg.dataLabel}${t('esim_pkg_day')}`
    : `${pkg.dataLabel} · ${pkg.duration} ${pkg.durationUnit.toLowerCase().startsWith('day') ? t('esim_pkg_days') : t('esim_pkg_mos')}`

  return (
    <>
      <div onClick={onClose} className="fixed inset-0 z-[100] bg-black/45" />

      <div
        className="fixed left-0 right-0 bottom-0 z-[101] bg-[var(--tg-theme-bg-color,#fff)] rounded-t-[20px] px-5 pt-5 shadow-[0_-4px_30px_rgba(0,0,0,0.18)]"
        style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 24px)' }}
      >
        <div className="w-9 h-1 rounded-sm bg-[rgba(128,128,128,0.3)] mx-auto -mt-2 mb-[18px]" />

        <div className="mb-[18px]">
          <div className="font-bold text-lg text-[var(--tg-theme-text-color)]">
            {pkg.dataLabel}
          </div>
          <div className="text-[13px] text-[var(--tg-theme-hint-color)] mt-[3px]">
            {durationStr} · {pkg.speed}
          </div>
        </div>

        <div className="text-xs font-semibold text-[var(--tg-theme-hint-color)] uppercase tracking-wide mb-2">
          {t('esim_payment_method')}
        </div>

        <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-[14px] overflow-hidden mb-5">
          <div className="py-[13px] px-4 flex items-center gap-[14px] bg-primary/5">
            <span className="text-[22px] w-8 text-center shrink-0">⭐</span>
            <div className="flex-1">
              <div className="text-[15px] text-[var(--tg-theme-text-color)] font-medium">Telegram Stars</div>
              <div className="text-xs text-[var(--tg-theme-hint-color)] mt-px">≈ {priceToRub(pkg.price)} {t('esim_rubles')}</div>
            </div>
            <span className="text-[13px] text-[var(--tg-theme-button-color,#2481cc)] font-semibold">{pkg.stars} ⭐</span>
            <div className="w-5 h-5 rounded-full shrink-0 border-2 border-[var(--tg-theme-button-color,#2481cc)] bg-[var(--tg-theme-button-color,#2481cc)] flex items-center justify-center">
              <div className="w-2 h-2 rounded-full bg-white" />
            </div>
          </div>
        </div>

        <button
          className="btn w-full text-base py-[14px]"
          disabled={paying}
          onClick={onPay}
        >
          {paying ? '…' : `${t('esim_pay_btn')} ${pkg.stars} ⭐ · ≈${priceToRub(pkg.price)} ${t('esim_rubles')}`}
        </button>
      </div>
    </>
  )
}

export default function ESimCountry() {
  const { code }     = useParams<{ code: string }>()
  const { state }    = useLocation() as { state: { name?: string; ruCompatible?: boolean } | null }
  const countryName  = state?.name ?? code ?? ''
  const ruCompatible = state?.ruCompatible ?? false
  const nav          = useNavigate()
  const t            = useT()

  const [packages,  setPackages]  = useState<ESimPackage[]>([])
  const [loading,   setLoading]   = useState(true)
  const [sheetPkg,  setSheetPkg]  = useState<ESimPackage | null>(null)
  const [paying,    setPaying]    = useState(false)
  const [paid,      setPaid]      = useState(false)
  const [errMsg,    setErrMsg]    = useState('')

  useEffect(() => {
    WebApp.BackButton.show()
    WebApp.BackButton.onClick(() => nav('/esim'))
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(() => nav('/esim')) }
  }, [nav])

  useEffect(() => {
    if (!code) return
    getESimPackages(code)
      .then(all => setPackages(filterEssential(all)))
      .catch(() => setErrMsg(t('esim_no_pkgs')))
      .finally(() => setLoading(false))
  }, [code])

  const handlePay = async () => {
    if (!sheetPkg || paying) return
    setPaying(true)
    setErrMsg('')
    try {
      const { invoice_url } = await createESimInvoice(sheetPkg)
      WebApp.openInvoice(invoice_url, status => {
        setPaying(false)
        setSheetPkg(null)
        if (status === 'paid') { WebApp.HapticFeedback.notificationOccurred('success'); setPaid(true) }
        else if (status !== 'cancelled') setErrMsg(t('payment_failed'))
      })
    } catch (e) {
      setPaying(false)
      setErrMsg(e instanceof Error ? e.message : t('server_error'))
    }
  }

  if (paid) {
    return (
      <div className="page">
        <div className="center">
          <div className="w-[72px] h-[72px] rounded-[22px] mb-1 bg-[rgba(39,174,96,0.12)] flex items-center justify-center text-4xl">✅</div>
          <div className="text-[22px] font-extrabold text-[var(--tg-theme-text-color)]">{t('esim_paid_success')}</div>
          <p className="text-[var(--tg-theme-hint-color)] text-sm leading-relaxed">
            {t('esim_paid_qr_note')}
          </p>
          <button className="btn w-full" onClick={() => setPaid(false)}>{t('esim_buy_more')}</button>
        </div>
      </div>
    )
  }

  const popIdx = popularIndex(packages)

  return (
    <>
      <div className="page" style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 90px)' }}>

        <div className="px-1 pt-1.5 pb-0.5">
          <div className="text-2xl font-extrabold text-[var(--tg-theme-text-color)] mb-1">
            {countryName}
          </div>
          <div className="text-[13px] text-[var(--tg-theme-hint-color)]">
            {ruCompatible ? t('esim_country_works_in_ru') : t('esim_country_travel')}
          </div>
        </div>

        {ruCompatible && (
          <div className="bg-[rgba(39,174,96,0.1)] rounded-xl py-[10px] px-[14px] text-[13px] text-success leading-relaxed">
            {t('esim_ru_compat_note')}
          </div>
        )}

        {loading && (
          <div className="flex flex-col gap-[10px]">
            {[1,2,3].map(i => <div key={i} className="skeleton h-20 rounded-[14px]" />)}
          </div>
        )}

        {packages.length > 0 && (
          <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
            {packages.map((pkg, i) => {
              const isPopular = i === popIdx
              const isDaily = pkg.dataType === 2
              const durationStr = isDaily
                ? `${pkg.dataLabel}${t('esim_pkg_day')}`
                : `${pkg.dataLabel} · ${pkg.duration} ${pkg.durationUnit.toLowerCase().startsWith('day') ? t('esim_pkg_days') : t('esim_pkg_mos')}`
              return (
                <div key={pkg.packageCode} className={`py-[13px] px-4 flex items-center gap-[14px] ${isPopular ? 'bg-primary/5' : ''} ${i < packages.length - 1 ? 'border-b border-solid border-[var(--card-border)]' : ''}`}>
                  <div className={`w-10 h-10 rounded-[11px] shrink-0 flex items-center justify-center ${isPopular ? 'bg-[var(--tg-theme-button-color,#2481cc)]' : 'bg-[rgba(128,128,128,0.12)]'}`}>
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                      <path d="M21 15.5a9 9 0 1 0-18 0" stroke={isPopular ? '#fff' : 'var(--tg-theme-hint-color,#888)'} strokeWidth="2" strokeLinecap="round"/>
                      <path d="M12 6v6l4 2" stroke={isPopular ? '#fff' : 'var(--tg-theme-hint-color,#888)'} strokeWidth="2" strokeLinecap="round"/>
                    </svg>
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-[7px] mb-[2px]">
                      <span className="font-bold text-[15px] text-[var(--tg-theme-text-color)]">{isDaily ? `${pkg.dataLabel}${t('esim_pkg_day')}` : pkg.dataLabel}</span>
                      {isPopular && (
                        <span className="bg-[var(--tg-theme-button-color,#2481cc)] text-[var(--tg-theme-button-text-color,#fff)] text-[10px] font-bold py-[2px] px-[7px] rounded-[20px]">{t('esim_pkg_hit')}</span>
                      )}
                    </div>
                    <div className="text-xs text-[var(--tg-theme-hint-color)]">{isDaily ? `${t('esim_pkg_per_day')} · ${pkg.speed}` : `${durationStr} · ${pkg.speed}`}</div>
                  </div>

                  <button
                    className="btn min-w-[84px] text-[13px] shrink-0"
                    onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); setSheetPkg(pkg) }}
                  >
                    {priceToRub(pkg.price)} {t('esim_rubles')}
                  </button>
                </div>
              )
            })}
          </div>
        )}

        {!loading && packages.length === 0 && !errMsg && (
          <div className="text-center py-8">
            <div className="text-[40px] mb-3">😔</div>
            <p className="text-[var(--tg-theme-hint-color)]">{t('esim_no_pkgs')}</p>
          </div>
        )}

        {errMsg && (
          <p className="text-[var(--tg-theme-destructive-text-color,#ff3b30)] text-center text-sm">
            {errMsg}
          </p>
        )}

        {!loading && packages.length > 0 && (
          <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-xl py-3 px-4 text-[13px] text-[var(--tg-theme-hint-color)] leading-relaxed">
            {t('esim_pkg_install')}
          </div>
        )}
      </div>

      {sheetPkg && (
        <PaymentSheet
          pkg={sheetPkg}
          onClose={() => !paying && setSheetPkg(null)}
          onPay={handlePay}
          paying={paying}
        />
      )}
    </>
  )
}
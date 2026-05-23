'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import { toast } from '@/app/_components/ui'

// Confirm-required action button.  Single-source-of-truth для всех write-ops:
// первый клик → подтверждение, второй → fetch.  Не используем confirm() —
// он блокирующий и убогий в Telegram WebView (страница админки иногда
// открывается на мобиле).

type State = 'idle' | 'confirm' | 'pending' | 'error'

export function ConfirmButton({
  onConfirm,
  label,
  confirmLabel,
  className,
}: {
  onConfirm: () => Promise<void>
  label: string
  confirmLabel?: string
  className?: string
}) {
  const [state, setState] = useState<State>('idle')
  const [err, setErr] = useState<string | null>(null)
  const [, startTransition] = useTransition()
  const router = useRouter()

  const handle = async () => {
    if (state === 'idle') { setState('confirm'); return }
    if (state === 'confirm') {
      setState('pending')
      setErr(null)
      try {
        await onConfirm()
        startTransition(() => { router.refresh() })
        setState('idle')
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e))
        setState('error')
      }
    }
  }

  if (state === 'error') {
    return (
      <div className="flex items-center gap-2">
        <span className="text-xs text-rose-400">{err || 'ошибка'}</span>
        <button onClick={() => { setErr(null); setState('idle') }}
                className="text-xs text-neutral-500 hover:text-white">×</button>
      </div>
    )
  }

  return (
    <button
      onClick={handle}
      disabled={state === 'pending'}
      className={
        className ??
        (state === 'confirm'
          ? 'px-3 py-1 rounded-md text-xs bg-rose-500 text-white hover:bg-rose-400 disabled:opacity-50'
          : 'px-3 py-1 rounded-md text-xs bg-neutral-800 text-neutral-200 hover:bg-neutral-700 disabled:opacity-50')
      }
    >
      {state === 'pending' ? '...' : state === 'confirm' ? (confirmLabel ?? `Точно? ${label}`) : label}
    </button>
  )
}

// ── Convenience wrappers used on /clients/[id] ───────────────────────────────

// basePath '/admin' (next.config.ts) НЕ автодобавляется к client-side fetch URL,
// поэтому все API-вызовы должны включать /admin/api/... вручную.
const API = '/admin/api'

export function ExtendSubButton({ subId, days }: { subId: number; days: number }) {
  const onConfirm = async () => {
    const r = await fetch(`${API}/sub/${subId}/extend`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ days, reason: `manual gift ${days}d` }),
    })
    if (!r.ok) {
      const data = await r.json().catch(() => ({}))
      throw new Error(data.error || `HTTP ${r.status}`)
    }
  }
  return <ConfirmButton onConfirm={onConfirm} label={`+${days} дн`} confirmLabel={`Точно +${days} дн?`} />
}

/**
 * RefundSubButton — двухэтапный UX (B3, track B):
 *
 *  step=idle   → клик: показать confirm-modal с пояснением что произойдёт
 *  step=confirm → клик «Да, вернуть» → POST /api/sub/{id}/refund
 *      • 2xx → toast.success + warnings (если есть) + закрыть
 *      • 502 → показать fallback-modal «Telegram отказал → пометить локально?»
 *      • др. → toast.error
 *  step=fallback → клик «Да, пометить локально» → POST /api/sub/{id}/mark-refunded
 *      • 2xx → toast.warning «помечено локально, деньги НЕ возвращены»
 *      • др. → toast.error
 *
 * Раньше был один `ConfirmButton` без визуального разделения этих веток —
 * админ при 502 получал короткий error-pill и не понимал что делать.
 * Audit-finding 22.05.
 */
type RefundDialog = 'closed' | 'confirm' | 'fallback' | 'pending'

type RefundPayload = {
  lava_manual_refund_required?: boolean
  lava_recurring_charges?: Array<{ tx_id: string; amount?: number }>
  lava_cancel_attempted?: boolean
  stars_multiple_charges?: boolean
  stars_refunded_tx?: string | null
  stars_original_tx?: string | null
  configs_revoked?: number
  configs_revoke_failed?: number
  error?: string
  message?: string
}

function refundWarnings(d: RefundPayload): string[] {
  const warnings: string[] = []
  if (d.lava_manual_refund_required) {
    warnings.push(
      'Lava-возврат денег НЕ автоматизирован. Auto-renew отключён (cancel API '
      + (d.lava_cancel_attempted ? 'вызван' : 'не вызван')
      + '), но деньги нужно вернуть вручную через Lava-кабинет.',
    )
    if (d.lava_recurring_charges && d.lava_recurring_charges.length > 0) {
      const txs = d.lava_recurring_charges.map(c => c.tx_id).join(', ')
      warnings.push(`tx_id для ручного возврата: ${txs}`)
    }
  }
  if (d.stars_multiple_charges) {
    warnings.push(
      `Несколько Stars-платежей (upgrade). Автоматом возвращён последний: ${d.stars_refunded_tx ?? '—'}, `
      + `оригинал: ${d.stars_original_tx ?? '—'} — проверь historic charges.`,
    )
  }
  const failed = d.configs_revoke_failed ?? 0
  const revoked = d.configs_revoked ?? 0
  if (failed > 0) {
    warnings.push(
      `${failed} из ${revoked + failed} конфигов не отозвались на агенте — `
      + 'остальное подчистит фон-sync в течение часа.',
    )
  }
  return warnings
}

export function RefundSubButton({ subId, isStars }: { subId: number; isStars: boolean }) {
  const [dialog, setDialog] = useState<RefundDialog>('closed')
  const [, startTransition] = useTransition()
  const router = useRouter()

  const callPrimaryRefund = async () => {
    setDialog('pending')
    let r: Response
    try {
      r = await fetch(`${API}/sub/${subId}/refund`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'admin manual refund', stars_refund: isStars }),
      })
    } catch (e) {
      toast.error('Сеть упала', { description: e instanceof Error ? e.message : String(e) })
      setDialog('closed')
      return
    }
    const data: RefundPayload = await r.json().catch(() => ({} as RefundPayload))

    if (r.ok) {
      const warns = refundWarnings(data)
      toast.success('Refund выполнен', {
        description: warns.length > 0 ? warns.join('\n') : undefined,
      })
      startTransition(() => { router.refresh() })
      setDialog('closed')
      return
    }

    // 502 = провайдер (Telegram/CryptoBot/Lava) отказал. Предлагаем local-mark.
    // Other = непредвиденное (400/401/500) — просто error без fallback'а,
    // чтобы не учить админа лечить любую ошибку игнорированием.
    if (r.status === 502) {
      toast.warning('Внешний refund не удался', {
        description: data.error ?? data.message ?? 'Telegram/CryptoBot отказал',
      })
      setDialog('fallback')
      return
    }

    toast.error('Не удалось', {
      description: data.error ?? data.message ?? `HTTP ${r.status}`,
    })
    setDialog('closed')
  }

  const callLocalMark = async () => {
    setDialog('pending')
    let r: Response
    try {
      r = await fetch(`${API}/sub/${subId}/mark-refunded`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'admin local mark after external refund failed' }),
      })
    } catch (e) {
      toast.error('Сеть упала', { description: e instanceof Error ? e.message : String(e) })
      setDialog('closed')
      return
    }
    const data = await r.json().catch(() => ({})) as RefundPayload
    if (r.ok) {
      const failed = data.configs_revoke_failed ?? 0
      toast.warning('Помечено локально', {
        description: 'Деньги юзеру НЕ возвращены. Подписка отключена, конфиги отозваны.'
          + (failed > 0 ? ` ${failed} конфигов не отозвались на агенте — подчистит фон-sync.` : ''),
      })
      startTransition(() => { router.refresh() })
      setDialog('closed')
      return
    }
    toast.error('Mark-refund не удался', {
      description: data.error ?? data.message ?? `HTTP ${r.status}`,
    })
    setDialog('closed')
  }

  return (
    <>
      <button
        onClick={() => setDialog('confirm')}
        disabled={dialog === 'pending'}
        className="px-3 py-1 rounded-md text-xs bg-neutral-800 text-neutral-200 hover:bg-neutral-700 disabled:opacity-50"
      >
        {isStars ? 'Refund ⭐' : 'Refund'}
      </button>

      {dialog === 'confirm' && (
        <RefundModal
          title={isStars ? 'Вернуть Stars?' : 'Сделать refund?'}
          body={
            <>
              <p className="mb-2">
                Сначала попробуем вернуть деньги через {isStars ? 'Telegram (Stars refund API)' : 'провайдера (CryptoBot/Lava/OxaPay)'}.
              </p>
              <p className="text-neutral-400 text-xs">
                Если провайдер откажет — появится опция пометить sub как refunded
                локально (sub→refunded + revoke configs, деньги юзеру НЕ вернутся).
              </p>
            </>
          }
          confirmLabel="Да, вернуть"
          confirmClass="bg-rose-500 hover:bg-rose-400"
          onConfirm={callPrimaryRefund}
          onCancel={() => setDialog('closed')}
        />
      )}

      {dialog === 'fallback' && (
        <RefundModal
          title="Пометить локально?"
          body={
            <>
              <p className="mb-2">
                Не получилось через {isStars ? 'Telegram' : 'провайдера'}
                {' '}(вероятно charge старше 21 дня).
              </p>
              <p className="text-neutral-400 text-xs">
                Пометить sub как refunded локально? <b>Юзер не получит денег</b>, но подписка отключится и конфиги отзовутся.
              </p>
            </>
          }
          confirmLabel="Да, пометить локально"
          confirmClass="bg-amber-500 hover:bg-amber-400 text-neutral-950"
          onConfirm={callLocalMark}
          onCancel={() => setDialog('closed')}
        />
      )}

      {dialog === 'pending' && (
        <RefundModal
          title="Подождите…"
          body={<p className="text-neutral-400 text-xs">Выполняется запрос к провайдеру (до 15с).</p>}
        />
      )}
    </>
  )
}

/**
 * Простой inline-modal без отдельной библиотеки. На foundation D добавили
 * только sonner для toast'ов; для confirm-диалогов трек B решает локально.
 * Если кнопок нет (`onConfirm`/`onCancel` отсутствуют) — modal в loading-state.
 */
function RefundModal({
  title, body, confirmLabel, confirmClass, onConfirm, onCancel,
}: {
  title: string
  body: React.ReactNode
  confirmLabel?: string
  confirmClass?: string
  onConfirm?: () => void
  onCancel?: () => void
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="max-w-md w-full bg-neutral-900 border border-neutral-800 rounded-2xl p-6 shadow-2xl">
        <div className="text-base font-semibold mb-3">{title}</div>
        <div className="text-sm text-neutral-200 mb-5">{body}</div>
        {(onConfirm || onCancel) && (
          <div className="flex justify-end gap-2">
            {onCancel && (
              <button
                onClick={onCancel}
                className="px-4 py-1.5 rounded-md text-xs bg-neutral-800 text-neutral-300 hover:bg-neutral-700"
              >
                Отмена
              </button>
            )}
            {onConfirm && (
              <button
                onClick={onConfirm}
                className={`px-4 py-1.5 rounded-md text-xs text-white ${confirmClass ?? 'bg-sky-500 hover:bg-sky-400'}`}
              >
                {confirmLabel ?? 'Да'}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export function BanUserButton({ userId, banned }: { userId: number; banned: boolean }) {
  const onConfirm = async () => {
    const path = banned ? 'unban' : 'ban'
    const r = await fetch(`${API}/user/${userId}/${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: banned ? '{}' : JSON.stringify({ reason: 'admin manual ban' }),
    })
    const data = await r.json().catch(() => ({})) as {
      error?: string
      message?: string
      stars_manual_required?: boolean
      lava_cancel_attempted?: boolean
      payment_provider?: string
    }
    if (!r.ok) {
      throw new Error(data.message ?? data.error ?? `HTTP ${r.status}`)
    }

    // Surface backend warnings (mirrors RefundSubButton pattern). Backend
    // returns rich payload for ban/unban: stars-recurring subs cannot be
    // cancelled via API, Lava cancel may be skipped if parent_contract_id
    // is missing.
    const warnings: string[] = []
    if (data.stars_manual_required) {
      warnings.push('⚠️ Stars-recurring sub: вручную отмени в Stars dashboard (Telegram API не даёт).')
    }
    if (data.lava_cancel_attempted === false && data.payment_provider === 'lavatop') {
      warnings.push('⚠️ Lava cancel НЕ вызывался (нет parent_contract_id / API key).')
    }
    if (warnings.length > 0) {
      alert(warnings.join('\n\n'))
    }
  }
  return (
    <ConfirmButton
      onConfirm={onConfirm}
      label={banned ? 'Разбанить' : 'Забанить'}
      confirmLabel={banned ? 'Точно разбанить?' : 'Точно забанить?'}
      className={banned
        ? 'px-3 py-1 rounded-md text-xs bg-emerald-700 text-white hover:bg-emerald-600'
        : 'px-3 py-1 rounded-md text-xs bg-neutral-800 text-neutral-200 hover:bg-neutral-700'}
    />
  )
}

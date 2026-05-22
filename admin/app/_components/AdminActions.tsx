'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'

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

export function RefundSubButton({ subId, isStars }: { subId: number; isStars: boolean }) {
  const onConfirm = async () => {
    const r = await fetch(`${API}/sub/${subId}/refund`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: 'admin manual refund', stars_refund: isStars }),
    })
    const data = await r.json().catch(() => ({}))
    if (!r.ok) {
      throw new Error((data as { error?: string; message?: string }).error
        ?? (data as { message?: string }).message
        ?? `HTTP ${r.status}`)
    }

    // Backend возвращает rich payload (lava manual refund, multi-charge stars,
    // configs revoke failures). Без UI-эха админ не узнает что половина работы
    // не сделана автоматически. alert() — простой fallback (Telegram WebView
    // его поддерживает на десктопе и мобиле).
    const d = data as {
      lava_manual_refund_required?: boolean
      lava_recurring_charges?: Array<{ tx_id: string; amount?: number }>
      lava_cancel_attempted?: boolean
      stars_multiple_charges?: boolean
      stars_refunded_tx?: string | null
      stars_original_tx?: string | null
      configs_revoked?: number
      configs_revoke_failed?: number
    }
    const warnings: string[] = []
    if (d.lava_manual_refund_required) {
      warnings.push(
        '⚠️ Lava-возврат денег НЕ автоматизирован. ' +
        'Auto-renew отключён (cancel API ' +
        (d.lava_cancel_attempted ? 'вызван' : 'не вызван') +
        '), но деньги нужно вернуть вручную через Lava-кабинет.',
      )
      if (d.lava_recurring_charges && d.lava_recurring_charges.length > 0) {
        const txs = d.lava_recurring_charges.map(c => c.tx_id).join(', ')
        warnings.push(`Транзакции для ручного возврата: ${txs}`)
      }
    }
    if (d.stars_multiple_charges) {
      warnings.push(
        '⚠️ У подписки несколько Stars-платежей (upgrade-сценарий). ' +
        `Автоматом возвращён последний: ${d.stars_refunded_tx ?? '—'}. ` +
        `Оригинальный charge: ${d.stars_original_tx ?? '—'} — ` +
        'проверь historic charges на странице платежей.',
      )
    }
    const failed = d.configs_revoke_failed ?? 0
    const revoked = d.configs_revoked ?? 0
    if (failed > 0) {
      warnings.push(
        `⚠️ ${failed} из ${revoked + failed} конфигов не отозвались на агенте — ` +
        'остальное подчистит фон-sync в течение часа.',
      )
    }
    if (warnings.length > 0) {
      alert(warnings.join('\n\n'))
    }
  }
  return (
    <ConfirmButton
      onConfirm={onConfirm}
      label={isStars ? 'Refund ⭐' : 'Refund'}
      confirmLabel={isStars ? 'Точно вернуть Stars?' : 'Точно refund?'}
    />
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
    if (!r.ok) {
      const data = await r.json().catch(() => ({}))
      throw new Error(data.error || `HTTP ${r.status}`)
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

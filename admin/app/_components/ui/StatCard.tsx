import type { ReactNode } from 'react'

/**
 * StatCard — единая KPI-карточка для дашбордов админки.
 *
 * До этого было 6 копий в page.tsx / analytics / clients / clients/[id] /
 * monitoring / payments. У каждой свой набор тонов (warn=yellow в page и
 * monitoring, плюс «default» neutral везде). Унифицируем в один компонент
 * с явным `tone`:
 *
 *  - `default`  → белый value, neutral-800 border (базовая карточка)
 *  - `positive` → emerald (зелёный)
 *  - `negative` → rose (красный)
 *  - `warning`  → amber/yellow + цветной left-border (legacy `warn` map'ится сюда)
 *
 * Сохраняем визуал «как был»: bg-neutral-900, rounded-2xl, p-5,
 * uppercase tracking-wider label сверху + крупное value + опциональный hint.
 *
 * `value` и `hint` принимаем как ReactNode чтобы не терять inline-разметку
 * (например `⭐ {n}` или `<span className="text-yellow-400">`).
 */
export type StatCardTone = 'default' | 'positive' | 'negative' | 'warning'

export type StatCardProps = {
  label: string
  value: ReactNode
  hint?: ReactNode
  tone?: StatCardTone
}

const TONE_VALUE: Record<StatCardTone, string> = {
  default:  'text-white',
  positive: 'text-emerald-400',
  negative: 'text-rose-400',
  warning:  'text-amber-400',
}

const TONE_BORDER: Record<StatCardTone, string> = {
  default:  'border-neutral-800',
  // accents — цветной left-border + полупрозрачная рамка, как было у `warn` в page.tsx/monitoring.tsx
  positive: 'border-emerald-500/30 border-l-2 border-l-emerald-500 pl-4',
  negative: 'border-rose-500/30 border-l-2 border-l-rose-500 pl-4',
  warning:  'border-amber-500/40 border-l-2 border-l-amber-500 pl-4',
}

export function StatCard({ label, value, hint, tone = 'default' }: StatCardProps) {
  return (
    <div className={`bg-neutral-900 border rounded-2xl p-5 ${TONE_BORDER[tone]}`}>
      <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-1">{label}</div>
      <div className={`text-2xl font-bold ${TONE_VALUE[tone]}`}>{value}</div>
      {hint && <div className="text-xs text-neutral-500 mt-1">{hint}</div>}
    </div>
  )
}

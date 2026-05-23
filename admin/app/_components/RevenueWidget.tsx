import { StatCard } from './ui'
import type { StatCardTone } from './ui/StatCard'

/**
 * RevenueWidget — блок Today/WTD/MTD на главной dashboard'e.
 *
 * StatCard'ы в RUB-эквиваленте + delta vs предыдущего окна (тот же день
 * прошлой недели / тот же week-to-date прошлой недели / тот же month-to-date
 * прошлого месяца).
 *
 * Tone правило:
 *   delta > +5%   → positive (зелёный)
 *   delta < -5%   → negative (красный)
 *   |delta| ≤ 5%  → default  (нейтрально, шум)
 *
 * Если предыдущее окно = 0, дельта не считается (показываем «—» вместо ∞%).
 */

type Window = { rub: number; stars?: number }

function fmtRub(n: number): string {
  return `${n.toLocaleString('ru')} ₽`
}

function delta(curr: number, prev: number): { pct: number | null; tone: StatCardTone; arrow: string } {
  if (prev === 0) {
    // Нет базы — не можем посчитать процент. Возвращаем "—" в UI.
    return { pct: null, tone: 'default', arrow: '·' }
  }
  const pct = Math.round(((curr - prev) / prev) * 1000) / 10
  if (pct > 5)  return { pct, tone: 'positive', arrow: '▲' }
  if (pct < -5) return { pct, tone: 'negative', arrow: '▼' }
  return { pct, tone: 'default', arrow: '·' }
}

function Hint({ pct, arrow, prev, label }: { pct: number | null; arrow: string; prev: number; label: string }) {
  if (pct === null) {
    return (
      <span className="text-neutral-500">
        {label}: {fmtRub(prev)}
      </span>
    )
  }
  const sign = pct > 0 ? '+' : ''
  return (
    <span>
      <span>{arrow} {sign}{pct}% </span>
      <span className="text-neutral-500">vs {label.toLowerCase()} ({fmtRub(prev)})</span>
    </span>
  )
}

export type RevenueWidgetProps = {
  today:     Window
  todayPrev: number
  wtd:       Window
  wtdPrev:   number
  mtd:       Window
  mtdPrev:   number
}

export function RevenueWidget(props: RevenueWidgetProps) {
  const tDelta = delta(props.today.rub, props.todayPrev)
  const wDelta = delta(props.wtd.rub,   props.wtdPrev)
  const mDelta = delta(props.mtd.rub,   props.mtdPrev)

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      <StatCard
        label="Сегодня (UTC)"
        value={fmtRub(props.today.rub)}
        tone={tDelta.tone}
        hint={
          <Hint
            pct={tDelta.pct}
            arrow={tDelta.arrow}
            prev={props.todayPrev}
            label="7 дней назад"
          />
        }
      />
      <StatCard
        label="С понедельника (WTD)"
        value={fmtRub(props.wtd.rub)}
        tone={wDelta.tone}
        hint={
          <Hint
            pct={wDelta.pct}
            arrow={wDelta.arrow}
            prev={props.wtdPrev}
            label="прошлая неделя"
          />
        }
      />
      <StatCard
        label="С 1-го числа (MTD)"
        value={fmtRub(props.mtd.rub)}
        tone={mDelta.tone}
        hint={
          <Hint
            pct={mDelta.pct}
            arrow={mDelta.arrow}
            prev={props.mtdPrev}
            label="прошлый месяц"
          />
        }
      />
    </div>
  )
}

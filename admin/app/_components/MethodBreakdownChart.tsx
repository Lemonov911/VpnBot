'use client'

import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
} from 'recharts'

/**
 * Stacked-bar chart выручки по методу оплаты за 30 дней (для analytics/).
 *
 * Источник — methodBreakdown30d() в lib/db.ts. Каждая точка — один день;
 * стопка = stars (⭐ raw count) + crypto/oxapay/lavatop (RUB).
 *
 * Note: stars показываются как ⭐ count, а не RUB-эквивалент — потому что
 * у Telegram свой реальный курс выплаты, и микс цветов сразу даёт
 * картину «откуда деньги физически приходят». В тултипе показываем
 * raw цифры — админ сам считает; цель графика — пропорции каналов.
 *
 * Если schema-mismatch и в данных только stars-серия — выглядит как
 * однотонный bar chart, что и есть graceful degradation.
 */

export type MethodPoint = {
  day: string
  stars: number       // raw ⭐
  crypto_rub: number  // ₽
  oxapay_rub: number  // ₽
  lavatop_rub: number // ₽
}

const COLORS = {
  stars:       '#facc15',  // amber-400 — Telegram Stars
  crypto_rub:  '#10b981',  // emerald-500 — CryptoBot USDT
  oxapay_rub:  '#a855f7',  // purple-500 — OxaPay
  lavatop_rub: '#3b82f6',  // blue-500 — Lava
}

const LABEL: Record<keyof typeof COLORS, string> = {
  stars:       '⭐ Stars',
  crypto_rub:  '💎 CryptoBot',
  oxapay_rub:  '💰 OxaPay',
  lavatop_rub: '💳 Lava',
}

export function MethodBreakdownChart({ data }: { data: MethodPoint[] }) {
  if (data.length === 0) {
    return (
      <div className="text-xs text-neutral-500 h-[220px] flex items-center justify-center">
        нет данных за 30 дней
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ top: 5, right: 5, left: -20, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#262626" vertical={false} />
        <XAxis
          dataKey="day"
          stroke="#737373"
          fontSize={10}
          tickLine={false}
          axisLine={false}
          tickFormatter={(d: string) => d.slice(5)}
          minTickGap={20}
        />
        <YAxis
          stroke="#737373"
          fontSize={10}
          tickLine={false}
          axisLine={false}
          width={40}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: '#0a0a0a',
            border: '1px solid #404040',
            borderRadius: 8,
            fontSize: 12,
          }}
          labelStyle={{ color: '#a3a3a3', marginBottom: 4 }}
          formatter={(value, name) => {
            const v = Number(value ?? 0)
            const key = String(name) as keyof typeof LABEL
            const display = key === 'stars' ? `⭐ ${v}` : `${v.toLocaleString('ru')} ₽`
            return [display, LABEL[key] ?? String(name)] as [string, string]
          }}
        />
        <Legend
          formatter={(value) => {
            const key = String(value) as keyof typeof LABEL
            return <span style={{ color: '#a3a3a3', fontSize: 11 }}>{LABEL[key] ?? value}</span>
          }}
          iconSize={10}
          wrapperStyle={{ fontSize: 11 }}
        />
        <Bar dataKey="stars"       stackId="rev" fill={COLORS.stars} />
        <Bar dataKey="crypto_rub"  stackId="rev" fill={COLORS.crypto_rub} />
        <Bar dataKey="oxapay_rub"  stackId="rev" fill={COLORS.oxapay_rub} />
        <Bar dataKey="lavatop_rub" stackId="rev" fill={COLORS.lavatop_rub} />
      </BarChart>
    </ResponsiveContainer>
  )
}

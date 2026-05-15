'use client'

import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts'

export type DailyPoint = { day: string; stars: number; paid_subs: number }

/**
 * Линия выручки и платных подписок по дням за 30 дней.
 * Раньше был inline SVG-sparkline — без осей, тултипов и легенды.
 *
 * Прим. dataKey разная — рендерим два графика рядом с разными metric'ами.
 */
export function RevenueArea({ data, metric, color }: { data: DailyPoint[]; metric: 'stars' | 'paid_subs'; color: string }) {
  if (data.length === 0) {
    return <div className="text-xs text-neutral-500 h-[120px] flex items-center justify-center">нет данных за 30 дней</div>
  }

  return (
    <ResponsiveContainer width="100%" height={120}>
      <AreaChart data={data} margin={{ top: 5, right: 5, left: -20, bottom: 0 }}>
        <defs>
          <linearGradient id={`grad-${metric}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%"  stopColor={color} stopOpacity={0.35} />
            <stop offset="95%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#262626" vertical={false} />
        <XAxis
          dataKey="day"
          stroke="#737373"
          fontSize={10}
          tickLine={false}
          axisLine={false}
          tickFormatter={(d: string) => d.slice(5)} // MM-DD
          minTickGap={20}
        />
        <YAxis
          stroke="#737373"
          fontSize={10}
          tickLine={false}
          axisLine={false}
          width={30}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: '#0a0a0a',
            border: '1px solid #404040',
            borderRadius: 8,
            fontSize: 12,
          }}
          labelStyle={{ color: '#a3a3a3', marginBottom: 4 }}
          formatter={(value) => {
            const v = Number(value ?? 0)
            return [
              metric === 'stars' ? `⭐ ${v}` : `${v} шт.`,
              metric === 'stars' ? 'Выручка' : 'Подписки',
            ] as [string, string]
          }}
        />
        <Area
          type="monotone"
          dataKey={metric}
          stroke={color}
          strokeWidth={2}
          fill={`url(#grad-${metric})`}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}

'use client'

import {
  ComposedChart, Bar, Line, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ReferenceLine, ResponsiveContainer, Legend,
} from 'recharts'

type Point = { month: string; new_paid: number; cumulative: number; projected: boolean }

const TIP_STYLE = {
  contentStyle: { background: '#171717', border: '1px solid #262626', borderRadius: 8, fontSize: 12 },
  labelStyle: { color: '#737373' },
}

function fmtMonth(iso: string) {
  const [y, m] = iso.split('-')
  const names = ['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек']
  return `${names[parseInt(m) - 1]} ${y.slice(2)}`
}

export function GoalChart({
  points, goal, currentActive, avgNew,
}: {
  points: Point[]
  goal: number
  currentActive: number
  avgNew: number
}) {
  const pct = Math.min(100, Math.round((currentActive / goal) * 100))
  const projPoint = points.find(p => p.projected && p.cumulative >= goal)
  const eta = projPoint ? fmtMonth(projPoint.month) : null

  // Split into real and projected for styling
  const data = points.map(p => ({
    ...p,
    label: fmtMonth(p.month),
    cum_real: p.projected ? undefined : p.cumulative,
    cum_proj: p.projected ? p.cumulative : undefined,
    bar_real: p.projected ? 0 : p.new_paid,
  }))

  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5 space-y-5">
      {/* Hero progress */}
      <div className="flex items-start justify-between gap-6">
        <div>
          <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-1">Цель</div>
          <div className="flex items-baseline gap-2">
            <span className="text-4xl font-extrabold text-white">{currentActive}</span>
            <span className="text-neutral-500 text-lg">/ {goal.toLocaleString()}</span>
          </div>
          <div className="text-xs text-neutral-500 mt-1">
            платящих сейчас {eta && <span className="text-emerald-400 ml-1">· достигнем ≈ {eta}</span>}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-1">Прирост</div>
          <div className="text-2xl font-bold text-[#2481cc]">+{avgNew}</div>
          <div className="text-xs text-neutral-500">новых/мес (avg)</div>
        </div>
      </div>

      {/* Progress bar */}
      <div>
        <div className="flex justify-between text-[10px] text-neutral-500 mb-1">
          <span>{pct}%</span>
          <span>🎯 1 000</span>
        </div>
        <div className="h-3 bg-neutral-800 rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all"
            style={{
              width: `${pct}%`,
              background: pct < 33 ? '#2481cc' : pct < 66 ? '#10b981' : '#f59e0b',
            }}
          />
        </div>
      </div>

      {/* Chart */}
      <ResponsiveContainer width="100%" height={220}>
        <ComposedChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
          <defs>
            <linearGradient id="gradCum" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#2481cc" stopOpacity={0.3} />
              <stop offset="100%" stopColor="#2481cc" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#262626" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fill: '#737373', fontSize: 10 }}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fill: '#737373', fontSize: 10 }}
            tickLine={false}
            axisLine={false}
            domain={[0, Math.max(goal * 1.05, (data.at(-1)?.cumulative ?? 0) * 1.1)]}
          />
          <Tooltip
            {...TIP_STYLE}
            formatter={(v, name) => {
              if (name === 'cum_real')  return [v, 'Накоплено']
              if (name === 'cum_proj')  return [v, 'Прогноз']
              if (name === 'bar_real')  return [v, 'Новых']
              return [v, name]
            }}
          />
          <ReferenceLine
            y={goal}
            stroke="#f59e0b"
            strokeDasharray="6 3"
            strokeWidth={1.5}
            label={{ value: '🎯 1 000', position: 'right', fill: '#f59e0b', fontSize: 11 }}
          />
          <Bar dataKey="bar_real" fill="#2481cc" opacity={0.5} radius={[2, 2, 0, 0]} name="bar_real" />
          <Area
            type="monotone"
            dataKey="cum_real"
            stroke="#2481cc"
            strokeWidth={2.5}
            fill="url(#gradCum)"
            dot={false}
            connectNulls
            name="cum_real"
          />
          <Line
            type="monotone"
            dataKey="cum_proj"
            stroke="#2481cc"
            strokeWidth={1.5}
            strokeDasharray="5 4"
            dot={false}
            connectNulls
            name="cum_proj"
          />
        </ComposedChart>
      </ResponsiveContainer>

      <div className="text-[10px] text-neutral-600">
        Синие бары — новых платящих в месяц · Сплошная линия — накоплено · Пунктир — прогноз при текущем темпе
      </div>
    </div>
  )
}

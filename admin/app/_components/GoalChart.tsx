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

// Tooltip-обёртка. Native `title` показывает только текст без styling/breaks —
// этот компонент даёт rich-HTML на hover (с переводами строк, цветами).
// `position` = "top"/"bottom" — где появляется относительно anchor'а.
// `group/tt` нужен из-за того что родители могут уже использовать `group`
// для других целей; named scope изолирует.
function Tip({
  label, children, position = 'top',
}: {
  label: React.ReactNode
  children: React.ReactNode
  position?: 'top' | 'bottom'
}) {
  return (
    <span className="group/tt relative inline-flex cursor-help">
      {label}
      <span
        className={
          'absolute z-20 hidden group-hover/tt:block ' +
          'left-1/2 -translate-x-1/2 ' +
          (position === 'top' ? 'bottom-full mb-2' : 'top-full mt-2') +
          ' w-64 px-3 py-2 rounded-lg ' +
          'bg-neutral-950 border border-neutral-700 ' +
          'text-[11px] leading-snug text-neutral-300 ' +
          'shadow-lg shadow-black/50 pointer-events-none whitespace-normal text-left'
        }
      >
        {children}
      </span>
    </span>
  )
}

// Контент тултипов — описание целей и базовой модели прибыли.
// Числа взяты из obsidian «Прикидки 150k/мес» (ARPU 350 × 0.9 net = 315 ₽,
// minus server 22 ₽, minus replacement 60 ₽/user/мес = 233 ₽ contribution).
function SubGoalTip({ subGoal }: { subGoal: number }) {
  return (
    <>
      <div className="font-semibold text-emerald-400 mb-1">🚀 Промежуточная цель</div>
      <div>~150к ₽/мес чистой прибыли при {subGoal} платящих:</div>
      <div className="mt-1 text-neutral-400">
        {subGoal} × 233 ₽ contribution<br />
        ≈ 138 000 ₽/мес <span className="text-neutral-500">(базовая модель)</span>
      </div>
      <div className="mt-1 text-neutral-500">
        Допущения: ARPU 350 ₽ (60% База + 40% Макс), churn 30%, CAC 200 ₽.
      </div>
    </>
  )
}

function GoalTip({ goal }: { goal: number }) {
  return (
    <>
      <div className="font-semibold text-amber-400 mb-1">🎯 Основная цель</div>
      <div>~230к ₽/мес чистой прибыли при {goal.toLocaleString()} платящих.</div>
      <div className="mt-1 text-neutral-400">
        Зона уверенной операционки — найм support, доп. регионы, шортсы on payroll.
      </div>
      <div className="mt-1 text-neutral-500">
        Прогноз: при текущем темпе достижение за {goal === 1000 ? '~12 мес' : 'месяцы'}.
      </div>
    </>
  )
}

export function GoalChart({
  points, goal, subGoal, currentActive, avgNew,
}: {
  points: Point[]
  goal: number
  // Промежуточная цель — рисуется второй reference line (зелёная, мягче).
  // Когда subGoal достигнут — line остаётся, но визуально приглушается
  // и в подписи появляется ✓. Прогноз ETA показывается только если ещё
  // не достигнут (иначе бессмысленно). См. obsidian «Прикидки 150k/мес».
  subGoal?: number
  currentActive: number
  avgNew: number
}) {
  const pct = Math.min(100, Math.round((currentActive / goal) * 100))
  const projPoint = points.find(p => p.projected && p.cumulative >= goal)
  const eta = projPoint ? fmtMonth(projPoint.month) : null
  const subReached = subGoal != null && currentActive >= subGoal
  const projSubPoint = subGoal != null
    ? points.find(p => p.projected && p.cumulative >= subGoal)
    : null
  const subEta = !subReached && projSubPoint ? fmtMonth(projSubPoint.month) : null

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
          <div className="text-xs text-neutral-500 mt-1 flex flex-wrap gap-x-2 items-baseline">
            <span>платящих сейчас</span>
            {subEta && subGoal != null && (
              <Tip
                position="bottom"
                label={<span className="text-emerald-400 border-b border-dotted border-emerald-400/40">· 🚀 {subGoal} ≈ {subEta}</span>}
              >
                <SubGoalTip subGoal={subGoal} />
              </Tip>
            )}
            {eta && (
              <Tip
                position="bottom"
                label={<span className="text-amber-400 border-b border-dotted border-amber-400/40">· 🎯 {goal.toLocaleString()} ≈ {eta}</span>}
              >
                <GoalTip goal={goal} />
              </Tip>
            )}
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
          <div className="flex gap-3">
            {subGoal != null && (
              <Tip
                position="top"
                label={
                  <span className={(subReached ? 'text-emerald-400 ' : '') + 'border-b border-dotted border-emerald-400/40'}>
                    🚀 {subGoal}{subReached ? ' ✓' : ''}
                  </span>
                }
              >
                <SubGoalTip subGoal={subGoal} />
              </Tip>
            )}
            <Tip
              position="top"
              label={<span className="border-b border-dotted border-neutral-500/40">🎯 {goal.toLocaleString()}</span>}
            >
              <GoalTip goal={goal} />
            </Tip>
          </div>
        </div>
        <div className="h-3 bg-neutral-800 rounded-full overflow-hidden relative">
          <div
            className="h-full rounded-full transition-all"
            style={{
              width: `${pct}%`,
              background: pct < 33 ? '#2481cc' : pct < 66 ? '#10b981' : '#f59e0b',
            }}
          />
          {/* Маркер промежуточной цели — тонкая вертикальная линия на баре */}
          {subGoal != null && subGoal < goal && (
            <div
              className="absolute top-0 bottom-0 w-px bg-emerald-400/70"
              style={{ left: `${(subGoal / goal) * 100}%` }}
              title={`Промежуточная цель: ${subGoal}`}
            />
          )}
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
          {subGoal != null && (
            <ReferenceLine
              y={subGoal}
              stroke="#10b981"
              strokeDasharray="3 3"
              strokeWidth={1.5}
              strokeOpacity={subReached ? 0.35 : 1}
              label={{
                value: subReached ? `🚀 ${subGoal} ✓` : `🚀 ${subGoal}`,
                position: 'right',
                fill: '#10b981',
                fillOpacity: subReached ? 0.6 : 1,
                fontSize: 11,
              }}
            />
          )}
          <ReferenceLine
            y={goal}
            stroke="#f59e0b"
            strokeDasharray="6 3"
            strokeWidth={1.5}
            label={{ value: `🎯 ${goal.toLocaleString()}`, position: 'right', fill: '#f59e0b', fontSize: 11 }}
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
        {subGoal != null && (
          <> · 🚀 промежуточная цель (~150к ₽/мес profit) · 🎯 основная цель</>
        )}
      </div>
    </div>
  )
}

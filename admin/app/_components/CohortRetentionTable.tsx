import type { CohortRow } from '@/lib/db'

/**
 * Weekly cohort retention heatmap (12×12).
 *
 * Rows = signup-week (от старого к новому, ровно 12). Columns = «неделя
 * с регистрации» (week 0..11). Cell = % cohort'а с активной/grace платной
 * подпиской в эту календарную неделю.
 *
 * Цветовая шкала emerald — стандарт «зелёное = хорошо» для retention:
 * grey  → нулевая retention (или ещё пусто, см. ниже),
 * 1-24% → emerald-900/30,
 * 25-49% → emerald-800/60,
 * 50-74% → emerald-700/80,
 * 75-100% → emerald-600.
 *
 * Empty cells (week > возраст-cohort'а в неделях, т.е. этой клетки ещё
 * не может быть физически) — neutral grey без значения. Они идут по
 * диагонали в правом-верхнем треугольнике таблицы.
 *
 * Cohort-метка — название месяца + число понедельника (e.g. «May 12»).
 * Под меткой — размер cohort'а («N юзеров»), чтоб % имел смысл (10%
 * от 100 — это серьёзно; 10% от 3 — шум).
 *
 * Server component: данных мало, нет interactivity. Если в будущем
 * захотим drill-down (клик по клетке → список user_id) — превратим
 * в client с onCellClick.
 */
export function CohortRetentionTable({ data }: { data: CohortRow[] }) {
  if (data.length === 0) {
    return (
      <div className="text-sm text-neutral-500">нет данных</div>
    )
  }

  const totalCohortSize = data.reduce((a, b) => a + b.cohortSize, 0)

  // current-week index: indexOf cohort где «возраст» = 0. Это последняя
  // строка (data[data.length - 1]); её week-0 — последняя реальная клетка.
  // Используется для определения «эта клетка в будущем».
  const lastCohortIdx = data.length - 1

  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5 overflow-x-auto">
      <div className="text-xs text-neutral-500 uppercase tracking-wider mb-1">
        Cohort retention (12 недель)
      </div>
      <div className="text-[11px] text-neutral-500 mb-4">
        Когорты — % активных подписок по неделям с регистрации
      </div>

      {totalCohortSize === 0 ? (
        <div className="text-sm text-neutral-500 py-4">
          нет регистраций за последние 12 недель
        </div>
      ) : (
        <>
          <table className="text-[11px] border-separate border-spacing-1 min-w-max">
            <thead>
              <tr>
                <th className="text-left font-medium text-neutral-500 px-2 py-1 sticky left-0 bg-neutral-900 z-10">
                  Cohort
                </th>
                {Array.from({ length: 12 }, (_, w) => (
                  <th
                    key={w}
                    className="font-medium text-neutral-500 px-1 py-1 w-12 text-center"
                  >
                    W{w}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.map((cohort, cohortIdx) => {
                // Возраст cohort'а в неделях относительно текущей (newest = 0).
                // lastCohortIdx - cohortIdx = «сколько недель назад зарегался».
                const ageWeeks = lastCohortIdx - cohortIdx
                return (
                  <tr key={cohort.cohortWeekStart}>
                    <td className="text-left font-medium text-neutral-300 px-2 py-1 whitespace-nowrap sticky left-0 bg-neutral-900 z-10">
                      <div>{cohort.cohortLabel}</div>
                      <div className="text-[10px] text-neutral-600">
                        {cohort.cohortSize} {pluralUsers(cohort.cohortSize)}
                      </div>
                    </td>
                    {cohort.retention.map(cell => {
                      const isFuture = cell.week > ageWeeks
                      const emptyCohort = cohort.cohortSize === 0
                      return (
                        <td
                          key={cell.week}
                          className={[
                            'px-1 py-1 w-12 h-10 text-center rounded text-[11px] font-medium',
                            cellBg(cell.pct, isFuture, emptyCohort),
                            cellTextColor(cell.pct, isFuture, emptyCohort),
                          ].join(' ')}
                          title={
                            isFuture
                              ? 'ещё не случилось'
                              : emptyCohort
                                ? 'нет cohort'
                                : `${cell.active}/${cohort.cohortSize} активны`
                          }
                        >
                          {isFuture || emptyCohort ? '·' : `${cell.pct}%`}
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
            </tbody>
          </table>

          {/* Легенда цветов снизу — без неё heatmap читается хуже, особенно
              для новых юзеров которые не привыкли к зелёной шкале retention. */}
          <div className="flex items-center gap-2 mt-4 text-[10px] text-neutral-500">
            <span>0%</span>
            <span className="w-6 h-3 rounded bg-neutral-800" />
            <span className="w-6 h-3 rounded bg-emerald-900/30" />
            <span className="w-6 h-3 rounded bg-emerald-800/60" />
            <span className="w-6 h-3 rounded bg-emerald-700/80" />
            <span className="w-6 h-3 rounded bg-emerald-600" />
            <span className="w-6 h-3 rounded bg-emerald-500" />
            <span>100%</span>
          </div>
        </>
      )}
    </div>
  )
}

/** Tailwind classes for cell background. Spec из таска: */
function cellBg(pct: number, isFuture: boolean, emptyCohort: boolean): string {
  if (isFuture || emptyCohort) return 'bg-neutral-800/40'
  if (pct === 0)               return 'bg-neutral-800'
  if (pct < 25)                return 'bg-emerald-900/30'
  if (pct < 50)                return 'bg-emerald-800/60'
  if (pct < 75)                return 'bg-emerald-700/80'
  if (pct < 90)                return 'bg-emerald-600'
  return 'bg-emerald-500'
}

/** Text color contrast on cells. Тёмные клетки → светлый текст; светлые → тёмный. */
function cellTextColor(pct: number, isFuture: boolean, emptyCohort: boolean): string {
  if (isFuture || emptyCohort) return 'text-neutral-600'
  if (pct === 0)               return 'text-neutral-500'
  if (pct >= 75)               return 'text-emerald-50'
  return 'text-emerald-100'
}

/** «1 юзер» / «2 юзера» / «5 юзеров» — простая русская плюрализация. */
function pluralUsers(n: number): string {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod100 >= 11 && mod100 <= 14) return 'юзеров'
  if (mod10 === 1) return 'юзер'
  if (mod10 >= 2 && mod10 <= 4) return 'юзера'
  return 'юзеров'
}

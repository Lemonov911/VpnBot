import type { ReactNode } from 'react'
import { EmptyState } from './EmptyState'

/**
 * Table<T> — типобезопасная обёртка над <table> с описанием колонок.
 *
 * Раньше каждая страница (clients, payments, grant, servers, tickets, clients/[id])
 * писала свою ad-hoc разметку с почти одинаковыми классами. Сейчас унифицируем
 * только пилотные кейсы (clients top-50, payments recent) — остальные останутся
 * как есть до feature-треков A/B/C.
 *
 * Стили — те же что были вручную:
 *   - заголовок `text-xs text-neutral-500 uppercase tracking-wide`, нижняя граница
 *   - тело `divide-y divide-neutral-800`, hover на строке
 *   - выравнивание задаётся через `align` колонки (left/right/center)
 *
 * Empty-state встроен: если `rows.length === 0`, рендерим <EmptyState> с
 * переданным `emptyText` (по умолчанию «нет данных»).
 */
export type Column<T> = {
  /** ключ для React key и для понимания отладки; сам по себе не отображается */
  key: string
  label: string
  render: (row: T) => ReactNode
  align?: 'left' | 'right' | 'center'
  /** дополнительные классы на <td>/<th> для конкретной колонки */
  className?: string
}

export type TableProps<T> = {
  rows: T[]
  columns: Column<T>[]
  /** текст empty-state когда rows.length === 0 */
  emptyText?: string
  /** функция для извлечения React key (по умолчанию index) */
  rowKey?: (row: T) => string | number
}

const ALIGN_CLS: Record<'left' | 'right' | 'center', string> = {
  left:   'text-left',
  right:  'text-right',
  center: 'text-center',
}

export function Table<T>({
  rows,
  columns,
  emptyText = 'нет данных',
  rowKey,
}: TableProps<T>) {
  if (rows.length === 0) {
    return <EmptyState title={emptyText} />
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-xs text-neutral-500 uppercase tracking-wide">
          <tr className="border-b border-neutral-800">
            {columns.map(col => (
              <th
                key={col.key}
                className={`px-4 py-2 font-medium ${ALIGN_CLS[col.align ?? 'left']} ${col.className ?? ''}`}
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-neutral-800">
          {rows.map((row, i) => (
            <tr
              key={rowKey ? rowKey(row) : i}
              className="hover:bg-neutral-800/30 transition-colors"
            >
              {columns.map(col => (
                <td
                  key={col.key}
                  className={`px-4 py-2 ${ALIGN_CLS[col.align ?? 'left']} ${col.className ?? ''}`}
                >
                  {col.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

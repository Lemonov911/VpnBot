import type { ReactNode } from 'react'

/**
 * EmptyState — единый плейсхолдер для пустых списков/таблиц.
 *
 * Раньше в каждой странице было что-то вроде
 *   <div className="px-5 py-8 text-center text-sm text-neutral-500">пока никто не платил</div>
 * Теперь единая структура: icon (опционально), title (обязательно), description, action.
 *
 * Используется и сам по себе (вне таблицы), и как `emptyText`-fallback внутри
 * `<Table>` (когда rows.length === 0 — рендерим EmptyState с переданным title).
 */
export type EmptyStateProps = {
  icon?: ReactNode
  title: string
  description?: string
  action?: ReactNode
}

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="px-5 py-10 text-center">
      {icon && <div className="mb-2 text-2xl text-neutral-600">{icon}</div>}
      <div className="text-sm text-neutral-400">{title}</div>
      {description && <div className="text-xs text-neutral-600 mt-1">{description}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

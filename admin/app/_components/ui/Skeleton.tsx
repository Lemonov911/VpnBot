/**
 * Skeleton — простой placeholder для loading states.
 *
 * Используем Tailwind `animate-pulse` + `bg-neutral-800`. Это нативный
 * Tailwind shimmer, не нужно подключать кастомные keyframes.
 *
 * Три варианта:
 *   - `<Skeleton />`              — generic блок (передай className для размера)
 *   - `<SkeletonTable rows cols />` — таблица-плейсхолдер для пилотных страниц
 *   - `<SkeletonCard />`          — карточка KPI-плейсхолдер
 *
 * Пока не используется по умолчанию — даём треку A/B/C использовать при
 * добавлении lazy/streaming разделов.
 */

export type SkeletonProps = {
  className?: string
}

export function Skeleton({ className = '' }: SkeletonProps) {
  return <div className={`animate-pulse bg-neutral-800 rounded ${className}`} />
}

export type SkeletonTableProps = {
  rows?: number
  cols?: number
}

export function SkeletonTable({ rows = 5, cols = 4 }: SkeletonTableProps) {
  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-2xl overflow-hidden">
      <div className="px-5 py-4 border-b border-neutral-800">
        <Skeleton className="h-4 w-32" />
      </div>
      <div className="divide-y divide-neutral-800">
        {Array.from({ length: rows }).map((_, r) => (
          <div key={r} className="px-5 py-3 flex items-center gap-4">
            {Array.from({ length: cols }).map((__, c) => (
              <Skeleton key={c} className="h-4 flex-1" />
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

export function SkeletonCard() {
  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5 space-y-2">
      <Skeleton className="h-3 w-20" />
      <Skeleton className="h-7 w-24" />
      <Skeleton className="h-3 w-16" />
    </div>
  )
}

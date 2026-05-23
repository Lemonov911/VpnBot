// W2 #15 — общий skeleton-компонент для loading-стейтов страниц.
//
// До этого SkeletonPage был только в VPN.tsx (приватная функция), а Home /
// ESim / Support при async-загрузке показывали пустой экран (или одиночный
// текст «Loading…»). Это давало визуальный flash на медленных сетях —
// юзер думает что аппа сломана.
//
// `.skeleton` CSS-класс определён в index.css (animation: skeleton-pulse).
// Мы только композим высоты/радиусы под конкретный layout страницы.

type SkeletonProps = {
  className?: string
}

/** Базовый skeleton-блок. Используй для inline-плейсхолдеров (карточки,
 *  кнопки, статы). Высоту/ширину передай через className. */
export function Skeleton({ className = '' }: SkeletonProps) {
  return <div className={`skeleton ${className}`} />
}

/** Универсальная skeleton-сетка для full-page loading-стейта. Подходит
 *  для Home / VPN / Configs — крупная карточка вверху + ряд secondary
 *  блоков. Layout совпадает с реальным `.page` контейнером (gap + padding),
 *  чтобы при переходе loading → loaded не было «прыжка». */
export function SkeletonPage() {
  return (
    <div className="page pb-[calc(env(safe-area-inset-bottom)+96px)] gap-2.5">
      <div className="h-4" />
      <div className="skeleton h-40 rounded-[18px]" />
      <div className="skeleton h-[60px] rounded-xl" />
      <div className="skeleton h-[60px] rounded-xl" />
      <div className="skeleton h-[60px] rounded-xl" />
    </div>
  )
}

export default Skeleton

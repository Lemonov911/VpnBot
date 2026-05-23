/**
 * toast — re-export для sonner.
 *
 * Foundation D добавляет sonner в проект и монтирует <Toaster /> в
 * `app/layout.tsx`. Этот файл — единая точка импорта для всех страниц,
 * чтобы при будущей замене библиотеки достаточно было поменять одну
 * строчку.
 *
 * Использование:
 *   import { toast } from '@/app/_components/ui'
 *   toast.success('Готово')
 *   toast.error('Не получилось', { description: '...' })
 *
 * NB: фактический переход на toast вместо alert/confirm — задача треков
 * A/B/C, здесь только инфраструктура.
 */
export { toast } from 'sonner'

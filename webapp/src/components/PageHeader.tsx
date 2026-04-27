import { useNavigate } from 'react-router-dom'

interface PageHeaderProps {
  logoSrc: string
  title: string
  subtitle: string
  backTo?: string
}

export function PageHeader({ logoSrc, title, subtitle, backTo }: PageHeaderProps) {
  const nav = useNavigate()
  return (
    <div className="flex items-center gap-3 pt-1.5 pb-0.5">
      <img
        src={logoSrc}
        alt="MAX"
        className="w-10 h-10 rounded-[11px] shrink-0 object-cover"
      />
      <div className="min-w-0">
        <div className="font-extrabold text-[20px] text-[var(--tg-theme-text-color)] tracking-[-0.3px] leading-[1.2]">
          {title}
        </div>
        <div className="text-xs text-[var(--tg-theme-hint-color)] mt-px">
          {subtitle}
        </div>
      </div>
    </div>
  )
}
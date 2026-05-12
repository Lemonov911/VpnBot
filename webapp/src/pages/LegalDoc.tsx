import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import { marked } from 'marked'

/**
 * Универсальная страница для рендера legal-документов (privacy / disclaimer)
 * из `public/legal/*.md`. Открывается по `/legal/:slug`.
 *
 * Документы — короткие, статические, переживают деплой как обычные ассеты.
 * Когда оформится самозанятость и заполнятся плейсхолдеры [ИП ФИО]/[ИНН] —
 * меняется markdown-файл в репо, webapp пересобирается, юзеры видят новое.
 */

const TITLES: Record<string, string> = {
  privacy:    'Политика конфиденциальности',
  disclaimer: 'Уведомление 149-ФЗ',
}

export default function LegalDoc() {
  const { slug } = useParams<{ slug: string }>()
  const nav = useNavigate()
  const [html, setHtml] = useState('')
  const [err,  setErr]  = useState('')

  useEffect(() => {
    WebApp.BackButton.show()
    const goBack = () => nav(-1)
    WebApp.BackButton.onClick(goBack)
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(goBack) }
  }, [nav])

  useEffect(() => {
    if (!slug || !(slug in TITLES)) {
      setErr('Документ не найден')
      return
    }
    document.title = `${TITLES[slug]} — MAX VPN ESIM`
    fetch(`${import.meta.env.BASE_URL}legal/${slug}.md`)
      .then(r => r.ok ? r.text() : Promise.reject(`HTTP ${r.status}`))
      .then(md => setHtml(marked.parse(md, { gfm: true, breaks: false }) as string))
      .catch(e => setErr(String(e)))
  }, [slug])

  if (err) {
    return (
      <div className="page pt-2">
        <div className="rounded-[16px] p-4 bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)]">
          <div className="text-sm font-bold text-rose-500">⚠ {err}</div>
        </div>
      </div>
    )
  }

  return (
    <div className="page pt-2">
      <div
        className="legal-prose text-[var(--tg-theme-text-color)] text-[14px] leading-relaxed"
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  )
}

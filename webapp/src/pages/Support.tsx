import React, { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import { createSupportTicket, uploadTicketWithPhotos, type SupportCategory } from '../api'
import { useT, useLang } from '../i18n'
import { HAPP_LINKS } from '../data/happ'
import { AppFooter } from '../components/AppFooter'

// Screenshot/video upload limits — must match backend (handle_support_ticket).
// If you change these, also update bot/services/webapp_api.py.
const MAX_FILES = 5
const MAX_PHOTO_SIZE = 5 * 1024 * 1024
const MAX_VIDEO_SIZE = 10 * 1024 * 1024
const MAX_VIDEOS = 1
const MAX_TOTAL = 30 * 1024 * 1024
// Files larger than this don't render a base64 preview — generating one
// from a 5 MB JPEG on a mid-range Android device stalls the UI thread for
// ~2s. We render a generic placeholder instead.
const THUMB_LIMIT = 2 * 1024 * 1024
const ACCEPT_TYPES =
  'image/jpeg,image/png,image/webp,image/heic,image/heif,' +
  'video/mp4,video/quicktime,.mp4,.mov'

function isVideoFile(f: File): boolean {
  // iOS Safari иногда даёт пустой `type` для .mov из «Файлов» — fallback по расширению.
  if (f.type.startsWith('video/')) return true
  return /\.(mp4|mov|m4v)$/i.test(f.name)
}

type AttachedFile = {
  file: File
  thumb: string | null
  id: string
  isVideo: boolean
}


// FAQ icons — атрибут вопроса, не индекса.
// Раньше FAQ_META[i] коллапсил per-FAQ icon к позиции в массиве, и при
// SHOW_ESIM=false вопрос про платёж (q6) подтягивал phone-with-plus иконку
// от выпавшего eSIM-вопроса. Теперь meta едет вместе с item.
const META_VPN_APP_CHOICE = { color: '#27ae60', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round"/></svg> }
const META_TROUBLESHOOT  = { color: '#2481cc', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 16v-8M8 12l4 4 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><rect x="3" y="3" width="18" height="18" rx="3" stroke="#fff" strokeWidth="2"/></svg> }
const META_CLIENT_CHOICE = { color: '#e67e22', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><rect x="5" y="2" width="14" height="20" rx="2" stroke="#fff" strokeWidth="2"/><path d="M9 8h6M9 12h6M9 16h4" stroke="#fff" strokeWidth="1.5" strokeLinecap="round"/></svg> }
const META_ESIM_INSTALL  = { color: '#ff3b30', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><rect x="5" y="2" width="14" height="20" rx="2" stroke="#fff" strokeWidth="2"/><path d="M9 12h6M12 9v6" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/></svg> }
const META_ESIM_ACTIVATE = { color: '#8e44ad', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><rect x="2" y="5" width="20" height="14" rx="2" stroke="#fff" strokeWidth="2"/><path d="M2 10h20" stroke="#fff" strokeWidth="2"/><path d="M6 15h4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/></svg> }
const META_PAYMENT_FAIL  = { color: '#ff2d55', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><rect x="2" y="5" width="20" height="14" rx="2" stroke="#fff" strokeWidth="2"/><path d="M2 10h20" stroke="#fff" strokeWidth="2"/><path d="M6 15h4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/><path d="M16 14v2M18 14v2" stroke="#fff" strokeWidth="1.5" strokeLinecap="round"/></svg> }

type FaqMeta = { color: string; icon: React.ReactNode }

/** Inline parser: **жирный** + любой текст внутри строки. */
function inlineFmt(text: string, keyPrefix = '') {
  return text.split(/(\*\*[^*]+\*\*)/g).map((chunk, i) => {
    if (chunk.startsWith('**') && chunk.endsWith('**')) {
      return <strong key={`${keyPrefix}-b-${i}`} className="text-[var(--tg-theme-text-color)]">{chunk.slice(2, -2)}</strong>
    }
    return <React.Fragment key={`${keyPrefix}-t-${i}`}>{chunk}</React.Fragment>
  })
}

/** Markdown-light парсер для FAQ-ответов.
 *  - Строки `1.`, `2.` ... — нумерованный список
 *  - Строки `• ` или `- ` — буллет-лист
 *  - Пустая строка — разделитель параграфов
 *  - **bold** — жирный inline */
function FaqText({ text }: { text: string }) {
  const lines = text.split('\n')

  type Block =
    | { kind: 'para'; lines: string[] }
    | { kind: 'ol';   items: string[] }
    | { kind: 'ul';   items: string[] }

  const blocks: Block[] = []
  const olRe = /^(\d+)\.\s+(.*)$/
  const ulRe = /^[•\-]\s+(.*)$/

  for (const raw of lines) {
    const ln = raw.trimEnd()
    const ol = ln.match(olRe)
    const ul = ln.match(ulRe)
    const last = blocks[blocks.length - 1]
    if (ol) {
      if (last?.kind === 'ol') last.items.push(ol[2])
      else blocks.push({ kind: 'ol', items: [ol[2]] })
    } else if (ul) {
      if (last?.kind === 'ul') last.items.push(ul[1])
      else blocks.push({ kind: 'ul', items: [ul[1]] })
    } else if (ln === '') {
      if (last?.kind === 'para') last.lines.push('')  // sep
    } else {
      if (last?.kind === 'para') last.lines.push(ln)
      else blocks.push({ kind: 'para', lines: [ln] })
    }
  }

  return (
    <div className="space-y-2.5">
      {blocks.map((b, bi) => {
        if (b.kind === 'ol') {
          return (
            <ol key={bi} className="list-decimal pl-5 space-y-1">
              {b.items.map((it, ii) => <li key={ii}>{inlineFmt(it, `${bi}-${ii}`)}</li>)}
            </ol>
          )
        }
        if (b.kind === 'ul') {
          return (
            <ul key={bi} className="list-disc pl-5 space-y-1">
              {b.items.map((it, ii) => <li key={ii}>{inlineFmt(it, `${bi}-${ii}`)}</li>)}
            </ul>
          )
        }
        return (
          <p key={bi}>
            {b.lines.map((ln, li) => (
              <React.Fragment key={li}>
                {li > 0 && <br />}
                {inlineFmt(ln, `${bi}-${li}`)}
              </React.Fragment>
            ))}
          </p>
        )
      })}
    </div>
  )
}

function DownloadChip({ href, label }: { href: string; label: string }) {
  return (
    <button
      onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); WebApp.openLink(href) }}
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11.5px] font-medium bg-[var(--tg-theme-button-color,#2481cc)]/12 text-[var(--tg-theme-button-color,#2481cc)] border-none cursor-pointer"
    >
      ↗ {label}
    </button>
  )
}

function AppChoiceAnswer() {
  // Bilingual JSX-rich answer. The structure has inline <strong> / <br /> tags
  // and download chips, which don't translate cleanly via plain-string keys —
  // so we branch on `lang` and inline both versions. Pragmatic, not DRY.
  const { lang } = useLang()
  if (lang === 'en') {
    return (
      <div className="space-y-3">
        <p>Open <strong className="text-[var(--tg-theme-text-color)]">«My configs»</strong> in this app and check what's there:</p>
        <div className="pl-2 border-l-2 border-purple/40">
          <p className="mb-1">
            See the <strong className="text-[var(--tg-theme-text-color)]">«Happ subscription URL»</strong> card?
            <br />→ install <strong className="text-[var(--tg-theme-text-color)]">Happ</strong>. Copy the URL, in the app: «+» → «From subscription» → paste.
          </p>
          <div className="flex flex-wrap gap-1.5 mt-1.5">
            <DownloadChip href={HAPP_LINKS.site} label="iOS" />
            <DownloadChip href={HAPP_LINKS.android} label="Android" />
            <DownloadChip href="https://happ.su" label="Win / macOS" />
          </div>
        </div>
        <div className="pl-2 border-l-2 border-cyan-500/40">
          <p className="mb-1">
            See an <strong className="text-[var(--tg-theme-text-color)]">AmneziaWG slot</strong> with QR / download buttons?
            <br />→ install <strong className="text-[var(--tg-theme-text-color)]">Amnezia VPN</strong> (full client) or <strong className="text-[var(--tg-theme-text-color)]">AmneziaWG</strong> (lightweight, this protocol only).
            Import: «+» → «Scan QR» or «From file».
          </p>
          <div className="text-[11px] font-semibold text-[var(--tg-theme-hint-color)] mt-2 mb-1">Amnezia VPN</div>
          <div className="flex flex-wrap gap-1.5">
            <DownloadChip href="https://apps.apple.com/app/amneziavpn/id1600529900" label="iOS" />
            <DownloadChip href="https://play.google.com/store/apps/details?id=org.amnezia.vpn" label="Android" />
            <DownloadChip href="https://amnezia.org/downloads" label="Win / macOS / Linux" />
          </div>
          <div className="text-[11px] font-semibold text-[var(--tg-theme-hint-color)] mt-2 mb-1">AmneziaWG</div>
          <div className="flex flex-wrap gap-1.5">
            <DownloadChip href="https://apps.apple.com/app/amneziawg/id6478942365" label="iOS" />
            <DownloadChip href="https://play.google.com/store/apps/details?id=org.amnezia.awg" label="Android" />
          </div>
        </div>
      </div>
    )
  }
  return (
    <div className="space-y-3">
      <p>Открой <strong className="text-[var(--tg-theme-text-color)]">«Мои конфиги»</strong> в этом приложении и посмотри что там есть:</p>
      <div className="pl-2 border-l-2 border-purple/40">
        <p className="mb-1">
          Видишь карточку <strong className="text-[var(--tg-theme-text-color)]">«Ссылка для Happ»</strong>
          <br />→ ставь <strong className="text-[var(--tg-theme-text-color)]">Happ</strong>. Скопируй ссылку, в приложении: «+» → «Из подписки» → вставь.
        </p>
        <div className="flex flex-wrap gap-1.5 mt-1.5">
          <DownloadChip href={HAPP_LINKS.site} label="iOS" />
          <DownloadChip href={HAPP_LINKS.android} label="Android" />
          <DownloadChip href="https://happ.su" label="Win / macOS" />
        </div>
      </div>
      <div className="pl-2 border-l-2 border-cyan-500/40">
        <p className="mb-1">
          Видишь <strong className="text-[var(--tg-theme-text-color)]">AmneziaWG-слот</strong> с кнопками QR / скачать
          <br />→ ставь <strong className="text-[var(--tg-theme-text-color)]">Amnezia VPN</strong> (полный клиент) или <strong className="text-[var(--tg-theme-text-color)]">AmneziaWG</strong> (легче, только для этого протокола).
          Импорт: «+» → «Сканировать QR» или «Из файла».
        </p>
        <div className="text-[11px] font-semibold text-[var(--tg-theme-hint-color)] mt-2 mb-1">Amnezia VPN</div>
        <div className="flex flex-wrap gap-1.5">
          <DownloadChip href="https://apps.apple.com/app/amneziavpn/id1600529900" label="iOS" />
          <DownloadChip href="https://play.google.com/store/apps/details?id=org.amnezia.vpn" label="Android" />
          <DownloadChip href="https://amnezia.org/downloads" label="Win / macOS / Linux" />
        </div>
        <div className="text-[11px] font-semibold text-[var(--tg-theme-hint-color)] mt-2 mb-1">AmneziaWG</div>
        <div className="flex flex-wrap gap-1.5">
          <DownloadChip href="https://apps.apple.com/app/amneziawg/id6478942365" label="iOS" />
          <DownloadChip href="https://play.google.com/store/apps/details?id=org.amnezia.awg" label="Android" />
        </div>
      </div>
    </div>
  )
}

function FaqGroup({ t }: { t: ReturnType<typeof useT> }) {
  const [open, setOpen] = useState<number | null>(null)
  // FAQ q3+a3 — про установку eSIM. Скрываем если eSIM выключен (build flag),
  // иначе юзер видит подробную инструкцию на отсутствующий в UI продукт.
  const SHOW_ESIM = import.meta.env.VITE_SHOW_ESIM !== 'false'
  // Порядок:
  //   q1 «какое приложение нужно» — JSX-rich answer с кнопками-ссылками
  //   q2 «не подключается» — самый частый troubleshoot
  //   q3 «Happ vs Amnezia VPN» — закрывает confusion о выборе клиента
  //   q4/q5 — eSIM (только если SHOW_ESIM)
  //   q6 — payment failed
  const faqItems: { q: string; a: React.ReactNode; meta: FaqMeta }[] = [
    { q: t('faq_q1'), a: <AppChoiceAnswer />,             meta: META_VPN_APP_CHOICE },
    { q: t('faq_q2'), a: t('faq_a2'),                     meta: META_TROUBLESHOOT },
    { q: t('faq_q3'), a: t('faq_a3'),                     meta: META_CLIENT_CHOICE },
    ...(SHOW_ESIM ? [
      { q: t('faq_q4' as never), a: t('faq_a4' as never), meta: META_ESIM_INSTALL },
      { q: t('faq_q5' as never), a: t('faq_a5' as never), meta: META_ESIM_ACTIVATE },
    ] : []),
    { q: t('faq_q6' as never), a: t('faq_a6' as never),   meta: META_PAYMENT_FAIL },
  ]
  return (
    <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
      {faqItems.map(({ q, a, meta }, i) => (
        <div key={i}>
          <button
            onClick={() => { setOpen(open === i ? null : i); WebApp.HapticFeedback.selectionChanged() }}
            className={`w-full border-none bg-transparent py-[14px] px-4 cursor-pointer flex items-center gap-3 ${(open === i || i < faqItems.length - 1) ? 'border-b border-solid border-[var(--card-border)]' : ''}`}
          >
            <div className="w-9 h-9 rounded-[10px] shrink-0 flex items-center justify-center" style={{ background: meta.color }}>
              {meta.icon}
            </div>
            <span className="flex-1 text-[14px] font-semibold text-[var(--tg-theme-text-color)] text-left">{q}</span>
            <svg width="7" height="12" viewBox="0 0 7 12" fill="none" className={`shrink-0 transition-transform duration-200 ${open === i ? 'rotate-90' : ''}`}>
              <path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </button>
          {open === i && (
            <div className={`py-3 px-4 pl-[60px] text-[13px] text-[var(--tg-theme-hint-color)] leading-[1.6] ${i < faqItems.length - 1 ? 'border-b border-solid border-[var(--card-border)]' : ''}`}>
              {/* String → парсим **bold** + \n; JSX (q1) — рендерим как есть */}
              {typeof a === 'string' ? <FaqText text={a} /> : a}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

type PageState = 'form' | 'sending' | 'done' | 'error'

export default function Support() {
  const nav = useNavigate()
  const t   = useT()
  const accent = 'var(--tg-theme-button-color, #2481cc)'

  const [category, setCategory] = useState<SupportCategory>('vpn')
  const [message,  setMessage]  = useState('')
  const [state,    setState]    = useState<PageState>('form')
  const [ticketId, setTicketId] = useState<number | null>(null)
  const [errMsg,   setErrMsg]   = useState('')
  const [files, setFiles] = useState<AttachedFile[]>([])
  const [uploadProgress, setUploadProgress] = useState<number | null>(null)
  // W2 #9 — heartbeat indicator. true когда прогресс не сдвинулся >10s.
  // Гарантирует юзеру что аппа не зависла — просто медленная сеть.
  const [slowUpload, setSlowUpload] = useState(false)
  const textRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  // W2 #6 — mountedRef pattern (BackButton cleanup audit). Гарантирует что
  // setState из long-running upload не сработает на размонтированном
  // компоненте если юзер уходит со страницы во время отправки.
  const mountedRef = useRef(true)
  useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false } }, [])
  // Tracking время последнего progress-tick для heartbeat-детекции.
  // Mutable ref — без re-render когда обновляем (только interval читает).
  const lastProgressAtRef = useRef<number>(0)

  // BackButton wire+cleanup. Cleanup отрабатывает даже при unmount во время
  // pending upload — BackButton.offClick очистит callback, чтобы хвост от
  // прошлого Support не повёл юзера обратно при следующем mount'е.
  useEffect(() => {
    WebApp.BackButton.show()
    const goBack = () => nav('/')
    WebApp.BackButton.onClick(goBack)
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(goBack) }
  }, [nav])

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files || [])
    e.target.value = ''  // allow re-picking the same file after remove
    if (!picked.length) return

    if (files.length + picked.length > MAX_FILES) {
      WebApp.showAlert(t('support_too_many_files' as never))
      return
    }
    const currentTotal = files.reduce((a, f) => a + f.file.size, 0)
    const incomingTotal = picked.reduce((a, f) => a + f.size, 0)
    if (currentTotal + incomingTotal > MAX_TOTAL) {
      WebApp.showAlert(t('support_total_too_large' as never))
      return
    }
    // Подсчёт уже-прикреплённых видео + проверка лимита заранее
    // (иначе юзер мог бы накидать 2 видео и получить отказ только при send).
    const currentVideos = files.filter(f => f.isVideo).length
    let incomingVideos = 0
    for (const f of picked) {
      const isVid = isVideoFile(f)
      if (isVid) {
        incomingVideos += 1
        if (f.size > MAX_VIDEO_SIZE) {
          WebApp.showAlert(t('support_video_too_large' as never))
          return
        }
      } else {
        if (f.size > MAX_PHOTO_SIZE) {
          WebApp.showAlert(t('support_file_too_large' as never))
          return
        }
        // type-based check + HEIC name fallback (iOS Safari often reports
        // empty type for HEIC files picked from Photos).
        const accepted = ACCEPT_TYPES.split(',').some(at =>
          f.type === at || (at.startsWith('image/heic') && /\.(heic|heif)$/i.test(f.name))
        )
        if (!accepted) {
          WebApp.showAlert(t('support_unsupported_type' as never))
          return
        }
      }
    }
    if (currentVideos + incomingVideos > MAX_VIDEOS) {
      WebApp.showAlert(t('support_too_many_videos' as never))
      return
    }

    const newAttachments: AttachedFile[] = picked.map(f => ({
      file: f,
      thumb: null,
      id: `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      isVideo: isVideoFile(f),
    }))
    // Thumbnail generation is async + opt-in for small files only — see
    // THUMB_LIMIT rationale. Видео thumbnail не генерируем (тяжело + редко
    // нужен); рендерим play-icon placeholder.
    for (const att of newAttachments) {
      if (!att.isVideo && att.file.size < THUMB_LIMIT && att.file.type.startsWith('image/')) {
        const reader = new FileReader()
        reader.onload = ev => {
          if (typeof ev.target?.result === 'string') {
            setFiles(prev => prev.map(p => p.id === att.id ? { ...p, thumb: ev.target!.result as string } : p))
          }
        }
        reader.readAsDataURL(att.file)
      }
    }
    setFiles(prev => [...prev, ...newAttachments])
  }

  const handleRemoveFile = (id: string) => {
    WebApp.HapticFeedback.selectionChanged()
    setFiles(prev => prev.filter(f => f.id !== id))
  }

  const handleSubmit = async () => {
    if (state === 'sending') return
    const trimmed = message.trim()
    if (trimmed.length < 10) {
      WebApp.showAlert(t('support_text_too_short' as never))
      return
    }
    WebApp.HapticFeedback.impactOccurred('light')
    setState('sending')
    setErrMsg('')
    setUploadProgress(null)
    setSlowUpload(false)
    // W2 #9 — timeout / heartbeat infra.
    //  • Hard 60s timeout — Promise.race с timeout-rejecter. Если upload
    //    висит 60+с (плохая сеть / большой видео+медленный нет-channel) —
    //    показываем юзеру alert и переключаем state в error. Реальный xhr
    //    мы отсюда aborts'нуть не можем (api/index.ts — W1's territory),
    //    но UI отписывается от результата через mountedRef-guards и timed-
    //    out flag.
    //  • Heartbeat 10s — interval, который смотрит на ref времени последнего
    //    progress-tick'а и поднимает slowUpload state, рендерящий «🐢 Медленное
    //    соединение…». Юзер видит что аппа не зависла, просто bytes медленно
    //    идут.
    let timedOut = false
    const TIMEOUT_MS = 60_000
    const HEARTBEAT_THRESHOLD_MS = 10_000
    lastProgressAtRef.current = Date.now()
    const heartbeatInterval = setInterval(() => {
      if (!mountedRef.current) return
      const sinceLastTick = Date.now() - lastProgressAtRef.current
      setSlowUpload(sinceLastTick > HEARTBEAT_THRESHOLD_MS)
    }, 1000)
    const timeoutPromise = new Promise<never>((_, reject) => {
      setTimeout(() => {
        timedOut = true
        reject(new Error('upload_timeout'))
      }, TIMEOUT_MS)
    })
    try {
      let ticket_id: number
      if (files.length === 0) {
        ({ ticket_id } = await createSupportTicket(category, trimmed))
      } else {
        // Multipart upload — fields named `photo_1`...`photo_N` so backend
        // can iterate by `field.name.startswith("photo")`.
        const fd = new FormData()
        fd.append('category', category)
        fd.append('message', trimmed)
        files.forEach((att, i) => fd.append(`photo_${i + 1}`, att.file))
        // Race upload vs timeout. Если upload победит — нормальный путь;
        // если timeout — catch ниже обработает `upload_timeout` ветку.
        const result = await Promise.race([
          uploadTicketWithPhotos(fd, pct => {
            if (!mountedRef.current || timedOut) return
            lastProgressAtRef.current = Date.now()
            setSlowUpload(false)
            setUploadProgress(pct)
          }),
          timeoutPromise,
        ])
        ticket_id = result.ticket_id
      }
      if (!mountedRef.current) return
      setTicketId(ticket_id)
      WebApp.HapticFeedback.notificationOccurred('success')
      setState('done')
    } catch (e) {
      if (!mountedRef.current) return
      const raw = e instanceof Error ? e.message : ''
      // W2 #9 — upload_timeout ветка. Алертим юзеру + сохраняем error-стейт
      // чтобы он мог решить ретраить (просто закроет alert и нажмёт send
      // ещё раз; форма + файлы сохранились).
      if (raw === 'upload_timeout') {
        WebApp.HapticFeedback.notificationOccurred('error')
        WebApp.showAlert(t('bot_err_upload_timeout' as never))
        setErrMsg(t('bot_err_upload_timeout' as never))
        setState('error')
        return
      }
      // Whitelist known backend error codes → localised strings. Backend
      // now also returns bilingual `message` in JSON which xhr propagates
      // as e.message — show as-is when it doesn't match a legacy code.
      const friendly =
        raw === 'rate_limited'    ? t('support_rate_limited' as never) :
        raw === 'auth_failed'     ? t('server_auth_failed' as never) :
        raw === 'validation_error'? t('server_validation_error' as never) :
        raw === 'session_expired' ? '' :  // handle401 already alerts + closes
        raw === 'network'         ? t('support_submit_error' as never) :
        raw && raw.length < 200   ? raw :  // bilingual server msg
        t('support_submit_error' as never)
      if (friendly) setErrMsg(friendly)
      setState('error')
    } finally {
      clearInterval(heartbeatInterval)
      if (mountedRef.current) {
        setUploadProgress(null)
        setSlowUpload(false)
      }
    }
  }

  // eSIM-категория тикета скрыта если SHOW_ESIM=false (build-time flag).
  // Иначе юзер пишет «не могу активировать eSIM» а у него нет eSIM-продукта.
  const SHOW_ESIM = import.meta.env.VITE_SHOW_ESIM !== 'false'

  const CATS_ALL = [
    { key: 'vpn'     as SupportCategory, label: t('support_cat_vpn'),  color: '#27ae60', icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round"/></svg> },
    { key: 'esim'    as SupportCategory, label: t('support_cat_esim'), color: '#2481cc', icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><rect x="5" y="2" width="14" height="20" rx="2" stroke="#fff" strokeWidth="2"/><path d="M9 8h6M9 12h6M9 16h4" stroke="#fff" strokeWidth="1.5" strokeLinecap="round"/></svg> },
    { key: 'payment' as SupportCategory, label: t('support_cat_pay'),  color: '#e67e22', icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><rect x="2" y="5" width="20" height="14" rx="2" stroke="#fff" strokeWidth="2"/><path d="M2 10h20" stroke="#fff" strokeWidth="2"/><path d="M6 15h4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/></svg> },
    { key: 'other'   as SupportCategory, label: t('support_cat_other'),color: '#8e44ad', icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg> },
  ]
  const CATS = SHOW_ESIM ? CATS_ALL : CATS_ALL.filter(c => c.key !== 'esim')

  if (state === 'done') {
    return (
      <div className="page">
        <div className="center">
          <div className="w-[72px] h-[72px] rounded-[22px] mb-1 bg-success/12 flex items-center justify-center text-[36px]">✅</div>
          <div className="font-extrabold text-[22px] text-[var(--tg-theme-text-color)]">{t('support_done')}</div>
          <p className="text-[var(--tg-theme-hint-color)] text-sm leading-relaxed max-w-[280px]">
            {t('support_ticket')} #{ticketId} {t('support_ticket_accepted')}.<br />{t('support_done_sub')}
          </p>
          <button className="btn w-full mb-2.5" onClick={() => { setMessage(''); setFiles([]); setState('form') }}>
            {t('support_write_more')}
          </button>
          <button className="btn w-full !bg-[var(--tg-theme-section-bg-color)] !text-[var(--tg-theme-text-color)]" onClick={() => nav('/')}>
            {t('support_home')}
          </button>
        </div>
      </div>
    )
  }

  const selectedCat = CATS.find(c => c.key === category) ?? CATS[0]

  return (
    <div className="page" style={{ gap: 12 }}>

      {/* FAQ */}
      <span className="section-title">{t('support_faq')}</span>
      <FaqGroup t={t} />

      {/* Тема обращения */}
      <span className="section-title">{t('support_form')}</span>
      <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
        {CATS.map((c, i) => (
          <button
            key={c.key}
            onClick={() => { setCategory(c.key); WebApp.HapticFeedback.selectionChanged() }}
            className={`w-full border-none bg-transparent py-[13px] px-4 cursor-pointer flex items-center gap-[14px] ${i < CATS.length - 1 ? 'border-b border-solid border-[var(--card-border)]' : ''}`}
          >
            <div className="w-9 h-9 rounded-[10px] shrink-0 flex items-center justify-center" style={{ background: c.color }}>
              {c.icon}
            </div>
            <span className="flex-1 text-[15px] font-medium text-[var(--tg-theme-text-color)] text-left">
              {c.label}
            </span>
            {category === c.key ? (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="10" fill={accent}/>
                <path d="M8 12l3 3 5-5" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            ) : (
              <div className="w-[18px] h-[18px] rounded-full border-2 border-[rgba(128,128,128,0.3)]" />
            )}
          </button>
        ))}
      </div>

      {/* Поле сообщения */}
      <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden py-1">
        <div className="py-[10px] px-4 pb-[6px] flex items-center gap-[10px]">
          <div className="w-7 h-7 rounded-lg shrink-0 flex items-center justify-center" style={{ background: selectedCat.color }}>
            {selectedCat.icon}
          </div>
          <span className="text-[13px] font-semibold text-[var(--tg-theme-text-color)]">{selectedCat.label}</span>
        </div>
        <textarea
          ref={textRef}
          value={message}
          onChange={e => setMessage(e.target.value)}
          placeholder={t('support_placeholder')}
          rows={5}
          maxLength={2000}
          aria-label={t('support_placeholder')}
          /* font-size:16px глобально (index.css) — иначе iOS зумит viewport при focus.
             text-sm убран, чтобы не переопределить.  leading-[1.4] — компактнее
             при 16px чтобы текстарея не разрослась. */
          className="w-full py-2 px-4 pb-1 border-none bg-transparent leading-[1.4] resize-none outline-none font-sans box-border"
        />
        <div className="px-4 pb-2 text-[10px] text-[var(--tg-theme-hint-color)] text-right">
          {message.length} / 2000
        </div>
      </div>

      {/* Screenshot / video attachments */}
      <input
        ref={fileInputRef}
        type="file"
        accept={ACCEPT_TYPES}
        multiple
        onChange={handleFileChange}
        style={{ display: 'none' }}
      />
      {files.length === 0 ? (
        // Empty state: одна большая dashed-кнопка-dropzone. Юзер сразу видит
        // что аппа поддерживает аттачи, не приходится высматривать text-link.
        <button
          type="button"
          disabled={state === 'sending'}
          onClick={() => { WebApp.HapticFeedback.selectionChanged(); fileInputRef.current?.click() }}
          className="w-full rounded-2xl border-2 border-dashed border-[var(--card-border)] bg-[var(--tg-theme-section-bg-color)]/40 py-5 px-4 flex flex-col items-center gap-1.5 cursor-pointer disabled:opacity-40 transition-colors active:bg-[var(--tg-theme-section-bg-color)]"
        >
          <div
            className="w-10 h-10 rounded-full flex items-center justify-center"
            style={{ background: `${accent}1A`, color: accent }}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.49"/>
            </svg>
          </div>
          <span className="text-[14px] font-semibold text-[var(--tg-theme-text-color)]">
            {t('support_attach_label' as never)}
          </span>
          <span className="text-[11px] text-[var(--tg-theme-hint-color)]">
            {t('support_attach_hint' as never)}
          </span>
        </button>
      ) : (
        // Filled state: card с header'ом «Прикреплено N/5» + grid тумбов
        // и «+»-tile в конце пока не дошли до лимита.
        <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
          <div className="py-[10px] px-4 flex items-center justify-between gap-3 border-b border-[var(--card-border)]">
            <span className="text-[13px] font-semibold text-[var(--tg-theme-text-color)]">
              {(t('support_attach_count' as never)).replace('{n}', String(files.length)).replace('{max}', String(MAX_FILES))}
            </span>
            <button
              type="button"
              onClick={() => { WebApp.HapticFeedback.selectionChanged(); setFiles([]) }}
              className="text-[12px] border-none bg-transparent cursor-pointer text-[var(--tg-theme-hint-color)] px-1 py-0.5"
            >
              {t('support_attach_clear' as never)}
            </button>
          </div>
          <div className="p-3 flex flex-wrap gap-2">
            {files.map(att => (
              <div key={att.id} className="relative w-[72px] h-[72px] rounded-xl overflow-hidden border border-[var(--card-border)] bg-[var(--tg-theme-bg-color)] flex items-center justify-center">
                {att.isVideo ? (
                  // Видео-плашка: тёмный фон, центр-плей, badge с размером
                  // в углу + угловая metka «VIDEO» чтобы юзер сразу отличал
                  // от тёмных скринов (например ночной режим Mini App'a).
                  <div className="relative w-full h-full flex items-center justify-center bg-gradient-to-br from-[#1c1c1e] to-[#3a3a3c] text-white">
                    <div className="w-9 h-9 rounded-full bg-white/15 backdrop-blur-sm flex items-center justify-center">
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M8 5v14l11-7z"/>
                      </svg>
                    </div>
                    <span className="absolute top-1 left-1 text-[9px] font-bold tracking-wider bg-black/40 px-1.5 py-0.5 rounded">VIDEO</span>
                    <span className="absolute bottom-1 right-1 text-[10px] font-medium bg-black/40 px-1.5 py-0.5 rounded">
                      {(att.file.size / 1024 / 1024).toFixed(1)}МБ
                    </span>
                  </div>
                ) : att.thumb ? (
                  <img src={att.thumb} alt="" className="w-full h-full object-cover" />
                ) : (
                  // Photo >2MB: photo-icon + размер.  Превью не генерируем
                  // (heavy base64 encode на mobile блокирует main thread).
                  <div className="flex flex-col items-center text-[var(--tg-theme-hint-color)] gap-0.5">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                      <rect x="3" y="3" width="18" height="18" rx="2" stroke="currentColor" strokeWidth="1.8"/>
                      <circle cx="8.5" cy="8.5" r="1.5" fill="currentColor"/>
                      <path d="M21 15l-5-5L5 21" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                    <span className="text-[10px] font-medium">{(att.file.size / 1024 / 1024).toFixed(1)}МБ</span>
                  </div>
                )}
                <button
                  type="button"
                  onClick={() => handleRemoveFile(att.id)}
                  aria-label={t('support_remove_file' as never)}
                  className="absolute top-1 right-1 w-[22px] h-[22px] rounded-full bg-black/65 text-white border-2 border-white/90 shadow flex items-center justify-center cursor-pointer p-0"
                >
                  <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
                    <path d="M2 2l8 8M10 2l-8 8"/>
                  </svg>
                </button>
              </div>
            ))}
            {files.length < MAX_FILES && (
              // «+»-tile в общем grid'е — даёт быстрое affordance докинуть ещё
              // файл не уезжая глазом с превью.
              <button
                type="button"
                disabled={state === 'sending'}
                onClick={() => { WebApp.HapticFeedback.selectionChanged(); fileInputRef.current?.click() }}
                className="w-[72px] h-[72px] rounded-xl border-2 border-dashed border-[var(--card-border)] bg-transparent flex items-center justify-center cursor-pointer disabled:opacity-40"
                style={{ color: accent }}
                aria-label={t('support_attach_btn' as never)}
              >
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d="M12 5v14M5 12h14"/>
                </svg>
              </button>
            )}
          </div>
        </div>
      )}

      {/* Upload progress */}
      {uploadProgress !== null && (
        <div className="px-1">
          <div className="text-[11px] text-[var(--tg-theme-hint-color)] mb-1 text-center">
            {t('support_upload_progress' as never).replace('{pct}', String(uploadProgress))}
          </div>
          <div className="h-1.5 rounded-full bg-[var(--card-border)] overflow-hidden">
            <div
              className="h-full transition-all duration-150"
              style={{ width: `${uploadProgress}%`, background: accent }}
            />
          </div>
          {/* W2 #9 — heartbeat indicator. Поднимается через 10s простоя
              progress'а. Юзер видит что аппа жива, просто канал медленный. */}
          {slowUpload && (
            <div className="text-[11px] text-warning mt-1.5 text-center font-medium">
              {t('bot_slow_connection' as never)}
            </div>
          )}
        </div>
      )}

      {/* Хинт когда юзер ждать ответа (раньше — нечего: тикет уходил «в пустоту») */}
      <div className="text-[11px] text-[var(--tg-theme-hint-color)] px-1 text-center">
        {t('support_reply_hint' as never)}
      </div>

      {state === 'error' && errMsg && (
        <p style={{ color: 'var(--tg-theme-destructive-text-color,#ff3b30)', textAlign: 'center', fontSize: 13, margin: 0 }}>
          {errMsg}
        </p>
      )}

      <button
        className="btn"
        disabled={message.trim().length < 10 || state === 'sending'}
        onClick={handleSubmit}
        style={{ width: '100%' }}
      >
        {state === 'sending' ? t('support_sending') : t('support_send')}
      </button>

      <AppFooter />

    </div>
  )
}

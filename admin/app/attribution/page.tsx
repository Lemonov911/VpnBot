'use client'
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import AdminNav from '../_components/AdminNav'

type AttributionRow = {
  source: string
  starts: number
  trial_users: number
  paying_users: number
  revenue_stars: number
  revenue_rub: number
}

type RecentUtm = { code: string; paying: number; starts: number }

type Resp = {
  days: number | null
  rows: AttributionRow[]
  recent_utm: RecentUtm[]
  bot_username: string
}

const STARS_TO_RUB = 1.4

const PERIOD_OPTS: Array<{ label: string; days: number }> = [
  { label: '7 дней',  days: 7   },
  { label: '30 дней', days: 30  },
  { label: '90 дней', days: 90  },
  { label: 'Всё',     days: 0   },  // 0 → null на бэке (вся история)
]

// Заголовок источника в человеческом виде. namespace:payload → подпись + тип.
function formatSource(source: string): { label: string; chip: string; tone: 'utm' | 'ref' | 'direct' | 'deeplink' | 'other' } {
  if (source === 'direct')      return { label: 'Прямой заход', chip: 'direct',   tone: 'direct'   }
  if (source === 'deeplink')    return { label: 'Deep link (продукт)', chip: 'deeplink', tone: 'deeplink' }
  if (source.startsWith('utm:'))      return { label: source.slice(4),      chip: 'utm',      tone: 'utm'      }
  if (source.startsWith('referral:')) return { label: `Реф от ${source.slice(9)}`, chip: 'ref', tone: 'ref'    }
  if (source.startsWith('other:'))    return { label: source.slice(6),      chip: 'other',    tone: 'other'    }
  return { label: source, chip: '?', tone: 'other' }
}

function totalRub(r: AttributionRow) {
  return Math.round(r.revenue_stars * STARS_TO_RUB) + r.revenue_rub
}

export default function AttributionPage() {
  const router = useRouter()
  const [data, setData]       = useState<Resp | null>(null)
  const [period, setPeriod]   = useState<number>(30)
  const [loading, setLoading] = useState(true)

  // Генератор
  const [campaign, setCampaign] = useState('')
  const [copied, setCopied]     = useState(false)
  const [showLegend, setShowLegend] = useState(false)

  async function load(days: number) {
    setLoading(true)
    const r = await fetch(`/admin/api/attribution?days=${days}`)
    if (r.status === 401) { router.push('/login'); return }
    setData(await r.json())
    setLoading(false)
  }

  useEffect(() => { load(period) }, [period])

  // Нормализуем имя кампании на лету: lowercase, только [a-z0-9_-], max 60
  function normalize(raw: string): string {
    return raw.toLowerCase().replace(/[^a-z0-9_\-]/g, '').slice(0, 60)
  }

  const cleanCampaign = useMemo(() => normalize(campaign), [campaign])
  const utmUrl = useMemo(() => {
    const bot = data?.bot_username || 'YOUR_BOT'
    return cleanCampaign
      ? `https://t.me/${bot}?start=utm_${cleanCampaign}`
      : ''
  }, [data?.bot_username, cleanCampaign])

  async function copyUrl(url: string) {
    if (!url) return
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // На случай если clipboard API заблокирован (HTTP, не HTTPS, и т.п.) —
      // показываем prompt с готовой строкой, юзер скопирует вручную.
      window.prompt('Скопируй вручную:', url)
    }
  }

  const rows  = data?.rows ?? []
  const total = rows.reduce((a, r) => a + r.starts, 0)
  const paying = rows.reduce((a, r) => a + r.paying_users, 0)
  const revRub = rows.reduce((a, r) => a + totalRub(r), 0)

  return (
    <div className="min-h-screen p-6 max-w-6xl mx-auto space-y-8">
      <AdminNav />

      <div>
        <div className="text-xl font-extrabold tracking-tight">Атрибуция трафика</div>
        <div className="text-xs text-neutral-500 mt-0.5">
          Откуда приходят юзеры → сколько становятся платящими. First-touch.
        </div>
      </div>

      {/* Period switcher */}
      <div className="flex gap-2">
        {PERIOD_OPTS.map(opt => (
          <button
            key={opt.days}
            onClick={() => setPeriod(opt.days)}
            className={`text-xs px-3 py-1.5 rounded-lg transition-colors ${
              period === opt.days
                ? 'bg-[#2481cc] text-white'
                : 'bg-neutral-900 border border-neutral-800 text-neutral-400 hover:text-white'
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {/* Totals */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5">
          <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-1">Стартов</div>
          <div className="text-2xl font-bold text-white">{total}</div>
        </div>
        <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5">
          <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-1">Платящих</div>
          <div className="text-2xl font-bold text-emerald-400">{paying}</div>
          <div className="text-[10px] text-neutral-500 mt-1">
            {total > 0 ? `${Math.round((paying / total) * 100)}% конверсия` : '—'}
          </div>
        </div>
        <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5">
          <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-1">Выручка с когорты</div>
          <div className="text-2xl font-bold text-emerald-400">≈ {revRub.toLocaleString('ru')} ₽</div>
          <div className="text-[10px] text-neutral-500 mt-1">за всё время этих юзеров</div>
        </div>
      </div>

      {/* Generator */}
      <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 space-y-4">
        <div className="font-semibold text-sm">🔗 Сгенерировать ссылку для рекламы</div>

        <div className="space-y-2">
          <label className="text-xs text-neutral-500 block">
            Имя кампании (только a-z, 0-9, _ -):
          </label>
          <input
            value={campaign}
            onChange={e => setCampaign(e.target.value)}
            placeholder="tg_pythonist_jul"
            className="bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2 text-sm w-full font-mono focus:outline-none focus:border-[#2481cc] placeholder:text-neutral-600"
          />
          {campaign && campaign !== cleanCampaign && (
            <div className="text-[10px] text-yellow-400">
              нормализовано: <span className="font-mono">{cleanCampaign}</span>
            </div>
          )}
        </div>

        {cleanCampaign && (
          <div className="space-y-2">
            <div className="text-xs text-neutral-500">Готовая ссылка:</div>
            <div className="bg-neutral-950 border border-neutral-800 rounded-lg px-3 py-2 font-mono text-xs text-emerald-400 break-all">
              {utmUrl}
            </div>
            <button
              onClick={() => copyUrl(utmUrl)}
              className="text-xs bg-[#2481cc] hover:bg-[#1a6db3] px-3 py-1.5 rounded-lg transition-colors"
            >
              {copied ? '✓ Скопировано' : '📋 Скопировать'}
            </button>
            {!data?.bot_username && (
              <div className="text-[10px] text-yellow-400">
                ⚠ BOT_USERNAME не задан в .env админки — в ссылке стоит YOUR_BOT. Поправь на сервере перед использованием.
              </div>
            )}
          </div>
        )}

        {/* Legend toggle */}
        <button
          onClick={() => setShowLegend(s => !s)}
          className="text-xs text-neutral-500 hover:text-neutral-300 transition-colors"
        >
          {showLegend ? '▾' : '▸'} Как правильно называть кампании
        </button>

        {showLegend && (
          <div className="bg-neutral-950 border border-neutral-800 rounded-lg p-4 text-xs space-y-3 text-neutral-300">
            <div>
              <span className="text-neutral-500">Формат:</span>
              {' '}<span className="font-mono">&lt;тип&gt;_&lt;имя&gt;_&lt;месяц&gt;</span>
            </div>

            <div>
              <div className="text-neutral-500 mb-1.5">Префиксы по каналам:</div>
              <div className="grid grid-cols-2 gap-x-6 gap-y-1 font-mono">
                <div><span className="text-emerald-400">tg</span>     <span className="text-neutral-500">— закуп в TG-канале</span></div>
                <div><span className="text-emerald-400">tgads</span>  <span className="text-neutral-500">— TG Ads</span></div>
                <div><span className="text-emerald-400">shorts</span> <span className="text-neutral-500">— YouTube Shorts свой</span></div>
                <div><span className="text-emerald-400">reels</span>  <span className="text-neutral-500">— TG/Insta Reels свой</span></div>
                <div><span className="text-emerald-400">yt</span>     <span className="text-neutral-500">— YouTube блогер</span></div>
                <div><span className="text-emerald-400">pikabu</span> <span className="text-neutral-500">— Pikabu пост</span></div>
                <div><span className="text-emerald-400">4pda</span>   <span className="text-neutral-500">— 4PDA тема</span></div>
                <div><span className="text-emerald-400">vk</span>     <span className="text-neutral-500">— VK реклама</span></div>
                <div><span className="text-emerald-400">dzen</span>   <span className="text-neutral-500">— Yandex.Дзен</span></div>
                <div><span className="text-emerald-400">other</span>  <span className="text-neutral-500">— нестандартный</span></div>
              </div>
            </div>

            <div>
              <div className="text-neutral-500 mb-1.5">Примеры правильных имён:</div>
              <div className="font-mono text-[11px] space-y-0.5">
                <div>tg_pythonist_jul         <span className="text-neutral-600">— TG канал, июль</span></div>
                <div>shorts_001               <span className="text-neutral-600">— мой Shorts #1</span></div>
                <div>shorts_002</div>
                <div>yt_techguy_blok          <span className="text-neutral-600">— блогер про блокировки</span></div>
                <div>tgads_iosvless_a         <span className="text-neutral-600">— TG Ads, креатив A</span></div>
                <div>pikabu_yandexnotwork     <span className="text-neutral-600">— пост про Яндекс</span></div>
              </div>
            </div>

            <div className="text-neutral-400 border-t border-neutral-800 pt-2.5">
              <span className="text-yellow-400">Главное:</span> чтоб через полгода ты сам понял что это за источник.
              «kanal_1» — плохо, через 3 месяца забудешь. «tg_pythonist_jul» — ОК.
            </div>
          </div>
        )}
      </div>

      {/* Recent UTM codes (re-use) */}
      {data?.recent_utm && data.recent_utm.length > 0 && (
        <div className="bg-neutral-900 border border-neutral-800 rounded-2xl overflow-hidden">
          <div className="px-5 py-3 border-b border-neutral-800">
            <div className="font-semibold text-sm">Недавние кампании</div>
            <div className="text-[10px] text-neutral-500 mt-0.5">Нажми чтоб скопировать ссылку</div>
          </div>
          <div className="divide-y divide-neutral-800">
            {data.recent_utm.map(u => {
              const url = `https://t.me/${data.bot_username || 'YOUR_BOT'}?start=utm_${u.code}`
              return (
                <button
                  key={u.code}
                  onClick={() => copyUrl(url)}
                  className="w-full px-5 py-2.5 flex items-center gap-3 hover:bg-neutral-800/40 transition-colors text-left"
                >
                  <span className="font-mono text-xs flex-1 truncate">{u.code}</span>
                  <span className="text-[10px] text-neutral-500 shrink-0">{u.starts} стартов</span>
                  <span className="text-xs text-emerald-400 shrink-0 w-20 text-right">{u.paying} платных</span>
                  <span className="text-[10px] text-neutral-600 shrink-0">📋</span>
                </button>
              )
            })}
          </div>
        </div>
      )}

      {/* Sources table */}
      <div className="bg-neutral-900 border border-neutral-800 rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-neutral-800">
          <div className="font-semibold text-sm">Источники</div>
          <div className="text-[10px] text-neutral-500 mt-0.5">Сортировка: по платящим, затем по стартам</div>
        </div>
        {loading ? (
          <div className="px-5 py-8 text-center text-sm text-neutral-600">Загрузка...</div>
        ) : rows.length === 0 ? (
          <div className="px-5 py-8 text-center text-sm text-neutral-600">
            Нет данных за этот период. Создай ссылку выше и пусти в рекламу — здесь появятся метрики.
          </div>
        ) : (
          <div className="divide-y divide-neutral-800 text-sm">
            <div className="px-5 py-2 grid grid-cols-[1fr_60px_60px_60px_90px_60px] gap-3 text-[10px] uppercase tracking-wider text-neutral-500">
              <div>Источник</div>
              <div className="text-right">Старты</div>
              <div className="text-right">Триал</div>
              <div className="text-right">Платных</div>
              <div className="text-right">Выручка</div>
              <div className="text-right">Конв %</div>
            </div>
            {rows.map(r => {
              const meta = formatSource(r.source)
              const rub = totalRub(r)
              const conv = r.starts > 0 ? Math.round((r.paying_users / r.starts) * 100) : 0
              return (
                <div key={r.source} className="px-5 py-3 grid grid-cols-[1fr_60px_60px_60px_90px_60px] gap-3 items-center">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
                        meta.tone === 'utm' ? 'bg-sky-900/40 text-sky-300' :
                        meta.tone === 'ref' ? 'bg-purple-900/40 text-purple-300' :
                        meta.tone === 'direct' ? 'bg-neutral-800 text-neutral-400' :
                        meta.tone === 'deeplink' ? 'bg-emerald-900/40 text-emerald-300' :
                        'bg-yellow-900/40 text-yellow-300'
                      }`}>{meta.chip}</span>
                      <span className="font-medium truncate">{meta.label}</span>
                    </div>
                  </div>
                  <div className="text-right text-neutral-300">{r.starts}</div>
                  <div className="text-right text-neutral-500">{r.trial_users}</div>
                  <div className="text-right text-emerald-400 font-semibold">{r.paying_users}</div>
                  <div className="text-right text-emerald-400">
                    {rub > 0 ? `≈${rub.toLocaleString('ru')} ₽` : '—'}
                  </div>
                  <div className="text-right text-neutral-400">{conv}%</div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

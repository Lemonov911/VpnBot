'use client'
import { useEffect, useRef, Suspense } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'

const BOT_USERNAME = process.env.NEXT_PUBLIC_BOT_USERNAME ?? 'MaxVpnBot'

function LoginForm() {
  const router = useRouter()
  const params = useSearchParams()
  const ref    = useRef<HTMLDivElement>(null)
  const error  = params.get('error')

  useEffect(() => {
    if (!ref.current) return
    const script = document.createElement('script')
    script.src = 'https://telegram.org/js/telegram-widget.js?22'
    script.setAttribute('data-telegram-login', BOT_USERNAME)
    script.setAttribute('data-size', 'large')
    script.setAttribute('data-radius', '10')
    script.setAttribute('data-onauth', 'onTelegramAuth(user)')
    script.setAttribute('data-request-access', 'write')
    script.async = true
    ref.current.appendChild(script)

    ;(window as any).onTelegramAuth = async (user: Record<string, string>) => {
      const res = await fetch('/api/auth', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(user),
      })
      if (res.ok) router.replace('/')
      else {
        const d = await res.json()
        alert(d.error ?? 'Ошибка авторизации')
      }
    }
  }, [router])

  return (
    <div className="min-h-screen bg-[#0f0f0f] flex items-center justify-center">
      <div className="text-center space-y-6 w-80">
        <div>
          <div className="text-2xl font-extrabold text-white tracking-tight">MAX VPN &amp; eSIM</div>
          <div className="text-sm text-neutral-500 mt-1">Панель администратора</div>
        </div>

        {error === 'forbidden' && (
          <div className="text-sm text-red-400 bg-red-400/10 border border-red-400/20 rounded-lg px-4 py-2">
            Нет доступа. Обратитесь к владельцу.
          </div>
        )}

        <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-8 space-y-4">
          <div className="text-neutral-400 text-sm">Войдите через Telegram</div>
          <div ref={ref} className="flex justify-center" />
        </div>
      </div>
    </div>
  )
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  )
}

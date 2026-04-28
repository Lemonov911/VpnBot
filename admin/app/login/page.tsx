'use client'
import { Suspense } from 'react'
import { useSearchParams } from 'next/navigation'

function LoginForm() {
  const params = useSearchParams()
  const error  = params.get('error')

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
        {error === 'expired' && (
          <div className="text-sm text-yellow-400 bg-yellow-400/10 border border-yellow-400/20 rounded-lg px-4 py-2">
            Ссылка устарела. Запросите новую у бота.
          </div>
        )}

        <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-8 space-y-5">
          <div className="text-3xl">🤖</div>
          <div className="space-y-2">
            <div className="text-white font-semibold">Войти через бота</div>
            <div className="text-neutral-400 text-sm leading-relaxed">
              Напишите <span className="text-white font-mono bg-neutral-800 px-1.5 py-0.5 rounded">/admin</span> боту{' '}
              <a
                href="https://t.me/MaxVpnBot"
                target="_blank"
                rel="noreferrer"
                className="text-[#2481cc] hover:underline"
              >
                @MaxVpnBot
              </a>{' '}
              — он пришлёт ссылку для входа
            </div>
          </div>
          <a
            href="https://t.me/MaxVpnBot?start=admin"
            target="_blank"
            rel="noreferrer"
            className="block w-full py-3 rounded-xl bg-[#2481cc] hover:bg-[#1a6db3] transition-colors text-white font-semibold text-sm"
          >
            Открыть @MaxVpnBot
          </a>
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

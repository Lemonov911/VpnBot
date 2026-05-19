import { requireSession } from '@/lib/auth'
import { redirect } from 'next/navigation'
import AdminNav from '../_components/AdminNav'
import GrantForm from './GrantForm'

export default async function GrantPage() {
  const session = await requireSession()
  if (!session) redirect('/login')

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <div className="max-w-5xl mx-auto px-6 py-6">
        <AdminNav username={session.username} />
        <div className="mt-6">
          <h1 className="text-2xl font-bold">🎁 Выдать бесплатную подписку</h1>
          <p className="text-sm text-neutral-500 mt-1">
            Подписка выдаётся по telegram_id юзера. Если юзер ещё не запускал бота —
            user-row создаётся, при первом /start он увидит подписку.
            Все выдачи логируются (granted_by_admin_id + audit_log).
          </p>
          <GrantForm adminId={session.userId} />
        </div>
      </div>
    </div>
  )
}

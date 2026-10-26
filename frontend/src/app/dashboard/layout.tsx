'use client'

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'

import { AppHeader } from '@/components/AppHeader'
import { StoreBar } from '@/components/StoreSwitcher'
import { useAuth } from '@/contexts/AuthContext'
import { StoreProvider } from '@/contexts/StoreContext'

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { user, loading, logout } = useAuth()
  const router = useRouter()
  const pathname = usePathname()
  const [leaving, setLeaving] = useState(false)

  // Cookie есть, но сессию восстановить не удалось (токен отозван/истёк) — на вход
  useEffect(() => {
    if (!loading && !user && !leaving) router.replace(`/login?next=${encodeURIComponent(pathname)}`)
  }, [loading, user, leaving, router, pathname])

  async function onLogout() {
    setLeaving(true)
    await logout()
    router.replace('/login')
  }

  if (loading || !user) {
    return <main className="flex min-h-screen items-center justify-center text-muted">Загрузка…</main>
  }

  return (
    <StoreProvider>
      <div className="min-h-screen">
        <AppHeader onLogout={onLogout} leaving={leaving} />
        <StoreBar />
        {user.deletion_due_at && (
          // удаление аккаунта запрошено (шаг 10.26) — до срока напоминаем на каждой странице, отменить — в профиле
          <div className="border-b border-bad/40 bg-bad/10 px-4 py-2 text-sm md:px-6" data-testid="deletion-banner">
            <span className="text-bad">Аккаунт будет удалён {new Date(user.deletion_due_at).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow' })}</span>{' '}
            вместе с данными всех магазинов.{' '}
            <Link href="/dashboard/settings/account#delete" className="text-accent hover:underline">
              Отменить →
            </Link>
          </div>
        )}
        <div className="p-4 md:p-6">{children}</div>
      </div>
    </StoreProvider>
  )
}

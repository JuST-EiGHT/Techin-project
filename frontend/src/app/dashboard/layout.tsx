'use client'

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
        <div className="p-4 md:p-6">{children}</div>
      </div>
    </StoreProvider>
  )
}

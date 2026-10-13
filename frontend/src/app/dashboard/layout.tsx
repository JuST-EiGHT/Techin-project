'use client'

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'

import { SyncStatus } from '@/components/SyncStatus'
import { useAuth } from '@/contexts/AuthContext'

const NAV = [
  { href: '/dashboard', label: 'Главная' },
  { href: '/dashboard/reports', label: 'Отчёты' },
  { href: '/dashboard/warehouses', label: 'Склады' },
  { href: '/dashboard/promotions', label: 'Акции' },
  { href: '/dashboard/settings/api-keys', label: 'API-ключи' },
]

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
    return <main className="min-h-screen flex items-center justify-center text-slate-400">Загрузка…</main>
  }

  return (
    <div className="min-h-screen">
      <header className="flex items-center gap-6 border-b border-line px-6 py-4">
        <Link href="/dashboard" className="font-semibold">
          Ozon Analytics
        </Link>
        <nav className="flex gap-4 text-sm">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={
                pathname === item.href || (item.href !== '/dashboard' && pathname.startsWith(`${item.href}/`))
                  ? 'text-accent'
                  : 'text-slate-400 hover:text-slate-200'
              }
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="ml-auto">
          <SyncStatus />
        </div>
        <button
          onClick={onLogout}
          disabled={leaving}
          className="rounded-lg border border-line px-4 py-1.5 text-sm hover:border-bad hover:text-bad disabled:opacity-60"
        >
          Выйти
        </button>
      </header>
      <div className="p-6">{children}</div>
    </div>
  )
}

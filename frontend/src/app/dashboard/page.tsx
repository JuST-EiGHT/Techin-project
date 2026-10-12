'use client'

import { useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'

import { useAuth } from '@/contexts/AuthContext'

export default function DashboardPage() {
  const { user, loading, logout } = useAuth()
  const router = useRouter()
  const [leaving, setLeaving] = useState(false)

  // Cookie есть, но сессию восстановить не удалось (токен отозван/истёк) — на вход
  useEffect(() => {
    if (!loading && !user && !leaving) router.replace('/login?next=/dashboard')
  }, [loading, user, leaving, router])

  async function onLogout() {
    setLeaving(true)
    await logout()
    router.replace('/login')
  }

  if (loading || !user) {
    return <main className="min-h-screen flex items-center justify-center text-slate-400">Загрузка…</main>
  }

  return (
    <main className="min-h-screen">
      <header className="flex items-center justify-between border-b border-line px-6 py-4">
        <span className="font-semibold">Ozon Analytics</span>
        <button
          onClick={onLogout}
          disabled={leaving}
          className="rounded-lg border border-line px-4 py-1.5 text-sm hover:border-bad hover:text-bad disabled:opacity-60"
        >
          Выйти
        </button>
      </header>
      <section className="p-6">
        <div className="bg-card border border-line rounded-xl p-6">
          <h1 className="text-2xl font-semibold">Добро пожаловать, {user.full_name || user.email}</h1>
        </div>
      </section>
    </main>
  )
}

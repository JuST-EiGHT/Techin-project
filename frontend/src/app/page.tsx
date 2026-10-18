'use client'

import Link from 'next/link'

import { useRegistrationMode } from '@/lib/registration'

export default function Home() {
  const mode = useRegistrationMode()
  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-6">
      <h1 className="text-3xl font-bold">Ozon Analytics</h1>
      <div className="flex gap-3">
        <Link href="/login" className="rounded-lg bg-accent px-5 py-2 font-medium text-white hover:opacity-90">
          Войти
        </Link>
        {(mode === 'open' || mode === 'invite') && (
          <Link href="/register" className="rounded-lg border border-line px-5 py-2 hover:border-accent">
            {mode === 'invite' ? 'Регистрация по приглашению' : 'Регистрация'}
          </Link>
        )}
      </div>
    </main>
  )
}

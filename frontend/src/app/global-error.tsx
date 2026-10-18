'use client'

import './globals.css'

import { useEffect } from 'react'

import { reportError } from '@/lib/sentry'

// Ошибка в самом макете сайта (app/error.tsx её не ловит): своя страница целиком, тёмная тема по умолчанию
export default function GlobalError({ error }: { error: Error & { digest?: string } }) {
  useEffect(() => reportError(error), [error])
  return (
    <html lang="ru" data-theme="dark">
      <body>
        <main className="min-h-screen flex items-center justify-center px-4">
          <div className="max-w-md rounded-xl border border-line bg-card p-6 text-center">
            <h1 className="mb-2 text-lg font-semibold">Сайт не загрузился</h1>
            <p className="mb-5 text-sm text-muted">Произошла ошибка. Мы уже знаем о ней. Обновите страницу через минуту.</p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="rounded-lg bg-accent px-5 py-2 font-medium text-white hover:opacity-90"
            >
              Обновить страницу
            </button>
          </div>
        </main>
      </body>
    </html>
  )
}

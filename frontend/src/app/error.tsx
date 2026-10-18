'use client'

import { useEffect } from 'react'

import { reportError } from '@/lib/sentry'

// Ошибка при отрисовке страницы: понятное сообщение вместо пустого экрана, сама ошибка — в Sentry
export default function PageError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => reportError(error), [error])
  return (
    <main className="min-h-[60vh] flex items-center justify-center px-4">
      <div className="max-w-md rounded-xl border border-line bg-card p-6 text-center">
        <h1 className="mb-2 text-lg font-semibold">Что-то пошло не так</h1>
        <p className="mb-5 text-sm text-muted">
          Страница не открылась из-за ошибки. Мы уже знаем о ней. Попробуйте ещё раз — если не поможет, обновите страницу.
        </p>
        <button type="button" onClick={reset} className="rounded-lg bg-accent px-5 py-2 font-medium text-white hover:opacity-90">
          Попробовать ещё раз
        </button>
      </div>
    </main>
  )
}

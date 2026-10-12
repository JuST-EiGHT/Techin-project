'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useCallback, useEffect, useState } from 'react'

import { api, errorMessage } from '@/lib/api'

type SyncRun = {
  id: string
  status: 'queued' | 'running' | 'success' | 'failed'
  started_at: string
  finished_at: string | null
  stats: Record<string, number> | null
  error: string | null
}

type Status = {
  has_active_key: boolean
  products: { last_run: SyncRun | null; last_success_at: string | null }
}

const POLL_MS = 3000 // пока синхронизация идёт
const TICK_MS = 30000 // обновить «N минут назад»

function plural(n: number, one: string, few: string, many: string) {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few
  return many
}

export function timeAgo(iso: string, now = Date.now()): string {
  const min = Math.floor((now - new Date(iso).getTime()) / 60000)
  if (min < 1) return 'только что'
  if (min < 60) return `${min} ${plural(min, 'минуту', 'минуты', 'минут')} назад`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h} ${plural(h, 'час', 'часа', 'часов')} назад`
  return new Date(iso).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' })
}

// Событие «ключи Ozon изменились» — шлёт страница API-ключей, шапка перечитывает статус
export const CREDENTIALS_CHANGED = 'ozon-credentials-changed'
export const notifyCredentialsChanged = () => window.dispatchEvent(new Event(CREDENTIALS_CHANGED))

const isActive = (s: Status | null) => ['queued', 'running'].includes(s?.products.last_run?.status ?? '')

export function SyncStatus() {
  const pathname = usePathname()
  const [status, setStatus] = useState<Status | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [, setTick] = useState(0)

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<Status>('/sync/status')
      setStatus(data)
      const run = data.products.last_run
      setError(run?.status === 'failed' ? run.error : null)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  // при переходах по кабинету и после изменения ключей
  useEffect(() => {
    load()
  }, [load, pathname])

  useEffect(() => {
    window.addEventListener(CREDENTIALS_CHANGED, load)
    return () => window.removeEventListener(CREDENTIALS_CHANGED, load)
  }, [load])

  // пока идёт синхронизация — опрашиваем статус
  const active = isActive(status)
  useEffect(() => {
    if (!active) return
    const t = setInterval(load, POLL_MS)
    return () => clearInterval(t)
  }, [active, load])

  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), TICK_MS)
    return () => clearInterval(t)
  }, [])

  async function syncNow() {
    setStarting(true)
    setError(null)
    try {
      await api.post('/sync/products')
    } catch (err) {
      // 409 — уже идёт: просто покажем прогресс
      const conflict = (err as { response?: { status?: number } }).response?.status === 409
      if (!conflict) setError(errorMessage(err))
    } finally {
      setStarting(false)
      await load()
    }
  }

  if (!status) return null

  if (!status.has_active_key) {
    return (
      <Link href="/dashboard/settings/api-keys" className="text-sm text-warn hover:underline">
        Подключите API-ключ Ozon
      </Link>
    )
  }

  const lastSuccess = status.products.last_success_at
  const busy = active || starting

  return (
    <div className="flex items-center gap-3 text-sm" data-testid="sync-status">
      {error && (
        <span className="max-w-xs truncate text-bad" title={error}>
          Ошибка: {error}
        </span>
      )}
      <span className="text-slate-400">
        {busy ? 'Обновляется…' : lastSuccess ? `Обновлено ${timeAgo(lastSuccess)}` : 'Данные ещё не загружались'}
      </span>
      <button
        onClick={syncNow}
        disabled={busy}
        className="rounded-lg border border-line px-3 py-1.5 hover:border-accent hover:text-accent disabled:opacity-60"
      >
        Обновить сейчас
      </button>
    </div>
  )
}

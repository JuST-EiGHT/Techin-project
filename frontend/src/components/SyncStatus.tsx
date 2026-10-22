'use client'
// Статус синхронизации в шапке (шаг 1.2). С шага 10.1 разделов в шапке больше: до 1536 px — компактно (кнопка-иконка,
// время и ошибка — в подсказке), шире — с текстом. Новая кнопка «Синхронизация» со статусом каждой загрузки — шаг 10.2.
import { RefreshCw } from 'lucide-react'
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
      <Link href="/dashboard/settings/integrations" className="whitespace-nowrap text-sm text-warn hover:underline" data-testid="sync-no-key">
        <span className="2xl:hidden">Нужен ключ Ozon</span>
        <span className="hidden 2xl:inline">Подключите API-ключ Ozon</span>
      </Link>
    )
  }

  const lastSuccess = status.products.last_success_at
  const busy = active || starting
  const state = busy ? 'Обновляется…' : lastSuccess ? `Обновлено ${timeAgo(lastSuccess)}` : 'Данные ещё не загружались'

  return (
    <div className="flex items-center gap-3 text-sm" data-testid="sync-status">
      {error && (
        <span className="hidden max-w-xs truncate text-bad 2xl:inline" title={error}>
          Ошибка: {error}
        </span>
      )}
      <span className="hidden text-muted 2xl:inline">{state}</span>
      <button
        onClick={syncNow}
        disabled={busy}
        title={`${state}${error ? `. Ошибка: ${error}` : ''}. Обновить данные из Ozon сейчас`}
        aria-label="Обновить сейчас"
        className={`flex items-center gap-1.5 rounded-lg border px-2 py-1.5 hover:border-accent hover:text-accent disabled:opacity-60 2xl:px-3 ${
          error ? 'border-bad text-bad 2xl:border-line 2xl:text-fg' : 'border-line'
        }`}
      >
        <RefreshCw className={`h-4 w-4 2xl:hidden ${busy ? 'animate-spin' : ''}`} aria-hidden />
        <span className="hidden 2xl:inline">Обновить сейчас</span>
      </button>
    </div>
  )
}

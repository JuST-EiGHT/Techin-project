// Синхронизация с Ozon и банками (шаги 1.2, 10.2): типы ответа /sync/status, «N минут назад» и общий статус —
// один опрос на все компоненты (кнопка «Синхронизация» в шапке, «Обновлено …» в разделах), как счётчик уведомлений.
import { useEffect, useState } from 'react'

import { useAuth } from '@/contexts/AuthContext'
import { api } from '@/lib/api'

export type SyncRun = {
  id: string
  kind: string
  status: 'queued' | 'running' | 'success' | 'failed'
  started_at: string
  finished_at: string | null
  stats: Record<string, unknown> | null
  error: string | null
}
export type KindStatus = { last_run: SyncRun | null; last_success_at: string | null }

/** Состояние вида данных и общее: свежие, загружаются, ошибка, давно не обновлялись, ещё не загружались, нечего загружать. */
export type SyncState = 'fresh' | 'running' | 'error' | 'stale' | 'never' | 'no_source'

export type SyncKind = {
  kind: string
  title: string
  state: Exclude<SyncState, 'no_source'>
  running: boolean
  last_success_at: string | null
  schedule: string
  problem: string | null
  hint: string | null
  action: { label: string; href: string } | null
  error: string | null
  recent_until: string | null
  can_refresh: boolean
}

export type SyncStatus = {
  has_active_key: boolean
  has_bank: boolean
  products: KindStatus
  stocks: KindStatus
  finance: KindStatus
  promotions: KindStatus
  balance: KindStatus
  bank: KindStatus | null
  postings: KindStatus | null
  state: SyncState
  oldest_kind: string | null
  oldest_success_at: string | null
  kinds: SyncKind[]
}

export type SyncAllItem = { kind: string; title: string; result: 'queued' | 'running' | 'recent' | 'unavailable'; recent_until: string | null }
export type SyncAllResponse = { items: SyncAllItem[]; message: string }

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

/** «через 12 мин» до времени iso (не меньше минуты). */
export function minutesLeft(iso: string, now = Date.now()): string {
  return `${Math.max(1, Math.ceil((new Date(iso).getTime() - now) / 60000))} мин`
}

// Событие «ключи Ozon изменились» — шлёт вкладка «Интеграции → Ozon», статус синхронизации перечитывается
export const CREDENTIALS_CHANGED = 'ozon-credentials-changed'
export const notifyCredentialsChanged = () => window.dispatchEvent(new Event(CREDENTIALS_CHANGED))

// ── общий статус: один опрос /sync/status на все компоненты ──
const IDLE_MS = 60_000 // ничего не загружается — раз в минуту (загрузки по расписанию тоже видны)
const BUSY_MS = 3_000 // идёт загрузка — чаще
let status: SyncStatus | null = null
let owner: string | null = null
let timer: number | undefined
const listeners = new Set<(s: SyncStatus | null) => void>()

const busy = (s: SyncStatus | null) => s?.state === 'running'

function schedule() {
  window.clearTimeout(timer)
  if (listeners.size === 0) return
  timer = window.setTimeout(() => {
    if (document.visibilityState === 'visible') refreshSyncStatus()
    else schedule()
  }, busy(status) ? BUSY_MS : IDLE_MS)
}

/** Перечитать статус сейчас (после «Обновить», смены ключа, перехода по разделам). */
export async function refreshSyncStatus(): Promise<void> {
  try {
    const { data } = await api.get<SyncStatus>('/sync/status', { skipAuthRedirect: true })
    status = data
    listeners.forEach((l) => l(data))
  } catch {
    // нет сети или сессия кончилась — оставляем прежний статус, следующий опрос попробует снова
  }
  schedule()
}

function onVisible() {
  if (document.visibilityState === 'visible') refreshSyncStatus()
}

/** Статус синхронизации текущего магазина. null — ещё не загрузился. */
export function useSyncStatus(): SyncStatus | null {
  const { user } = useAuth()
  const [value, setValue] = useState<SyncStatus | null>(status)
  useEffect(() => {
    if (!user) return
    if (owner !== user.id) {
      owner = user.id
      status = null
    }
    listeners.add(setValue)
    setValue(status)
    if (listeners.size === 1) {
      refreshSyncStatus()
      document.addEventListener('visibilitychange', onVisible)
      window.addEventListener(CREDENTIALS_CHANGED, refreshSyncStatus)
    }
    return () => {
      listeners.delete(setValue)
      if (listeners.size === 0) {
        window.clearTimeout(timer)
        document.removeEventListener('visibilitychange', onVisible)
        window.removeEventListener(CREDENTIALS_CHANGED, refreshSyncStatus)
      }
    }
  }, [user])
  return value
}

/** «Обновить всё» или один вид: в очередь — то, что не загружается и не обновлялось только что. */
export async function syncAll(kinds?: string[]): Promise<SyncAllResponse> {
  const { data } = await api.post<SyncAllResponse>('/sync/all', kinds ? { kinds } : {})
  await refreshSyncStatus()
  return data
}

/** Самые старые из данных kinds (все — без kinds): когда обновлены, ещё не загружались ли. Для «Обновлено …». */
export function freshness(s: SyncStatus, kinds?: string[]): { at: string | null; never: boolean; kinds: SyncKind[] } {
  const list = s.kinds.filter((k) => !kinds || kinds.includes(k.kind))
  const never = list.some((k) => k.last_success_at === null)
  const times = list.map((k) => k.last_success_at).filter((t): t is string => t !== null)
  const at = times.length ? times.reduce((a, b) => (a < b ? a : b)) : null
  return { at, never, kinds: list }
}

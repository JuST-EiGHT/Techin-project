// Уведомления об аномалиях (шаг 7.2): типы, подписи и общий счётчик для колокольчика, страницы и главной.
import { useEffect, useState } from 'react'

import { useAuth } from '@/contexts/AuthContext'
import { api } from '@/lib/api'

export type Severity = 'red' | 'yellow'
export type AlertGroup = 'sales' | 'price' | 'margin' | 'stock' | 'promo' | 'money' | 'data'
export type AlertStatus = 'active' | 'resolved' | 'all'

export type Anomaly = {
  id: string
  type: string
  type_title: string // «Падение продаж»
  group: AlertGroup | null
  severity: Severity
  title: string // «Продажи упали на 62 %»
  message: string // что случилось и что сделать
  link: string | null // куда перейти: товар, акция, выплаты, календарь …
  product: { id: string; offer_id: string; name: string | null } | null
  metric_before: string | null
  metric_after: string | null
  threshold: string | null
  unit: string | null
  details: Record<string, unknown>
  money_at_risk: string | null // деньги под риском, ₽ — где можно оценить (10.5)
  detected_at: string
  last_seen_at: string
  resolved_at: string | null
  resolution: 'normal' | 'escalated' | 'gone' | null
  is_acknowledged: boolean
  acknowledged_at: string | null
}

// unread, red, yellow — без «Ок» (колокольчик); red_active, yellow_active — все активные (фильтры страницы)
export type AlertCounts = { active: number; unread: number; red: number; yellow: number; red_active: number; yellow_active: number }

export type AnomaliesPage = {
  items: Anomaly[]
  total: number
  limit: number
  offset: number
  counts: AlertCounts
  by_group: Partial<Record<AlertGroup, number>>
  checked_at: string | null
}

export const GROUPS: { value: AlertGroup; label: string; hint: string }[] = [
  { value: 'sales', label: 'Продажи', hint: 'упали или резко выросли — по магазину и по товарам' },
  { value: 'price', label: 'Цены', hint: 'цена или комиссия Ozon изменились за 7 дней' },
  { value: 'margin', label: 'Маржа', hint: 'маржа при текущей цене ниже вашего порога' },
  { value: 'stock', label: 'Остатки', hint: 'запаса меньше порога «Мало» или «Критично» или больше порога «Избыток»' },
  { value: 'promo', label: 'Акции', hint: 'Ozon сам добавил товар в акцию или в акции маржа ниже нуля' },
  { value: 'money', label: 'Деньги', hint: 'выплаты Ozon, сверка с банком, кассовый разрыв, операции без статьи' },
  { value: 'data', label: 'Данные', hint: 'синхронизация, пропуски в начислениях, отчёты с ошибкой, продажи без себестоимости' },
]

export const SEVERITY: Record<Severity, { label: string; dot: string; text: string; stripe: string }> = {
  red: { label: 'красная', dot: 'bg-bad', text: 'text-bad', stripe: 'border-l-bad' },
  yellow: { label: 'жёлтая', dot: 'bg-warn', text: 'text-warn', stripe: 'border-l-warn' },
}

export const RESOLUTION: Record<NonNullable<Anomaly['resolution']>, string> = {
  normal: 'вернулось в норму',
  escalated: 'стала красной — см. новую запись',
  gone: 'проверка больше не относится',
}

const MSK = { timeZone: 'Europe/Moscow' } as const

/** «сегодня в 14:57», «вчера в 09:12», «12.10 в 18:00» — по Москве. */
export function whenText(iso: string): string {
  const d = new Date(iso)
  const day = (x: Date) => x.toLocaleDateString('ru-RU', MSK)
  const time = d.toLocaleTimeString('ru-RU', { ...MSK, hour: '2-digit', minute: '2-digit' })
  const now = new Date()
  if (day(d) === day(now)) return `сегодня в ${time}`
  if (day(d) === day(new Date(now.getTime() - 86_400_000))) return `вчера в ${time}`
  return `${d.toLocaleDateString('ru-RU', { ...MSK, day: '2-digit', month: '2-digit' })} в ${time}`
}

/** Было → стало с единицей: «10 шт./день → 1 шт./день», «1 000 ₽ → 1 400 ₽». Одно значение уже в заголовке — null. */
export function metricText(a: Anomaly): string | null {
  if (a.metric_before === null || a.metric_after === null) return null
  const fmt = (v: string) => `${Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 2 })}${a.unit ? ` ${a.unit}` : ''}`
  return `было ${fmt(a.metric_before)} → стало ${fmt(a.metric_after)}`.replace(/-/g, '−')
}

// ── общий счётчик: один опрос /anomalies/count раз в 60 с на все компоненты ──
const POLL_MS = 60_000
let counts: AlertCounts | null = null
let owner: string | null = null
let timer: number | undefined
const listeners = new Set<(c: AlertCounts | null) => void>()

function publish(next: AlertCounts | null) {
  counts = next
  listeners.forEach((l) => l(next))
}

/** Перечитать счётчик сейчас (после «Ок» или «Отметить все»). */
export async function refreshAlertCounts(): Promise<void> {
  try {
    const { data } = await api.get<AlertCounts>('/anomalies/count', { skipAuthRedirect: true })
    publish(data)
  } catch {
    // нет сети или сессия кончилась — оставляем прежние цифры, следующий опрос попробует снова
  }
}

/** Счётчик ответа «Отметить все прочитанными» — без лишнего запроса. */
export const setAlertCounts = (c: AlertCounts) => publish(c)

function onVisible() {
  if (document.visibilityState === 'visible') refreshAlertCounts()
}

/** Активные аномалии: всего, без «Ок», красных и жёлтых. null — ещё не загрузились. */
export function useAlertCounts(): AlertCounts | null {
  const { user } = useAuth()
  const [value, setValue] = useState<AlertCounts | null>(counts)
  useEffect(() => {
    if (!user) return
    if (owner !== user.id) {
      owner = user.id
      counts = null
    }
    listeners.add(setValue)
    setValue(counts)
    if (listeners.size === 1) {
      refreshAlertCounts()
      timer = window.setInterval(() => document.visibilityState === 'visible' && refreshAlertCounts(), POLL_MS)
      document.addEventListener('visibilitychange', onVisible)
    }
    return () => {
      listeners.delete(setValue)
      if (listeners.size === 0) {
        window.clearInterval(timer)
        document.removeEventListener('visibilitychange', onVisible)
      }
    }
  }, [user])
  return value
}

/** «Ок» у одной аномалии; счётчик обновляется у всех. */
export async function acknowledge(id: string): Promise<Anomaly> {
  const { data } = await api.post<Anomaly>(`/anomalies/${id}/acknowledge`)
  await refreshAlertCounts()
  return data
}

/** «Отметить все прочитанными» — по фильтрам (пусто — все активные). */
export async function acknowledgeAll(filters: { severity?: Severity; group?: AlertGroup } = {}): Promise<number> {
  const { data } = await api.post<{ acknowledged: number; counts: AlertCounts }>('/anomalies/acknowledge-all', filters)
  setAlertCounts(data.counts)
  return data.acknowledged
}

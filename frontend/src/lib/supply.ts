// Планирование поставок (шаг 5.2): типы ответа /supply/recommendations и подписи.
import type { StockProduct, Thresholds } from '@/lib/stocks'

export type Urgency = 'critical' | 'urgent' | 'planned'
export type SupplyKind = 'supply' | 'purchase'
export type SupplyParams = { target_days: number; lead_time_days: number }

export type Coverage = {
  loaded_from: string
  synced_at: string
  window_from: string
  window_to: string
  days: number
  enough: boolean
}

export type SupplyItem = {
  product: StockProduct
  kind: SupplyKind
  urgency: Urgency
  destination: { key: string; name: string; warehouse_type: string | null; warehouses: string[] }
  sold_30: number
  sold_7: number
  avg_daily: string
  rate: string
  trend: string | null
  stock: number
  in_transit: number
  days_left: string | null
  target_stock: number
  need: number
  to_order: number
  arrives_late: boolean
}

export type SupplyReport = {
  params: SupplyParams
  saved: SupplyParams
  is_saved: boolean
  thresholds: Thresholds
  has_key: boolean
  postings_known: boolean
  fbo: Coverage | null
  fbs: Coverage | null
  has_own_warehouses: boolean
  totals: { critical: number; urgent: number; planned: number; supply_units: number; purchase_units: number; products: number }
  today: string
  items: SupplyItem[]
}

export const URGENCY: Record<Urgency, { label: string; text: string; badge: string; border: string }> = {
  critical: { label: 'Срочно', text: 'text-bad', badge: 'bg-bad/15 text-bad', border: 'border-l-bad' },
  urgent: { label: 'Скоро', text: 'text-warn', badge: 'bg-warn/15 text-warn', border: 'border-l-warn' },
  planned: { label: 'Плановые', text: 'text-accent', badge: 'bg-accent/15 text-accent', border: 'border-l-accent' },
}

export const URGENCY_ORDER: Urgency[] = ['critical', 'urgent', 'planned']

/** Что значит группа — по порогам остатков пользователя. */
export function urgencyHint(u: Urgency, t: Thresholds): string {
  if (u === 'critical') return `запаса меньше чем на ${t.critical} дн. — отправляйте сейчас`
  if (u === 'urgent') return `меньше чем на ${t.low} дн. — запланируйте поставку в ближайшие дни`
  return 'запаса меньше, чем нужно до следующей поставки, — добавьте в плановую'
}

export const KIND: Record<SupplyKind, string> = {
  supply: 'Поставка в Ozon',
  purchase: 'Закупка на свой склад',
}

export const TARGET_WHY =
  'На сколько дней продаж должно хватать товара, когда поставка приедет. Больше — реже возить, но дольше платить за хранение.'
export const LEAD_WHY =
  'Сколько дней от отправки до приёмки на складе. Пока поставка едет, товар продаётся — это количество добавим к поставке.'

// Планирование поставок (шаги 5.2, 10.18): типы ответов /supply/* и подписи — рекомендации, планы, заявки Ozon, календарь.
import type { StockProduct, Thresholds } from '@/lib/stocks'

export type Urgency = 'critical' | 'urgent' | 'planned'
export type SupplyKind = 'supply' | 'purchase'
export type SupplyParams = { target_days: number; lead_time_days: number; safety_days: number }

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
  in_plans: number // уже в открытых планах сервиса (10.18)
  in_orders: number // уже в заявках Ozon, которые ещё готовятся
  still_needed: number // нужно ещё
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
export const SAFETY_WHY =
  'Запас сверх цели на случай задержки поставки или всплеска продаж. 0 — без него; больше — реже «кончился», но больше денег в товаре.'

// ── 10.18: планы поставок ──
export type PlanStatus = 'draft' | 'ordered' | 'cancelled'
export type OrderGroup = 'preparing' | 'on_the_way' | 'accepting' | 'done' | 'failed'
export type OrderBrief = { id: string | null; ozon_order_id: number; number: string | null; state: string | null; state_text: string; group: OrderGroup }

export type Plan = {
  id: string
  kind: SupplyKind
  kind_text: string
  destination_key: string
  destination_name: string
  planned_date: string | null
  note: string | null
  status: PlanStatus
  created_at: string
  updated_at: string
  products: number
  units: number
  recommended: number
  corrected: number
  order: OrderBrief | null
}

export type PlanItem = {
  product: StockProduct
  recommended: number | null
  qty: number
  updated_at: string
  has_sku: boolean
  history: { qty: number; at: string }[]
}

export type PlanDetail = Plan & { items: PlanItem[]; editable: boolean; can_create_in_ozon: boolean }

export type DraftWarehouse = { id: number; name: string; address: string | null; available: boolean; state: string | null; reason: string | null; rank: number | null }
export type Draft = {
  id: string
  plan_id: string
  status: 'pending' | 'ready' | 'failed' | 'used'
  cluster_name: string | null
  warehouses: DraftWarehouse[]
  rejected: { sku: number; offer_id: string | null; product_id: string | null; reasons: string[] }[]
  errors: string[]
  created_at: string
  expires_at: string
  ozon_order_id: number | null
}
export type Timeslots = { timezone: string | null; days: { date: string; slots: { from: string; to: string }[] }[] }

// ── заявки Ozon ──
export type SupplyOrder = {
  id: string
  ozon_order_id: number
  number: string | null
  created_at: string | null
  state: string
  state_text: string
  group: OrderGroup
  state_updated_at: string | null
  timeslot_from: string | null
  timeslot_to: string | null
  timeslot_day: string | null
  timeslot_time: string | null
  dropoff: string | null
  storage_warehouse: string | null
  cluster: string | null
  accepted_on: string | null
  products: number | null
  declared: number | null
  accepted: number | null
  plan: { id: string; destination_name: string } | null
  synced_at: string
}
export type SupplyOrders = { counts: Record<OrderGroup, number>; total: number; items: SupplyOrder[]; group_text: Record<OrderGroup, string> }

export type SalesAfter = {
  known: boolean
  after_days: number
  after_days_with_data: number | null
  after_qty: number | null
  per_day_after: string | null
  before_qty: number | null
  change_pct: string | null
}
export type OrderResult = {
  product: { id: string | null; offer_id: string; name: string | null; ozon_sku: number | null }
  planned: number | null
  declared: number | null
  accepted: number | null
  diff: number | null
  plan_diff: number | null
  defect: boolean
  sales_after: SalesAfter | null
  lasts_days: number | null
}
export type SupplyOrderDetail = SupplyOrder & {
  events: { state: string; state_text: string; at: string; timeslot_from: string | null }[]
  items: OrderResult[]
  totals: { planned: number | null; declared: number | null; accepted: number | null; diff: number | null; short: number }
  has_acts: boolean
}

export type CalendarEvent = {
  date: string
  kind: 'plan' | 'timeslot' | 'accepted'
  title: string
  units: number
  plan_id: string | null
  order_id: string | null
  state: string | null
  state_text: string
  group: OrderGroup | null
  time: string | null
}

export const GROUP_TONE: Record<OrderGroup, string> = {
  preparing: 'bg-accent/15 text-accent',
  on_the_way: 'bg-warn/15 text-warn',
  accepting: 'bg-warn/15 text-warn',
  done: 'bg-ok/15 text-ok',
  failed: 'bg-line text-muted',
}
export const GROUP_ORDER: OrderGroup[] = ['preparing', 'on_the_way', 'accepting', 'done', 'failed']

export const PLAN_STATUS: Record<PlanStatus, { label: string; badge: string }> = {
  draft: { label: 'В работе', badge: 'bg-accent/15 text-accent' },
  ordered: { label: 'Заявка в Ozon', badge: 'bg-ok/15 text-ok' },
  cancelled: { label: 'Отменён', badge: 'bg-line text-muted' },
}

export const pieces = (n: number | null) => (n === null ? '—' : `${n.toLocaleString('ru-RU')} шт.`)

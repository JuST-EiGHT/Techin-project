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
  urgency: Urgency | null // null — везти не нужно (rest)
  status: RowStatus
  status_reason: 'covered' | 'no_sales' | 'below_min' | null
  first: boolean // товара в кластере нет и не было — «Поставить впервые»
  destination: {
    key: string
    name: string
    warehouse_type: string | null
    warehouses: string[]
    warehouse: { id: number; name: string } | null // 10.18a: склад кластера из настроек; нет — выберет Ozon
  }
  sold_30: number | null // закупка: заказы за период; у кластеров Ozon — null (продажи от Ozon)
  sold_7: number | null
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
  // 10.18a — у кластеров Ozon
  ozon_recommended: number | null // рекомендует Ozon; null — рекомендации не загружены
  own_need: number | null // по продажам в кластере
  basis: 'ozon' | 'sales' | null
  min_applied: boolean // довели до минимума на склад (коробка)
  days_after: string | null // хватит после поставки, дней
  ozon_grade: string | null
  ozon_reasons: string[]
}

export type OzonData = {
  clusters_known: boolean // Ozon посчитал продажи по кластерам (из остатков)
  recs_known: boolean
  recs_ok: boolean | null
  recs_synced_at: string | null
  recs_error: string | null
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
  ozon_data: OzonData
  min_qty: number
  totals: {
    critical: number
    urgent: number
    planned: number
    supply_units: number
    purchase_units: number
    products: number
    first: number
    enough: number
    idle: number
  }
  today: string
  items: SupplyItem[] // что везти
  rest: SupplyItem[] // пары товар × кластер Ozon, которые везти не нужно
}

// ── 10.18a: вид как в Ozon — по кластерам или по товарам, статус у каждой строки ──
export type RowStatus = Urgency | 'enough' | 'idle'
export type SupplyView = 'clusters' | 'products'
export type SupplyShow = 'all' | 'needed' | 'critical' | 'urgent' | 'planned' | 'first'

export const STATUS: Record<RowStatus, { label: string; text: string; badge: string; border: string }> = {
  critical: { label: 'Срочно поставить', text: 'text-bad', badge: 'bg-bad/15 text-bad', border: '' },
  urgent: { label: 'Скоро поставить', text: 'text-warn', badge: 'bg-warn/15 text-warn', border: '' },
  planned: { label: 'Плановая поставка', text: 'text-accent', badge: 'bg-accent/15 text-accent', border: '' },
  enough: { label: 'Пока хватает', text: 'text-ok', badge: 'bg-ok/15 text-ok', border: '' },
  idle: { label: 'Не поставлять', text: 'text-muted', badge: 'bg-line text-muted', border: '' },
}
export const STATUS_ORDER: RowStatus[] = ['critical', 'urgent', 'planned', 'enough', 'idle']

export const SHOW: { value: SupplyShow; label: string }[] = [
  { value: 'all', label: 'Все товары' },
  { value: 'needed', label: 'Нужно везти' },
  { value: 'critical', label: 'Срочно' },
  { value: 'urgent', label: 'Скоро' },
  { value: 'planned', label: 'Плановые' },
  { value: 'first', label: 'Поставить впервые' },
]

/** Почему не везём — простыми словами. */
export function skipText(item: SupplyItem, minQty: number): string | null {
  if (item.status_reason === 'covered') return 'запаса хватит на срок'
  if (item.status_reason === 'no_sales') return 'Ozon не видит продаж в кластере'
  if (item.status_reason === 'below_min') return `за срок продастся меньше ${minQty} шт. — меньше минимума`
  return null
}

export const VIEW_WHY =
  'По кластерам — что везти в каждый кластер: одна строка — одна поставка. По товарам — куда разложить каждый товар. «Все товары» показывает и то, что везти не нужно: «Пока хватает» и «Не поставлять».'

export const URGENCY: Record<Urgency, { label: string; text: string; badge: string; border: string }> = {
  critical: { label: 'Срочно', text: 'text-bad', badge: 'bg-bad/15 text-bad', border: '' },
  urgent: { label: 'Скоро', text: 'text-warn', badge: 'bg-warn/15 text-warn', border: '' },
  planned: { label: 'Плановые', text: 'text-accent', badge: 'bg-accent/15 text-accent', border: '' },
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

/** Статус ликвидности Ozon в кластере (`turnover_grade_cluster`) — простыми словами. */
export const GRADE_TEXT: Record<string, string> = {
  DEFICIT: 'Ozon: дефицит — хватит до 28 дн.',
  POPULAR: 'Ozon: очень популярный',
  ACTUAL: 'Ozon: популярный',
  SURPLUS: 'Ozon: избыток — больше 120 дн.',
  NO_SALES: 'Ozon: нет продаж 28 дн.',
  WAITING_FOR_SUPPLY: 'Ozon: ждёт поставки',
  RESTRICTED_NO_SALES: 'Ozon: в поставку нельзя',
  COLLECTING_DATA: 'Ozon: собирает данные',
}

export const MIN_QTY_WHY =
  'Меньше скольких штук одного товара не возить на склад — обычно столько, сколько в одной коробке. Меньше — довезём до этого числа, если Ozon советует поставку или столько продастся за срок; иначе не повезём.'
export const CLUSTER_WAREHOUSE_WHY =
  'Ozon принимает поставку в кластер и сам выбирает склад. Если в кластере вам важен конкретный склад, выберите его: он будет в рекомендациях и в Excel и выбран заранее при создании заявки.'

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

export type DraftWarehouse = {
  id: number
  name: string
  address: string | null
  available: boolean
  state: string | null
  reason: string | null
  rank: number | null
  preferred?: boolean // склад кластера из настроек (10.18a)
}
export type Draft = {
  id: string
  plan_id: string
  status: 'pending' | 'ready' | 'failed' | 'used'
  cluster_name: string | null
  warehouses: DraftWarehouse[]
  preferred_warehouse_id?: number | null
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

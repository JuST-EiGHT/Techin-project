// Склады и остатки (шаги 1.3, 5.1): типы ответов API и подписи статусов — общие для страницы складов,
// «Кандидатов на вывоз» и виджета «Заканчиваются» на главной.

export type StockStatus = 'critical' | 'low' | 'normal' | 'excess'
export type StatusKey = StockStatus | 'no_data'

export type Thresholds = { critical: number; low: number; excess: number }
export type StatusCounts = Record<StatusKey, number>

export type StockProduct = { id: string; offer_id: string; name: string | null; ozon_sku: number | null }

/** Товар в целом: остаток на всех складах, продажи, дни запаса, статус. */
export type ProductStock = {
  product: StockProduct
  available: number
  in_transit: number | null
  avg_daily_sales: string | null
  days_of_supply: string | null
  status: StockStatus | null
  last_sale: string | null
  days_without_sales: number | null // null — продаж в данных нет
  warehouses: number
}

export type WarehouseSummary = {
  id: string
  ozon_warehouse_id: number
  name: string
  type: 'FBO' | 'FBS' | 'rFBS'
  cluster_id: number | null
  cluster_name: string | null
  is_active: boolean
  synced_at: string
  skus: number
  total_present: number
  total_in_transit: number
  counts: StatusCounts
  top: (ProductStock & { present: number; in_transit_here: number | null })[]
}

export type Summary = {
  thresholds: Thresholds
  sales_known: boolean
  stocks_known: boolean
  synced_at: string | null
  totals: { products: number; present: number; in_transit: number; counts: StatusCounts }
  warehouses: WarehouseSummary[]
  running_out: ProductStock[]
  running_out_total: number
  lying: ProductStock[]
  lying_total: number
}

/** Строка таблицы: товар на складе. */
export type StockRow = {
  product: StockProduct
  warehouse: { id: string; name: string; type: string; cluster_name: string | null }
  present: number
  reserved: number | null
  in_transit: number | null
  avg_daily_sales: string | null
  days_of_supply: string | null
  product_available: number
  status: StockStatus | null
  last_sale: string | null
  days_without_sales: number | null
}

export type RunningOut = {
  thresholds: Thresholds
  sales_known: boolean
  stocks_known: boolean
  critical: number
  low: number
  items: ProductStock[]
}

// Цвета статусов — палитра из CLAUDE.md
export const STATUS: Record<StatusKey, { label: string; badge: string; row: string; text: string; dot: string }> = {
  critical: { label: 'Критично', badge: 'bg-bad/15 text-bad', row: 'border-l-bad bg-bad/5', text: 'text-bad', dot: 'bg-bad' },
  low: { label: 'Мало', badge: 'bg-warn/15 text-warn', row: 'border-l-warn bg-warn/5', text: 'text-warn', dot: 'bg-warn' },
  normal: { label: 'Норма', badge: 'bg-ok/15 text-ok', row: 'border-l-ok', text: 'text-ok', dot: 'bg-ok' },
  excess: { label: 'Избыток', badge: 'bg-accent/15 text-accent', row: 'border-l-accent bg-accent/5', text: 'text-accent', dot: 'bg-accent' },
  no_data: { label: 'Нет данных', badge: 'bg-line text-muted', row: 'border-l-transparent', text: 'text-muted', dot: 'bg-faint' },
}

export const STATUS_ORDER: StatusKey[] = ['critical', 'low', 'normal', 'excess', 'no_data']

/** Что значит статус при порогах пользователя — для подсказок и фильтра. */
export function statusHint(key: StatusKey, t: Thresholds): string {
  switch (key) {
    case 'critical':
      return `запаса меньше чем на ${t.critical} дн. продаж`
    case 'low':
      return `запаса меньше чем на ${t.low} дн.`
    case 'normal':
      return `запаса на ${t.low}–${t.excess} дн.`
    case 'excess':
      return `запаса больше чем на ${t.excess} дн. или товар не продаётся`
    default:
      return 'мало данных о продажах — дни запаса не посчитать'
  }
}

export const DEFAULT_THRESHOLDS: Thresholds = { critical: 7, low: 14, excess: 60 }

/** «5,5» — дробные по-русски; null — «—». */
export const num = (v: string | number | null) =>
  v === null || v === undefined ? '—' : Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 1 })

/** Продажи в день: до сотых — «0,03», а не «0». */
export const perDay = (v: string | null) =>
  v === null ? '—' : Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 2 })

export const qty = (v: number | null) => (v === null ? '—' : `${v.toLocaleString('ru-RU')} шт.`)

/** Сколько не продавался: «45 дн.», «сегодня», нет продаж в данных — «нет продаж». */
export function idleText(p: { days_without_sales: number | null }): string {
  if (p.days_without_sales === null) return 'продаж не было'
  if (p.days_without_sales === 0) return 'продажа сегодня'
  return `без продаж ${p.days_without_sales} дн.`
}

export function warehouseLabel(w: { name: string; type: string; cluster_name: string | null }) {
  return `${w.cluster_name ? `${w.cluster_name} · ` : ''}${w.name} (${w.type})`
}

/** Карточка товара (шаг 4.1) — боковая панель на странице товаров. */
export const productHref = (id: string) => `/dashboard/products/${id}`

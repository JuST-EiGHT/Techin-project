// Акции Ozon (шаги 1.5, 4.3): типы ответов /promotions и подписи — общие для списка, страницы акции и истории.

export type Promotion = {
  id: string
  ozon_action_id: number
  title: string
  action_type: string | null
  date_from: string | null
  date_to: string | null
  discount_type: string | null
  discount_value: string | null
  freeze_date: string | null
  is_frozen: boolean
  is_participating: boolean
  whole_stock: boolean
  whole_stock_warning: string | null
  products_active: number
  candidates: number
  auto_added: number
  min_margin: string | null
  avg_margin: string | null
  profitable: number
  low_margin: number
  loss: number
  no_margin: number
  urgent: boolean
}

export type MarginLevel = 'negative' | 'low' | 'ok'

export type PromoProductRow = {
  product: { id: string; offer_id: string; name: string | null; ozon_sku: number | null }
  action_price: string | null
  price: string | null
  discount_pct: string | null
  min_price: string | null
  max_action_price: string | null
  below_min_price: boolean
  stock: number | null
  stock_on_hand: number
  is_active: boolean
  added_automatically: boolean
  add_mode: string | null
  margin_in_promo: string | null
  margin_level: MarginLevel | null
  first_seen_at: string
}

export type ProductsResponse = { promotion: Promotion; margin_note: string | null; items: PromoProductRow[] }
export type AutoAddedRow = PromoProductRow & { promotion: Promotion }
export type AutoAddedResponse = { margin_note: string | null; items: AutoAddedRow[] }

export type HistoryItem = {
  id: string
  promotion: { id: string; title: string; action_type: string | null; date_from: string | null; date_to: string | null; whole_stock: boolean }
  product: { id: string; offer_id: string; name: string | null; ozon_sku: number | null }
  entered_at: string
  left_at: string | null
  status: 'active' | 'left' | 'ended'
  action_price: string | null
  price: string | null
  prices: { at: string; action_price: string | null; stock: number | null }[]
  added_automatically: boolean
  margin_in_promo: string | null
}

export type HistoryResponse = { date_from: string; date_to: string; items: HistoryItem[] }

export type ActionPreview = {
  action: 'join' | 'update' | 'leave'
  applied: boolean
  can_apply: boolean
  errors: string[]
  warnings: string[]
  needs_loss_ack: boolean
  before: { participating: boolean; action_price: string | null; stock: number | null }
  after: { participating: boolean; action_price: string | null; stock: number | null }
  action_price: string | null
  stock: number | null
  stock_required: boolean
  min_stock: number | null
  recommended_stock: number | null
  max_action_price: string | null
  price: string | null
  discount_pct: string | null
  margin: string | null
  unit_profit: string | null
  min_price: string | null
  on_hand: number
  whole_stock: boolean
  whole_stock_total: { qty: number; profit: string | null } | null
  boosting: boolean
  message: string | null
}

export const MARGIN_TEXT: Record<MarginLevel, string> = { negative: 'text-bad', low: 'text-warn', ok: 'text-ok' }

/** Вкладки страницы акции: маржа участвующих (порог 10 % — BUSINESS_LOGIC §7) и кандидаты. */
export type PromoTab = 'ok' | 'low' | 'negative' | 'none' | 'candidates'
export const PROMO_TABS: { value: PromoTab; label: string; hint: string }[] = [
  { value: 'ok', label: 'Выгодные', hint: 'маржа в акции от 10 %' },
  { value: 'low', label: 'Невыгодные', hint: 'маржа от 0 до 10 %' },
  { value: 'negative', label: 'Убыточные', hint: 'каждая продажа в минус' },
  { value: 'none', label: 'Без маржи', hint: 'нет себестоимости, комиссии или данных о логистике' },
  { value: 'candidates', label: 'Можно добавить', hint: 'Ozon предлагает добавить эти товары' },
]

export function tabOf(row: PromoProductRow): PromoTab {
  if (!row.is_active) return 'candidates'
  return row.margin_level === null ? 'none' : row.margin_level
}

export const HISTORY_STATUS: Record<HistoryItem['status'], { label: string; tone: 'ok' | 'muted' | 'warn' }> = {
  active: { label: 'участвует', tone: 'ok' },
  left: { label: 'вышел', tone: 'warn' },
  ended: { label: 'акция закончилась', tone: 'muted' },
}

export const fmtDate = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow' }) : '—')

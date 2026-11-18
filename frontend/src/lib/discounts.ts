// Заявки покупателей на скидку (шаг 4.3a): типы ответов /discounts и подписи — страница, окно решения, блок на
// странице товара.

export type DiscountState = 'NEW' | 'APPROVED' | 'DECLINED' | 'EXPIRED'
export type DiscountTab = 'new' | 'approved' | 'declined' | 'expired' | 'all'
export type DiscountMark = 'loss' | 'below_ozon_min' | 'low_margin'
export type DiscountMarkFilter = DiscountMark | 'rule_ok' | 'no_calc'

export type DiscountRow = {
  id: string
  ozon_task_id: number
  sku: number | null
  name: string | null
  product: { id: string; offer_id: string; name: string | null; sku: number | null; barcode: string | null } | null
  status: string
  state: DiscountState
  state_title: string
  can_decide: boolean
  created_at: string | null
  end_at: string | null
  edited_till: string | null
  hours_left: string | null
  requested_price: string | null
  requested_discount_pct: string | null
  discount_pct: string | null
  requested_quantity: number | null
  price: string | null
  original_price: string | null
  min_auto_price: string | null
  breakeven_price: string | null
  target_price: string | null
  margin: string | null
  unit_profit: string | null
  total_profit: string | null
  stock: number | null
  marks: DiscountMark[]
  mark_titles: string[]
  rule_ok: boolean | null
  missing: string[]
  approved_price: string | null
  approved_quantity: number | null
  approved_margin: string | null
  moderated_at: string | null
  is_auto_moderated: boolean | null
  first_seen_at: string | null
  decision: { action: string; status: string; at: string; price: string | null; quantity: number | null; error: string | null } | null
}

export type DiscountCounts = { new: number; approved: number; declined: number; expired: number; all: number; loss: number }

export type DiscountList = {
  items: DiscountRow[]
  counts: DiscountCounts
  low_margin_pct: string
  rule_margin_pct: string | null
  expiry_hours: number
}

export type DecisionAction = 'approve' | 'decline'

export type DecisionPreviewItem = {
  preview_id: string
  task_id: string
  ozon_task_id: number
  action: DecisionAction
  product: { id: string; offer_id: string; name: string | null } | null
  name: string | null
  requested_price: string | null
  requested_quantity: number | null
  price: string | null
  approved_price: string | null
  approved_quantity: number | null
  discount_pct: string | null
  breakeven_price: string | null
  margin: string | null
  unit_profit: string | null
  total_profit: string | null
  errors: string[]
  warnings: string[]
  needs_loss_ack: boolean
  can_apply: boolean
}

export type DecisionPreview = {
  batch_id: string
  expires_at: string
  comment: string | null
  items: DecisionPreviewItem[]
  summary: { total: number; ok: number; blocked: number; approve: number; decline: number; loss: number; needs_confirm: boolean }
}

export type DecisionResult = {
  batch_id: string
  items: {
    task_id: string
    ozon_task_id: number
    action: DecisionAction
    status: string
    status_title: string
    approved_price: string | null
    approved_quantity: number | null
    error: string | null
  }[]
  applied: number
  failed: number
  message: string
}

export const MARK_TONE: Record<DiscountMark, 'bad' | 'warn'> = { loss: 'bad', below_ozon_min: 'warn', low_margin: 'warn' }

export const MARK_FILTERS: { value: DiscountMarkFilter; label: string; hint: string }[] = [
  { value: 'loss', label: 'В минус', hint: 'запрошенная цена ниже минимальной для участия — каждая продажа убыточна' },
  { value: 'below_ozon_min', label: 'Ниже мин. цены в Ozon', hint: 'ниже минимальной цены товара после автоприменения скидок' },
  { value: 'low_margin', label: 'Маржа ниже порога', hint: 'маржа при запрошенной цене ниже вашего порога «низкой маржи»' },
  { value: 'rule_ok', label: 'Подходят под правило', hint: 'маржа не ниже порога вашего правила подсказки' },
  { value: 'no_calc', label: 'Нет расчёта', hint: 'не хватает себестоимости или других данных — маржа не посчитана' },
]

/** Срок: «через 5 ч», «через 2 дн.», «через 40 мин». */
export function leftText(hours: string | null): string {
  if (hours === null) return '—'
  const h = Number(hours)
  if (h < 1) return `через ${Math.max(1, Math.round(h * 60))} мин`
  if (h < 48) return `через ${Math.round(h)} ч`
  return `через ${Math.round(h / 24)} дн.`
}

/** Дата и время по Москве: «18.11 15:30». */
export function msk(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('ru-RU', {
    timeZone: 'Europe/Moscow',
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

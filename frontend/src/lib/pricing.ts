// Калькулятор цены (шаг 4.2): типы ответов /price и подписи — общие для страницы цен и панели товара.

export type Breakdown = {
  price: string
  commission: string
  acquiring: string
  ad: string
  logistics: string
  storage: string
  other: string
  cost: string // себестоимость с НДС поставщика
  vat: string // НДС к уплате
  income_tax: string // налог с дохода
  profit: string // до налога на прибыль
  margin: string | null
  profit_tax: string
  net: string
}

export type CalcTax = { title: string; known: boolean; notes: string[]; profit_tax_pct: string }

export type CalculateResponse = {
  product: { id: string; offer_id: string; name: string | null }
  inputs: {
    cost: string | null
    supplier_vat: string
    cost_with_vat: string | null
    commission_pct: string | null
    acquiring_pct: string
    ad_pct: string
    logistics: string | null
    logistics_source: 'product' | 'shop' | 'manual' | null
    logistics_text: string
    storage: string
    other: string
    target_margin_pct: string
    price: string | null
    promo_price: string | null
    sources: Record<string, string>
  }
  tax: CalcTax
  missing: string[]
  result: {
    min_price: string | null
    min_error: string | null
    target_price: string | null
    target_error: string | null
    profitable: boolean | null
    current: Breakdown | null
    promo: Breakdown | null
  } | null
  promo_title: string | null
}

/** Свои значения для расчёта: пустая строка — «как в данных». */
export type Overrides = { ad_pct: string; target_margin_pct: string; logistics: string; storage: string; other: string; price: string }

export type PriceRow = {
  id: string
  offer_id: string
  name: string | null
  ozon_sku: number | null
  is_archived: boolean
  price: string | null
  min_price: string | null
  target_price: string | null
  margin: string | null
  unit_profit: string | null
  below_min: boolean
  below_target: boolean
  gap_pct: string | null
  promo: { title: string; action_price: string; margin: string | null; count: number; added_automatically: boolean } | null
  logistics: string | null
  logistics_source: 'product' | 'shop' | null
  missing: string[]
  errors: string[]
}

export type PriceFilter = 'below_min' | 'below_target' | 'promo_loss' | 'no_data'

export type PricesPage = {
  items: PriceRow[]
  total: number
  limit: number
  offset: number
  counts: { all: number } & Record<PriceFilter, number>
  settings: { ad_pct: string; target_margin_pct: string; defaults: boolean }
  tax: CalcTax
  shop_logistics: { per_unit: string | null; text: string }
}

export type Suggestion = {
  id: string
  offer_id: string
  name: string | null
  price: string
  min_price: string
  target_price: string | null
  margin: string | null
  raise_to: string
  raise_pct: string
}

export type Suggestions = { loss: Suggestion[]; loss_count: number; underpriced: Suggestion[]; underpriced_count: number }

export const PRICE_FILTERS: { value: PriceFilter; label: string; hint: string }[] = [
  { value: 'below_min', label: 'Ниже минимальной', hint: 'Каждая продажа по текущей цене — в минус' },
  { value: 'below_target', label: 'Ниже целевой', hint: 'В плюс, но меньше вашей целевой маржи' },
  { value: 'promo_loss', label: 'В акции в минус', hint: 'Цена в акции ниже минимальной' },
  { value: 'no_data', label: 'Не посчитать', hint: 'Нет себестоимости, комиссии или данных о логистике' },
]

export const AD_WHY =
  'Реклама — доля цены, которую вы тратите на продвижение в Ozon (трафареты, поиск). Ozon не делит её по товарам, поэтому указываете вы: она входит в минимальную цену. Не рекламируетесь — оставьте 0.'
export const MARGIN_WHY =
  'Целевая маржа — сколько процентов от цены вы хотите оставлять себе (до налога на прибыль). По ней считается целевая цена; минимальная — при марже 0.'

/** Число из поля ввода («12,5» → «12.5»); пусто — null, не число — NaN. */
export function fieldNumber(text: string): number | null {
  const t = text.replace(/\s/g, '').replace(',', '.')
  return t === '' ? null : Number(t)
}

// Изменение цены в Ozon (шаг 6.2): предпросмотр → применение по preview_id.
export type PricePreview = {
  preview_id: string
  created_at: string
  expires_at: string // после — не применить, нужен новый предпросмотр
  can_apply: boolean
  errors: string[] // блокируют
  warnings: string[]
  product: { id: string; offer_id: string; name: string | null }
  price_before: string | null // сейчас в Ozon
  price_after: string
  old_price: string | null // зачёркнутая — сервис её не меняет
  min_price_ozon: string | null
  change_pct: string | null
  change_rub: string | null
  floor: string | null // ниже нельзя: себестоимость с НДС × 1,05
  allowed_from: string | null // что можно за один раз
  allowed_to: string | null
  next_change_at: string | null
  profit_before: string | null
  profit_after: string | null
  margin_before: string | null
  margin_after: string | null
  min_price: string | null // наша минимальная (без убытка)
  target_price: string | null
  margin_note: string | null
  price_checked_at: string | null
  price_fresh: boolean
  reason: string | null
}

export type PriceApplyResult = {
  change_id: string
  status: string
  applied: boolean
  price_before: string | null
  price_after: string
  warnings: string[]
  message: string
}

// Массовое изменение цен (шаг 6.3).
export type BulkWay = 'target_margin' | 'percent' | 'min_plus' | 'file'
export type BulkGroup = 'ok' | 'warn' | 'blocked'

export type BulkPreviewItem = {
  preview_id: string
  product: { id: string; offer_id: string; name: string | null }
  group: BulkGroup
  price_before: string | null
  price_after: string | null // null — цену не посчитать
  change_pct: string | null
  note: string | null // «по стратегии — …, за один раз можно до …»
  line: number | null // строка файла
  errors: string[]
  warnings: string[]
  profit_before: string | null
  profit_after: string | null
  margin_before: string | null
  margin_after: string | null
}

export type BulkPreview = {
  batch_id: string | null // null — менять нечего
  created_at: string
  expires_at: string
  description: string | null
  strategy: string | null
  price_fresh: boolean
  warnings: string[]
  counts: { ok: number; warn: number; blocked: number; unchanged: number; total: number }
  items: BulkPreviewItem[]
  file: { rows: number; unchanged: number; errors: { line: number; offer_id: string; message: string }[] } | null
}

export type BulkItemStatus = 'applied' | 'rejected' | 'failed' | 'unknown' | 'blocked' | 'queued' | 'sending' | 'not_applied'

export type BulkStatus = {
  batch_id: string
  started: boolean
  finished: boolean
  total: number
  counts: Record<BulkItemStatus, number>
  batches: { no: number; size: number; state: 'waiting' | 'sending' | 'done'; applied: number; rejected: number; failed: number; unknown: number }[]
  items: {
    preview_id: string
    product: { id: string; offer_id: string; name: string | null }
    status: BulkItemStatus
    price_before: string | null
    price_after: string | null
    batch_no: number | null
    error: string | null
  }[]
  message: string | null
}

export const BULK_LIMIT = 500
export const BULK_CONFIRM_ABOVE = 10
export const BULK_WORD_ABOVE = 50
export const BULK_WORD = 'ПОДТВЕРЖДАЮ'
export const OZON_BATCH = 100

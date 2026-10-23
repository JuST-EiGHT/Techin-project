// Страница товаров (шаг 4.1): типы ответа /products и подписи — общие для таблицы и боковой панели. «Все товары» (10.13):
// продажи, выручка и прибыль за период, индикаторы проблем, выбор товаров для групповых действий.

export type StockStatus = 'critical' | 'low' | 'normal' | 'excess'
export type PriceIndex = 'super' | 'green' | 'yellow' | 'red'
export type QuickFilter = 'negative' | 'low_stock' | 'critical_stock' | 'in_promo' | 'no_cost'

export type ProductPromo = {
  id: string
  title: string
  action_price: string | null
  margin_in_promo: string | null
  added_automatically: boolean
  date_to: string | null
}

export type ProductItem = {
  id: string
  offer_id: string
  name: string | null
  ozon_sku: number | null
  ozon_product_id: number
  is_archived: boolean
  price: string | null
  old_price: string | null
  marketing_price: string | null
  min_price: string | null
  commission_pct: string | null
  commission: string | null
  acquiring_pct: string | null
  price_updated_at: string | null
  cost_price: string | null
  cost_with_vat: string | null
  stock: number | null
  in_transit: number | null
  avg_daily_sales: string | null
  days_of_supply: string | null
  stock_status: StockStatus | null
  unit_profit: string | null
  margin: string | null
  margin_level: 'negative' | 'low' | 'ok' | null
  price_index: PriceIndex | null
  promotions: ProductPromo[]
  last_sale: string | null
  // за выбранный период (10.13): нет начислений за период — null, без продаж — 0; прибыль без себестоимости — null
  sold_qty: number | null
  revenue: string | null
  period_profit: string | null
  flags: { code: FlagCode; text: string }[]
}

export type FlagCode = 'loss' | 'low_margin' | 'low_stock' | 'no_cost' | 'price_changed'

export type ProductsPage = {
  items: ProductItem[]
  total: number
  limit: number
  offset: number
  counts: { all: number; no_cost: number; in_promo: number; critical_stock: number; low_stock: number; negative: number; archived: number }
  sales_known: boolean
  stocks_known: boolean
  sales_window_days: number
  margin_note: string
  period: { date_from: string; date_to: string; data_source: string; days_total: number; missing_days: number }
}

export type ProductDetail = {
  item: ProductItem
  barcode: string | null
  category_id: number | null
  vat: string | null
  cost: {
    cost_price: string
    vat_multiplier: string | null
    supplier_vat: string
    cost_with_vat: string
    effective_from: string
    note: string | null
    history_count: number
  } | null
  supplier_vat: string
  stocks: { warehouse: string; type: string; cluster_name: string | null; present: number; in_transit: number | null; reserved: number | null }[]
  price_days: { date: string; price: string | null; min_price: string | null; marketing_price: string | null }[]
  price_changes: { at: string; price: string | null; old_price: string | null; min_price: string | null; marketing_price: string | null; commission_pct: string | null }[]
  first_price_at: string | null
}

// Цвета — токены темы (CLAUDE.md, правило 9)
export const STOCK_STATUS: Record<StockStatus, { label: string; tone: string }> = {
  critical: { label: 'критично', tone: 'text-bad' },
  low: { label: 'мало', tone: 'text-warn' },
  normal: { label: 'норма', tone: 'text-ok' },
  excess: { label: 'избыток', tone: 'text-accent' },
}

export const PRICE_INDEX: Record<PriceIndex, { label: string; dot: string; hint: string }> = {
  super: { label: 'супер', dot: 'bg-ok', hint: 'цена ниже, чем на такой же товар у других продавцов и площадок' },
  green: { label: 'выгодный', dot: 'bg-ok', hint: 'цена не выше, чем у других продавцов и площадок' },
  yellow: { label: 'умеренный', dot: 'bg-warn', hint: 'цена немного выше, чем у других продавцов и площадок' },
  red: { label: 'невыгодный', dot: 'bg-bad', hint: 'цена заметно выше, чем у других продавцов и площадок' },
}

export const INDEX_NOTE =
  'Индекс цены Ozon сравнивает вашу цену с ценой такого же товара у других продавцов и на других площадках. От него зависит, насколько охотно Ozon показывает товар покупателям.'

export const MARGIN_TONE = { negative: 'text-bad', low: 'text-warn', ok: 'text-ok' } as const

/** Цвет маржи, %: в минус — красным, ниже порога «низкой маржи» пользователя — жёлтым, выше — зелёным; нет — серым. */
export const marginTone = (m: string | null, low: number) =>
  m === null ? 'text-muted' : Number(m) < 0 ? MARGIN_TONE.negative : Number(m) < low ? MARGIN_TONE.low : MARGIN_TONE.ok

export const QUICK_FILTERS: { value: QuickFilter; label: string; hint: string; count: keyof ProductsPage['counts'] }[] = [
  { value: 'negative', label: 'В минус', hint: 'Маржа при текущей цене ниже нуля', count: 'negative' },
  { value: 'low_stock', label: 'Мало остатков', hint: 'Запаса меньше порога «Мало» (по умолчанию 14 дней продаж)', count: 'low_stock' },
  { value: 'in_promo', label: 'В акции', hint: 'Участвует в текущей акции Ozon', count: 'in_promo' },
  { value: 'no_cost', label: 'Нет себестоимости', hint: 'Без неё не посчитать маржу и прибыль', count: 'no_cost' },
]

export const FILTER_LABEL: Record<QuickFilter, string> = {
  negative: 'В минус',
  low_stock: 'Мало остатков',
  critical_stock: 'Критичный остаток',
  in_promo: 'В акции',
  no_cost: 'Нет себестоимости',
}

export type ColumnKey =
  | 'price'
  | 'min_price'
  | 'sold'
  | 'revenue'
  | 'profit'
  | 'commission'
  | 'cost'
  | 'stock'
  | 'days'
  | 'margin'
  | 'index'
  | 'promo'
  | 'last_sale'

/** Настраиваемые колонки: артикул и название видны всегда; first — порядок при первом нажатии на заголовок. */
export const COLUMNS: { key: ColumnKey; label: string; hint: string; first: 'asc' | 'desc' }[] = [
  { key: 'price', label: 'Цена', hint: 'Ваша цена в Ozon', first: 'desc' },
  { key: 'min_price', label: 'Мин. цена', hint: 'Минимальная цена в Ozon: ниже неё цена в акциях не опустится. Не задана — «—»', first: 'desc' },
  { key: 'sold', label: 'Продано', hint: 'Штук за выбранный период — по начислениям Ozon', first: 'desc' },
  { key: 'revenue', label: 'Выручка', hint: 'За выбранный период — строки «Выручка» в начислениях', first: 'desc' },
  { key: 'profit', label: 'Прибыль', hint: 'До налога за выбранный период — как в «Прибыли по товарам»; без себестоимости — «—»', first: 'desc' },
  { key: 'commission', label: 'Комиссия', hint: 'Комиссия Ozon за продажу (FBO), % и ₽ от текущей цены', first: 'desc' },
  { key: 'cost', label: 'Себестоимость', hint: 'За 1 шт. без НДС, на сегодня', first: 'desc' },
  { key: 'stock', label: 'Остаток', hint: 'Доступно к продаже на всех складах; ниже — в пути', first: 'desc' },
  { key: 'days', label: 'Дней запаса', hint: 'Остаток на всех складах / продажи в день за 30 дней', first: 'asc' },
  { key: 'margin', label: 'Маржа', hint: 'При текущей цене — как в калькуляторе цены, с логистикой и налогами', first: 'asc' },
  { key: 'index', label: 'Индекс цены', hint: 'Цветовой индекс цены Ozon: насколько ваша цена выгодна покупателю', first: 'desc' },
  { key: 'promo', label: 'Акции', hint: 'Текущие акции, в которых участвует товар', first: 'desc' },
  { key: 'last_sale', label: 'Посл. продажа', hint: 'Последний день с продажей по загруженным начислениям', first: 'desc' },
]

export const DEFAULT_HIDDEN: ColumnKey[] = ['min_price']

/** Индикаторы проблем у строки (10.13): значок и цвет — токенами темы; текст подсказки приходит с сервера. */
export const FLAGS: Record<FlagCode, { mark: string; tone: string; label: string }> = {
  loss: { mark: '↓', tone: 'bg-bad/15 text-bad', label: 'Убыток' },
  low_margin: { mark: '%', tone: 'bg-warn/15 text-warn', label: 'Низкая маржа' },
  low_stock: { mark: '▢', tone: 'bg-warn/15 text-warn', label: 'Мало остатков' },
  no_cost: { mark: '?', tone: 'bg-warn/15 text-warn', label: 'Нет себестоимости' },
  price_changed: { mark: '₽', tone: 'bg-accent/15 text-accent', label: 'Цена изменилась' },
}

/** Выбор товаров → «Цены списком» (6.3): страница цен откроется с этими товарами (на время вкладки браузера). */
export const BULK_SELECTION_KEY = 'prices.bulk.selection'
export function handOverSelection(ids: string[]): void {
  try {
    sessionStorage.setItem(BULK_SELECTION_KEY, JSON.stringify(ids))
  } catch {
    // хранилище недоступно — страница цен откроется без выбора
  }
}
export function takeSelection(): string[] | null {
  try {
    const raw = sessionStorage.getItem(BULK_SELECTION_KEY)
    sessionStorage.removeItem(BULK_SELECTION_KEY)
    return raw ? (JSON.parse(raw) as string[]) : null
  } catch {
    return null
  }
}

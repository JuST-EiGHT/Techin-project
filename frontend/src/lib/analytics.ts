// Основа аналитики (шаг 10.3): ответ /analytics/metrics, показатели, фильтры и их запись в адрес страницы
// (ссылкой можно поделиться, «назад» возвращает прежний фильтр). Определения — docs/BUSINESS_LOGIC.md §10.
import { type Range, addDays, daysCount, parseDay, presetRange, todayMsk } from '@/lib/period'

export type Granularity = 'day' | 'week' | 'month'

export type MetricValues = {
  revenue: string | null
  profit: string | null
  margin: string | null
  orders: number | null
  ordered_qty: number | null
  sold_qty: number | null
  returned_qty: number | null
  returns_amount: string | null
  avg_price: string | null
}
export type MetricPoint = MetricValues & {
  date_from: string
  date_to: string
  partial: boolean
  days: number
  missing_days: number
  orders_missing_days: number
  prev: (MetricValues & { date_from: string; date_to: string; partial: boolean }) | null
}
export type MetricsCoverage = {
  days_total: number
  missing_days: number
  orders_missing_days: number
  missing: { date_from: string; date_to: string }[]
  data_source: string
}
export type Metrics = {
  date_from: string
  date_to: string
  granularity: Granularity
  filter: {
    products: { id: string; offer_id: string; name: string | null }[] | null
    category: { id: number; name: string; path: string | null } | null
    product_count: number | null
  }
  coverage: MetricsCoverage
  profit_kind: 'net' | 'before_tax'
  totals: MetricValues & { missing_days: number; orders_missing_days: number; missing_cost: number }
  points: MetricPoint[]
  previous: { date_from: string; date_to: string; coverage: MetricsCoverage; totals: MetricValues } | null
  changes: (Record<Exclude<keyof MetricValues, 'margin'>, string | null> & { margin_pp: string | null }) | null
  compare_note?: string | null
}
export type CategoryOption = { id: number; name: string; path: string | null; products: number }
export type ProductOption = { id: string; offer_id: string; name: string | null; category_id: number | null; is_archived: boolean }

export type MetricKey = keyof MetricValues
export type MetricFormat = 'rub' | 'qty' | 'count' | 'pct'

/** Показатели: подпись, формат и хорошо ли, когда растёт (возвраты — нет). */
export const METRICS: Record<MetricKey, { label: string; format: MetricFormat; upIsGood: boolean }> = {
  revenue: { label: 'Выручка', format: 'rub', upIsGood: true },
  profit: { label: 'Прибыль', format: 'rub', upIsGood: true },
  margin: { label: 'Маржинальность', format: 'pct', upIsGood: true },
  orders: { label: 'Заказы', format: 'count', upIsGood: true },
  ordered_qty: { label: 'Заказано, шт.', format: 'qty', upIsGood: true },
  sold_qty: { label: 'Продано, шт.', format: 'qty', upIsGood: true },
  returned_qty: { label: 'Возвраты, шт.', format: 'qty', upIsGood: false },
  returns_amount: { label: 'Возвраты, ₽', format: 'rub', upIsGood: false },
  avg_price: { label: 'Средняя цена', format: 'rub', upIsGood: true },
}

export function formatMetric(v: string | number | null, format: MetricFormat): string {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  if (format === 'pct') return `${n.toLocaleString('ru-RU', { maximumFractionDigits: 1 })} %`.replace('-', '−')
  if (format === 'qty') return `${n.toLocaleString('ru-RU')} шт.`
  if (format === 'count') return n.toLocaleString('ru-RU')
  return `${n.toLocaleString('ru-RU', { maximumFractionDigits: 0 })} ₽`.replace('-', '−')
}

// ── фильтры ──
export type PeriodPreset = 'week' | '7d' | '30d' | 'month' | 'prev_month' | '90d' | '365d' | 'custom'
export const PERIOD_PRESETS: { value: PeriodPreset; label: string }[] = [
  { value: 'week', label: 'Эта неделя' },
  { value: '7d', label: '7 дней' },
  { value: '30d', label: '30 дней' },
  { value: 'month', label: 'Этот месяц' },
  { value: 'prev_month', label: 'Прошлый месяц' },
  { value: '90d', label: '90 дней' },
  { value: '365d', label: 'Год' },
  { value: 'custom', label: 'Свой период' },
]
export const GRANULARITIES: { value: Granularity; label: string }[] = [
  { value: 'day', label: 'По дням' },
  { value: 'week', label: 'По неделям' },
  { value: 'month', label: 'По месяцам' },
]

export type AnalyticsFilter = {
  preset: PeriodPreset
  range: Range
  granularity: Granularity
  /** Детализация выбрана вручную; иначе — по длине периода. */
  granularityChosen: boolean
  product: string | null
  category: number | null
  compare: boolean
}

const DEFAULT_PRESET: PeriodPreset = '30d' // у страницы может быть свой (главная — «Этот месяц»)
const DAY = /^\d{4}-\d{2}-\d{2}$/
const MAX_DAYS = 366

/** Детализация по умолчанию: до 45 дней — по дням, до полугода — по неделям, дальше — по месяцам. */
export function autoGranularity(range: Range): Granularity {
  const n = daysCount(range)
  return n <= 45 ? 'day' : n <= 186 ? 'week' : 'month'
}

export function rangeOf(preset: Exclude<PeriodPreset, 'custom'>, today = todayMsk()): Range {
  if (preset === 'week' || preset === 'month' || preset === 'prev_month') return presetRange(preset, today)
  const days = { '7d': 7, '30d': 30, '90d': 90, '365d': 365 }[preset]
  return { from: addDays(today, -(days - 1)), to: today }
}

/** Фильтр из адреса: ?period=30d | period=custom&from=…&to=…, g, product, category, compare=0. Мусор — значения по умолчанию. */
export function parseFilter(params: URLSearchParams, today = todayMsk(), defaultPreset = DEFAULT_PRESET): AnalyticsFilter {
  const raw = params.get('period') as PeriodPreset | null
  let preset: PeriodPreset = PERIOD_PRESETS.some((p) => p.value === raw) ? (raw as PeriodPreset) : defaultPreset
  let range: Range
  const from = params.get('from') ?? ''
  const to = params.get('to') ?? ''
  if (preset === 'custom' && DAY.test(from) && DAY.test(to) && from <= to && daysCount({ from, to }) <= MAX_DAYS) {
    range = { from, to }
  } else {
    if (preset === 'custom') preset = defaultPreset
    range = rangeOf(preset as Exclude<PeriodPreset, 'custom'>, today)
  }
  const g = params.get('g') as Granularity | null
  const chosen = GRANULARITIES.some((x) => x.value === g)
  const category = params.get('category')
  return {
    preset,
    range,
    granularity: chosen ? (g as Granularity) : autoGranularity(range),
    granularityChosen: chosen,
    product: params.get('product') || null,
    category: category !== null && /^\d+$/.test(category) ? Number(category) : null,
    compare: params.get('compare') !== '0',
  }
}

/** Фильтр → строка адреса без значений по умолчанию. Остальные параметры страницы (вкладки и т. п.) сохраняются. */
export function filterQuery(f: AnalyticsFilter, base = new URLSearchParams(), defaultPreset = DEFAULT_PRESET): string {
  const q = new URLSearchParams(base)
  for (const key of ['period', 'from', 'to', 'g', 'product', 'category', 'compare']) q.delete(key)
  if (f.preset !== defaultPreset) q.set('period', f.preset)
  if (f.preset === 'custom') {
    q.set('from', f.range.from)
    q.set('to', f.range.to)
  }
  if (f.granularityChosen) q.set('g', f.granularity)
  if (f.product) q.set('product', f.product)
  if (f.category !== null) q.set('category', String(f.category))
  if (!f.compare) q.set('compare', '0')
  return q.toString()
}

/** Параметры запроса /analytics/metrics. */
export const metricsParams = (f: AnalyticsFilter) => ({
  date_from: f.range.from,
  date_to: f.range.to,
  granularity: f.granularity,
  ...(f.product ? { product_id: f.product } : {}),
  ...(f.category !== null ? { category_id: f.category } : {}),
  compare: f.compare ? 1 : 0,
})

const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
const MONTHS_TITLE = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']

/** Подпись точки на оси: «07.09», неделя — «07.09», месяц — «сен 2026». */
export function pointLabel(p: { date_from: string }, g: Granularity): string {
  const d = parseDay(p.date_from)
  if (g === 'month') return `${MONTHS_SHORT[d.getUTCMonth()]} ${d.getUTCFullYear()}`
  return `${p.date_from.slice(8, 10)}.${p.date_from.slice(5, 7)}`
}

/** Заголовок точки в подсказке: «07.09.2026», «07.09 – 13.09.2026», «Сентябрь 2026». */
export function pointTitle(p: { date_from: string; date_to: string }, g: Granularity): string {
  const fmt = (iso: string) => iso.split('-').reverse().join('.')
  if (g === 'day') return fmt(p.date_from)
  if (g === 'month' && p.date_from.slice(8) === '01') {
    const d = parseDay(p.date_from)
    return `${MONTHS_TITLE[d.getUTCMonth()]} ${d.getUTCFullYear()}`
  }
  return `${fmt(p.date_from).slice(0, 5)} – ${fmt(p.date_to)}`
}

'use client'
// Основа аналитики (шаг 10.3): общие фильтры (период, товар, категория, детализация, сравнение, «обновлено …»), их
// состояние — в адресе страницы; загрузка показателей; график показателя через charts.tsx (правило 16). Страницы —
// главная (10.4), «Аналитика» (10.7 и дальше), «Товары», «Финансы» — берут их отсюда, а не пишут свои.
import { Search, X } from 'lucide-react'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { FormError } from '@/components/AuthForm'
import { type Drill, MoreLink, TipBox, useDrill } from '@/components/charts'
import { compact, useTokenColors } from '@/components/finance'
import {
  CheckOption,
  ChipOptions,
  DateRangePicker,
  FilterBar,
  FilterButton,
  FilterField,
  rangeLabel,
  selectClass,
} from '@/components/filters'
import { UpdatedAt } from '@/components/sync'
import { useDismiss } from '@/hooks/useDismiss'
import {
  type AnalyticsFilter,
  type CategoryOption,
  GRANULARITIES,
  type Granularity,
  METRICS,
  type MetricKey,
  type MetricPoint,
  type Metrics,
  MAX_DAYS,
  PERIOD_PRESETS,
  type PeriodPreset,
  type ProductOption,
  FILTER_EVENT,
  FILTER_PAGES,
  autoGranularity,
  filterQuery,
  formatMetric,
  metricsParams,
  parseFilter,
  pointLabel,
  pointTitle,
  rangeOf,
  rememberFilter,
  rememberedFilter,
} from '@/lib/analytics'
import { api, errorMessage } from '@/lib/api'
import { NAV } from '@/lib/nav'
import { daysWord } from '@/lib/period'

/** Фильтр из адреса и его изменение: новый фильтр — новая запись истории, «назад» возвращает прежний. */
export function useAnalyticsFilter(
  defaultPreset?: PeriodPreset,
  opts: { remember?: boolean } = {},
): [AnalyticsFilter, (patch: Partial<AnalyticsFilter>) => void] {
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const key = params.toString()
  const fromUrl = useMemo(() => parseFilter(new URLSearchParams(key), undefined, defaultPreset), [key, defaultPreset])
  // выбор виден сразу, адрес догоняет (иначе галочка и кнопки на миг «отскакивают» до перехода)
  const [filter, setFilter] = useState(fromUrl)
  useEffect(() => setFilter(fromUrl), [fromUrl])
  // подразделы «Аналитики» (10.7): фильтр запоминается — вкладки откроют соседний подраздел с ним же
  const remember = opts.remember ?? false
  useEffect(() => {
    if (remember) rememberFilter(filterQuery(fromUrl, new URLSearchParams(), defaultPreset))
  }, [remember, fromUrl, defaultPreset])
  const update = useCallback(
    (patch: Partial<AnalyticsFilter>) => {
      const next = { ...filter, ...patch }
      if (patch.preset && patch.preset !== 'custom') next.range = rangeOf(patch.preset)
      if (!next.granularityChosen) next.granularity = autoGranularity(next.range)
      setFilter(next)
      const q = filterQuery(next, new URLSearchParams(key), defaultPreset)
      router.push(q ? `${pathname}?${q}` : pathname, { scroll: false })
    },
    [filter, key, pathname, router, defaultPreset],
  )
  return [filter, update]
}

/** Показатели по фильтру. Повторный запрос с тем же фильтром — из кэша сервера (5 минут, как P&L). */
export function useMetrics(filter: AnalyticsFilter): { data: Metrics | null; error: string | null; loading: boolean } {
  const [state, setState] = useState<{ data: Metrics | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: true })
  const query = JSON.stringify(metricsParams(filter))
  useEffect(() => {
    let alive = true
    setState((s) => ({ ...s, loading: true, error: null }))
    api
      .get<Metrics>('/analytics/metrics', { params: JSON.parse(query) })
      .then(({ data }) => alive && setState({ data, error: null, loading: false }))
      .catch((err) => alive && setState({ data: null, error: errorMessage(err), loading: false }))
    return () => {
      alive = false
    }
  }, [query])
  return state
}

const chip = (on: boolean) =>
  `whitespace-nowrap rounded-lg border px-2.5 py-1 text-sm ${on ? 'border-accent bg-accent/10 text-accent' : 'border-line text-muted hover:text-fg'}`
const input = 'rounded-lg border border-line bg-bg px-2.5 py-1 text-sm outline-none focus:border-accent'

function ProductPicker({ value, onChange }: { value: string | null; onChange: (id: string | null) => void }) {
  const [text, setText] = useState('')
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<ProductOption[]>([])
  const [selected, setSelected] = useState<ProductOption | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const close = useCallback(() => setOpen(false), [])
  useDismiss(open, close, box)

  // выбранный товар из адреса — подписать артикулом и названием
  useEffect(() => {
    if (!value) return setSelected(null)
    if (selected?.id === value) return
    api.get<ProductOption[]>('/analytics/products', { params: { id: value } }).then(({ data }) => setSelected(data[0] ?? null)).catch(() => {})
  }, [value, selected?.id])

  useEffect(() => {
    if (!open) return
    const t = window.setTimeout(() => {
      api.get<ProductOption[]>('/analytics/products', { params: { search: text || undefined } }).then(({ data }) => setItems(data)).catch(() => setItems([]))
    }, 250)
    return () => window.clearTimeout(t)
  }, [text, open])

  if (value) {
    return (
      <span className="flex h-9 max-w-full items-center gap-1 rounded-lg border border-accent bg-accent/10 px-2.5 text-sm text-accent" data-testid="filter-product">
        <span className="truncate">{selected ? `${selected.offer_id}${selected.name ? ` · ${selected.name}` : ''}` : 'Товар'}</span>
        <button type="button" onClick={() => onChange(null)} aria-label="Все товары" className="shrink-0 hover:text-fg">
          <X className="h-4 w-4" />
        </button>
      </span>
    )
  }
  return (
    <div ref={box} className="relative w-72 max-w-full">
      <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted" aria-hidden />
      <input
        type="search"
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        placeholder="Артикул, название или штрихкод"
        aria-label="Товар: артикул, название или штрихкод"
        className="h-9 w-full rounded-lg border border-line bg-card pl-8 pr-2.5 text-sm outline-none placeholder:text-faint focus:border-accent"
        data-testid="filter-product-search"
      />
      {open && (
        <ul className="absolute left-0 top-full z-20 mt-1 max-h-72 w-80 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-xl border border-line bg-card p-1 shadow-xl shadow-black/20" data-testid="filter-product-list">
          {items.length === 0 && <li className="px-3 py-2 text-sm text-muted">Ничего не нашлось</li>}
          {items.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => {
                  setSelected(p)
                  setText('')
                  setOpen(false)
                  onChange(p.id)
                }}
                className="block w-full rounded-lg px-3 py-1.5 text-left text-sm hover:bg-line/60"
              >
                <span className="font-medium">{p.offer_id}</span>
                {p.name && <span className="block truncate text-xs text-muted">{p.name}</span>}
                {p.is_archived && <span className="text-xs text-faint"> · в архиве</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Общие фильтры аналитики (правка 11.11.2026): снаружи — товар (артикул, название, штрихкод) и период одной кнопкой,
 * остальное — категория, детализация, сравнение — за кнопкой «Фильтры». kinds — какие данные показывает страница: по ним
 * «Обновлено …» (кнопка «Синхронизация»). */
export function AnalyticsFilters({
  value,
  onChange,
  kinds = ['finance', 'postings', 'products'],
  showCompare = true,
  showProduct = true,
  showCategory = true,
  showGranularity = true,
  extra,
  extraCount = 0,
  onResetExtra,
}: {
  value: AnalyticsFilter
  onChange: (patch: Partial<AnalyticsFilter>) => void
  kinds?: string[]
  showCompare?: boolean
  /** ABC — итог за период, детализация по дням не нужна. */
  showGranularity?: boolean
  /** Главная — без товара и категории: там магазин целиком. */
  showProduct?: boolean
  showCategory?: boolean
  /** Свои фильтры страницы — в ту же панель «Фильтры» (виды событий, критерий ABC …), extraCount — сколько выбрано. */
  extra?: React.ReactNode
  extraCount?: number
  onResetExtra?: () => void
}) {
  const [categories, setCategories] = useState<CategoryOption[]>([])
  useEffect(() => {
    if (!showCategory) return
    api.get<CategoryOption[]>('/analytics/categories').then(({ data }) => setCategories(data)).catch(() => setCategories([]))
  }, [showCategory])

  const presets = PERIOD_PRESETS.filter((p) => p.value !== 'custom').map((p) => ({
    value: p.value,
    label: p.label,
    range: rangeOf(p.value as Exclude<PeriodPreset, 'custom'>),
  }))
  // в панели: категория, детализация, вкл. сравнения выключено — то, что отличается от умолчаний
  const count =
    (showCategory && value.category !== null ? 1 : 0) +
    (showGranularity && value.granularityChosen ? 1 : 0) +
    (showCompare && !value.compare ? 1 : 0) +
    extraCount
  const hasPanel = showCategory || showGranularity || showCompare || !!extra
  return (
    <section className="space-y-2" data-testid="analytics-filters">
      <FilterBar
        end={
          <>
            <UpdatedAt kinds={kinds} />
            {hasPanel && (
              <FilterButton
                count={count}
                onReset={() => {
                  onChange({ category: null, granularityChosen: false, compare: true })
                  onResetExtra?.()
                }}
              >
                {extra}
                {showCategory && (
                  <FilterField label="Категория">
                    <select
                      value={value.category ?? ''}
                      onChange={(e) => onChange({ category: e.target.value === '' ? null : Number(e.target.value) })}
                      aria-label="Категория"
                      className={selectClass}
                      data-testid="filter-category"
                    >
                      <option value="">Все категории</option>
                      {categories.map((c) => (
                        <option key={c.id} value={c.id} title={c.path ?? undefined}>
                          {c.name} ({c.products})
                        </option>
                      ))}
                    </select>
                  </FilterField>
                )}
                {showGranularity && (
                  <FilterField label="Детализация графика">
                    <ChipOptions
                      options={GRANULARITIES}
                      value={value.granularity}
                      onChange={(g) => onChange({ granularity: g, granularityChosen: true })}
                      testid="granularity"
                    />
                  </FilterField>
                )}
                {showCompare && (
                  <FilterField label="Сравнение">
                    <CheckOption
                      checked={value.compare}
                      onChange={(compare) => onChange({ compare })}
                      label="Сравнить с прошлым периодом"
                      hint="Прошлый сопоставимый период: месяц — прошлый месяц, неделя — прошлая неделя, иначе столько же дней перед периодом"
                      testid="filter-compare"
                    />
                  </FilterField>
                )}
              </FilterButton>
            )}
          </>
        }
      >
        {showProduct && <ProductPicker value={value.product} onChange={(product) => onChange({ product })} />}
        <DateRangePicker
          value={value.range}
          preset={value.preset === 'custom' ? null : value.preset}
          presets={presets}
          maxDays={MAX_DAYS}
          onChange={({ range, preset }) => (preset ? onChange({ preset: preset as PeriodPreset }) : onChange({ preset: 'custom', range }))}
        />
      </FilterBar>
    </section>
  )
}

/** Детализация графика — прямо над графиком (правка владельца: фильтры графика — у графика). */
export function GranularitySwitch({ value, onChange }: { value: Granularity; onChange: (g: Granularity) => void }) {
  return (
    <div className="flex gap-1" role="group" aria-label="Детализация графика" data-testid="chart-granularity">
      {GRANULARITIES.map((g) => (
        <button key={g.value} type="button" onClick={() => onChange(g.value)} className={chip(value === g.value)} aria-pressed={value === g.value} data-testid={`granularity-${g.value}`}>
          {g.label}
        </button>
      ))}
    </div>
  )
}

/** Пропуски данных периода одной строкой: дни без начислений и без загруженных отправлений — не ноль. */
export function CoverageNote({ data }: { data: Metrics }) {
  const parts = []
  if (data.coverage.data_source === 'none') parts.push('за период нет начислений — выручка, прибыль и продажи неизвестны')
  else if (data.coverage.missing_days) parts.push(`нет начислений за ${daysWord(data.coverage.missing_days)}`)
  if (data.coverage.orders_missing_days === data.coverage.days_total) parts.push('отправления не загружены — заказы неизвестны')
  else if (data.coverage.orders_missing_days) parts.push(`отправления не загружены за ${daysWord(data.coverage.orders_missing_days)}`)
  if (data.compare_note) parts.push(data.compare_note.replace(/\.$/, '').toLowerCase())
  if (!parts.length) return null
  const text = parts.join('; ')
  return (
    <p className="text-xs text-faint" data-testid="coverage-note">
      {text[0].toUpperCase() + text.slice(1)}. На графиках эти дни — точечным пунктиром: данных нет, это не ноль.
    </p>
  )
}

/** Событие на графике (10.8): метка у точки и строка в подсказке. */
export type ChartEvent = { day: string; kind: string; title: string }
/** Знак метки по виду события — над линией на графике. */
export const EVENT_MARK: Record<string, string> = { price: '₽', promo_in: '%', promo_out: '%', stockout: '0', restock: '↑' }
const TIP_EVENTS = 3

type Row = { label: string; value: number | null; prev: number | null; point: MetricPoint; events: ChartEvent[]; bridge: Record<number, number> }

function MetricTooltip({ active, payload, metric, data, drill }: { active?: boolean; payload?: { payload: Row }[]; metric: MetricKey; data: Metrics; drill: Drill<Row> }) {
  if (!active || !payload?.length) return null
  const { point, events } = payload[0].payload
  const meta = METRICS[metric]
  const g = data.granularity
  const missing = metric === 'orders' || metric === 'ordered_qty' || metric === 'ordered_amount' ? point.orders_missing_days : point.missing_days
  return (
    <TipBox href={drill.hrefFor(payload[0].payload)} touch={drill.touch}>
      <div className="mb-1 font-semibold">
        {pointTitle(point, g)}
        {point.partial && <span className="font-normal text-faint"> · неполная {g === 'week' ? 'неделя' : 'месяц'}</span>}
      </div>
      <div>
        {meta.label}: <b>{formatMetric(point[metric], meta.format)}</b>
      </div>
      {point.prev && (
        <div className="text-muted">
          {pointTitle(point.prev, g)}: {formatMetric(point.prev[metric], meta.format)}
        </div>
      )}
      {missing > 0 && <div className="text-faint">{missing === point.days ? 'нет данных' : `без данных — ${daysWord(missing)}`}</div>}
      {events.length > 0 && (
        <div className="mt-1.5 border-t border-line pt-1.5" data-testid="tip-events">
          {events.slice(0, TIP_EVENTS).map((e, i) => (
            <div key={i} className="text-warn">
              {EVENT_MARK[e.kind] ?? '•'} {e.title}
            </div>
          ))}
          {events.length > TIP_EVENTS && <div className="text-faint">и ещё {events.length - TIP_EVENTS} — список под графиком</div>}
        </div>
      )}
    </TipBox>
  )
}

/** График одного показателя с прошлым периодом пунктиром. Дни без данных — точечный пунктир между соседними точками (не ноль). pointHref — раздел за точку. */
const rangeText = (from: string, to: string) => rangeLabel({ from, to })

export function MetricChart({
  data,
  metric,
  title,
  pointHref,
  moreHref,
  events = [],
  toolbar,
  action,
}: {
  data: Metrics
  metric: MetricKey
  /** Под заголовком, над графиком: переключатель показателя (Segmented). */
  toolbar?: React.ReactNode
  /** Справа в заголовке, после «Подробнее →»: «Фильтры» графика. */
  action?: React.ReactNode
  title?: string
  pointHref?: (p: MetricPoint) => string | null
  moreHref?: string
  /** События периода — метки у точек, в которые они попали (день, неделя или месяц). */
  events?: ChartEvent[]
}) {
  const colors = useTokenColors()
  const drill = useDrill<Row>(pointHref ? (r) => (r.value === null ? null : pointHref(r.point)) : undefined)
  if (!colors) return null
  const meta = METRICS[metric]
  const rows: Row[] = data.points.map((p) => ({
    label: pointLabel(p, data.granularity),
    value: p[metric] === null ? null : Number(p[metric]),
    prev: p.prev?.[metric] == null ? null : Number(p.prev[metric]),
    point: p,
    events: events.filter((e) => p.date_from <= e.day && e.day <= p.date_to),
    bridge: {},
  }))
  // пропуски (решение владельца 12.11.2026): линия не рвётся — дни без данных проходят пунктиром от последней точки до
  // следующей; это не ноль и не данные — в подсказке «нет данных», в легенде «пунктир — нет данных»
  const gaps: number[] = []
  let last = -1
  rows.forEach((r, i) => {
    if (r.value === null) return
    if (last >= 0 && i - last > 1) {
      const k = gaps.length
      gaps.push(k)
      rows[last].bridge[k] = rows[last].value as number
      r.bridge[k] = r.value
    }
    last = i
  })
  const profitNote = metric === 'profit' && data.profit_kind === 'net' ? ' (в точках — до налога)' : ''
  // прибыль и маржа: красная линия «ноль» — ниже неё убыток (правка владельца), точки в минусе — красные
  const zeroLine = metric === 'profit' || metric === 'margin'
  // точка — где она что-то говорит: убыток, одиночный день между пропусками (иначе его не видно), короткий ряд
  const showDot = (i: number, v: number | null | undefined) =>
    rows.length <= 16 || (zeroLine && (v ?? 0) < 0) || ((rows[i - 1]?.value ?? null) === null && (rows[i + 1]?.value ?? null) === null)
  return (
    <section className="rounded-xl border border-line bg-card p-5 shadow-card" data-testid="metric-chart" data-metric={metric}>
      {/* дизайн-система 10.27a: заголовок панели и легенда слева, «Подробнее →» и «Фильтры» справа, показатель — под ними */}
      <div className="mb-3 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <h2 className="text-base font-semibold">
            {title ?? meta.label}
            {profitNote}
          </h2>
          <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-2 w-2 rounded-full bg-accent" /> {rangeText(data.date_from, data.date_to)}
            </span>
            {data.previous && (
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-0 w-5 border-t-2 border-dashed border-muted" /> прошлый период
              </span>
            )}
            {gaps.length > 0 && (
              <span className="flex items-center gap-1.5" data-testid="gap-legend">
                <span className="inline-block h-0 w-5 border-t-2 border-dotted border-accent/60" /> нет данных
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <MoreLink href={moreHref} />
          {action}
        </div>
      </div>
      {toolbar && <div className="mb-3">{toolbar}</div>}
      <div className={`h-64 ${drill.boxProps.className}`} onPointerDown={drill.boxProps.onPointerDown}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: events.length ? 16 : 8, right: 4, bottom: 0, left: 0 }} onClick={drill.onClick}>
            <CartesianGrid vertical={false} stroke={colors.line} />
            <XAxis dataKey="label" tick={{ fill: colors.faint, fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={8} />
            <YAxis tick={{ fill: colors.faint, fontSize: 11 }} tickFormatter={meta.format === 'pct' ? (v: number) => `${v} %` : compact} tickLine={false} axisLine={false} width={78} />
            <Tooltip content={<MetricTooltip metric={metric} data={data} drill={drill} />} cursor={{ stroke: colors.faint, strokeDasharray: '3 3' }} {...drill.tooltipProps} />
            {zeroLine && (
              <ReferenceLine
                y={0}
                stroke={colors.bad}
                strokeDasharray="4 3"
                ifOverflow="extendDomain"
                label={{ value: metric === 'margin' ? 'маржа 0 %' : 'ниже — убыток', position: 'insideBottomLeft', fill: colors.bad, fontSize: 10 }}
                data-testid="zero-line"
              />
            )}
            {gaps.map((k) => (
              <Line
                key={`gap-${k}`}
                type="linear"
                dataKey={(r: Row) => r.bridge[k] ?? null}
                stroke={colors.accent}
                strokeOpacity={0.55}
                strokeWidth={2}
                strokeDasharray="2 4"
                dot={false}
                activeDot={false}
                connectNulls
                isAnimationActive={false}
                legendType="none"
              />
            ))}
            <Line
              type="monotone"
              dataKey="value"
              stroke={colors.accent}
              strokeWidth={2.5}
              dot={(props: { cx?: number; cy?: number; value?: number | null; index?: number }) => (
                <circle
                  key={props.index}
                  cx={props.cx}
                  cy={props.cy}
                  r={zeroLine && (props.value ?? 0) < 0 ? 3 : 2.5}
                  fill={zeroLine && (props.value ?? 0) < 0 ? colors.bad : colors.accent}
                  display={props.value == null || props.cx == null || !showDot(props.index ?? 0, props.value) ? 'none' : undefined}
                />
              )}
              activeDot={{ r: 5, strokeWidth: 2, stroke: colors.card }}
              connectNulls={false}
              isAnimationActive={false}
            />
            {data.previous && (
              <Line type="monotone" dataKey="prev" stroke={colors.muted} strokeDasharray="4 3" strokeWidth={2} dot={false} activeDot={false} connectNulls isAnimationActive={false} />
            )}
            {rows
              .filter((r) => r.events.length > 0)
              .map((r) => (
                <ReferenceLine
                  key={r.label}
                  x={r.label}
                  stroke={colors.warn}
                  strokeDasharray="2 3"
                  className="event-mark"
                  label={{ value: Array.from(new Set(r.events.map((e) => EVENT_MARK[e.kind] ?? '•'))).join(''), position: 'top', fill: colors.warn, fontSize: 11 }}
                />
              ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}

/** Ошибка и загрузка — одинаково на всех страницах аналитики. */
export function MetricsState({ error, loading, empty }: { error: string | null; loading: boolean; empty: boolean }) {
  if (error) return <FormError message={error} />
  if (loading && empty) return <p className="text-sm text-muted">Загрузка…</p>
  return null
}

/** Вкладки раздела «Аналитика» (шаг 10.7) — пункты меню раздела по мере готовности. Подразделы с общим фильтром
 * открываются с последним выбранным фильтром — он сохраняется при переходе между ними. */
export function AnalyticsTabs() {
  const pathname = usePathname()
  const [query, setQuery] = useState('')
  useEffect(() => {
    const read = () => setQuery(rememberedFilter())
    read()
    window.addEventListener(FILTER_EVENT, read)
    return () => window.removeEventListener(FILTER_EVENT, read)
  }, [])
  const items = NAV.find((s) => s.label === 'Аналитика')?.items ?? []
  // на телефоне вкладки прокручиваются — текущая должна быть видна
  const nav = useRef<HTMLElement>(null)
  useEffect(() => {
    const el = nav.current?.querySelector<HTMLElement>('[aria-current=page]')
    if (nav.current && el) nav.current.scrollLeft = el.offsetLeft - (nav.current.clientWidth - el.offsetWidth) / 2
  }, [pathname])
  return (
    <nav ref={nav} className="flex gap-1 overflow-x-auto border-b border-line" aria-label="Аналитика" data-testid="analytics-tabs">
      {items.map((t) => {
        const active = t.href === '/dashboard/analytics' ? pathname === t.href : pathname.startsWith(t.href)
        const href = FILTER_PAGES.includes(t.href) && query ? `${t.href}?${query}` : t.href
        return (
          <Link
            key={t.href}
            href={href}
            aria-current={active ? 'page' : undefined}
            className={`-mb-px whitespace-nowrap border-b-2 px-4 py-2 text-sm ${active ? 'border-accent font-medium text-fg' : 'border-transparent text-muted hover:text-fg'}`}
            data-testid="analytics-tab"
          >
            {t.label}
          </Link>
        )
      })}
    </nav>
  )
}

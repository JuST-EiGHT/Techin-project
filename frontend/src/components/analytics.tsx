'use client'
// Основа аналитики (шаг 10.3): общие фильтры (период, товар, категория, детализация, сравнение, «обновлено …»), их
// состояние — в адресе страницы; загрузка показателей; график показателя через charts.tsx (правило 16). Страницы —
// главная (10.4), «Аналитика» (10.7 и дальше), «Товары», «Финансы» — берут их отсюда, а не пишут свои.
import { X } from 'lucide-react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { FormError } from '@/components/AuthForm'
import { type Drill, MoreLink, TipBox, useDrill } from '@/components/charts'
import { compact, useTokenColors } from '@/components/finance'
import { UpdatedAt } from '@/components/sync'
import { useDismiss } from '@/hooks/useDismiss'
import {
  type AnalyticsFilter,
  type CategoryOption,
  GRANULARITIES,
  METRICS,
  type MetricKey,
  type MetricPoint,
  type Metrics,
  PERIOD_PRESETS,
  type PeriodPreset,
  type ProductOption,
  autoGranularity,
  filterQuery,
  formatMetric,
  metricsParams,
  parseFilter,
  pointLabel,
  pointTitle,
  rangeOf,
} from '@/lib/analytics'
import { api, errorMessage } from '@/lib/api'
import { daysWord } from '@/lib/period'

/** Фильтр из адреса и его изменение: новый фильтр — новая запись истории, «назад» возвращает прежний. */
export function useAnalyticsFilter(defaultPreset?: PeriodPreset): [AnalyticsFilter, (patch: Partial<AnalyticsFilter>) => void] {
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const key = params.toString()
  const fromUrl = useMemo(() => parseFilter(new URLSearchParams(key), undefined, defaultPreset), [key, defaultPreset])
  // выбор виден сразу, адрес догоняет (иначе галочка и кнопки на миг «отскакивают» до перехода)
  const [filter, setFilter] = useState(fromUrl)
  useEffect(() => setFilter(fromUrl), [fromUrl])
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
      <span className="flex max-w-full items-center gap-1 rounded-lg border border-accent bg-accent/10 px-2.5 py-1 text-sm text-accent" data-testid="filter-product">
        <span className="truncate">{selected ? `${selected.offer_id}${selected.name ? ` · ${selected.name}` : ''}` : 'Товар'}</span>
        <button type="button" onClick={() => onChange(null)} aria-label="Все товары" className="shrink-0 hover:text-fg">
          <X className="h-4 w-4" />
        </button>
      </span>
    )
  }
  return (
    <div ref={box} className="relative">
      <input
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        placeholder="Все товары — найти артикул или название"
        aria-label="Товар"
        className={`${input} w-64 max-w-full`}
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

/** Общие фильтры аналитики. kinds — какие данные показывает страница: по ним «Обновлено …» (кнопка «Синхронизация»). */
export function AnalyticsFilters({
  value,
  onChange,
  kinds = ['finance', 'postings', 'products'],
  showCompare = true,
  showProduct = true,
  showCategory = true,
}: {
  value: AnalyticsFilter
  onChange: (patch: Partial<AnalyticsFilter>) => void
  kinds?: string[]
  showCompare?: boolean
  /** Главная — без товара и категории: там магазин целиком. */
  showProduct?: boolean
  showCategory?: boolean
}) {
  const [categories, setCategories] = useState<CategoryOption[]>([])
  useEffect(() => {
    if (!showCategory) return
    api.get<CategoryOption[]>('/analytics/categories').then(({ data }) => setCategories(data)).catch(() => setCategories([]))
  }, [showCategory])

  return (
    <section className="space-y-3 rounded-xl border border-line bg-card p-3 sm:p-4" data-testid="analytics-filters">
      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Период">
        {PERIOD_PRESETS.map((p) => (
          <button key={p.value} type="button" onClick={() => onChange({ preset: p.value as PeriodPreset })} className={chip(value.preset === p.value)} aria-pressed={value.preset === p.value} data-testid={`period-${p.value}`}>
            {p.label}
          </button>
        ))}
        {value.preset === 'custom' && (
          <span className="flex flex-wrap items-center gap-1.5">
            <input type="date" value={value.range.from} max={value.range.to} onChange={(e) => e.target.value && onChange({ range: { ...value.range, from: e.target.value } })} aria-label="С" className={input} data-testid="period-from" />
            <span className="text-muted">—</span>
            <input type="date" value={value.range.to} min={value.range.from} onChange={(e) => e.target.value && onChange({ range: { ...value.range, to: e.target.value } })} aria-label="По" className={input} data-testid="period-to" />
          </span>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {showProduct && <ProductPicker value={value.product} onChange={(product) => onChange({ product })} />}
        {showCategory && (
          <select
            value={value.category ?? ''}
            onChange={(e) => onChange({ category: e.target.value === '' ? null : Number(e.target.value) })}
            aria-label="Категория"
            className={`${input} max-w-[16rem]`}
            data-testid="filter-category"
          >
            <option value="">Все категории</option>
            {categories.map((c) => (
              <option key={c.id} value={c.id} title={c.path ?? undefined}>
                {c.name} ({c.products})
              </option>
            ))}
          </select>
        )}
        <div className="flex gap-1" role="group" aria-label="Детализация">
          {GRANULARITIES.map((g) => (
            <button key={g.value} type="button" onClick={() => onChange({ granularity: g.value, granularityChosen: true })} className={chip(value.granularity === g.value)} aria-pressed={value.granularity === g.value} data-testid={`granularity-${g.value}`}>
              {g.label}
            </button>
          ))}
        </div>
        {showCompare && (
          <label className="flex cursor-pointer items-center gap-2 text-sm text-muted" title="Прошлый сопоставимый период: месяц — прошлый месяц, неделя — прошлая неделя, иначе столько же дней перед периодом">
            <input type="checkbox" checked={value.compare} onChange={(e) => onChange({ compare: e.target.checked })} className="accent-accent" data-testid="filter-compare" />
            Сравнить с прошлым периодом
          </label>
        )}
        <UpdatedAt kinds={kinds} className="ml-auto" />
      </div>
    </section>
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
      {text[0].toUpperCase() + text.slice(1)}. На графиках это пропуски, а не ноль.
    </p>
  )
}

type Row = { label: string; value: number | null; prev: number | null; point: MetricPoint }

function MetricTooltip({ active, payload, metric, data, drill }: { active?: boolean; payload?: { payload: Row }[]; metric: MetricKey; data: Metrics; drill: Drill<Row> }) {
  if (!active || !payload?.length) return null
  const { point } = payload[0].payload
  const meta = METRICS[metric]
  const g = data.granularity
  const missing = metric === 'orders' || metric === 'ordered_qty' ? point.orders_missing_days : point.missing_days
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
    </TipBox>
  )
}

/** График одного показателя с прошлым периодом пунктиром. Дни без данных — разрыв линии. pointHref — раздел за точку. */
export function MetricChart({
  data,
  metric,
  title,
  pointHref,
  moreHref,
}: {
  data: Metrics
  metric: MetricKey
  title?: string
  pointHref?: (p: MetricPoint) => string | null
  moreHref?: string
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
  }))
  const profitNote = metric === 'profit' && data.profit_kind === 'net' ? ' (в точках — до налога)' : ''
  return (
    <section className="rounded-xl border border-line bg-card p-4" data-testid="metric-chart" data-metric={metric}>
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1">
        <h2 className="text-sm font-semibold">
          {title ?? meta.label}
          {profitNote}
        </h2>
        {data.previous && (
          <span className="flex items-center gap-1.5 text-xs text-muted">
            <span className="inline-block h-0 w-3 border-t-2 border-dashed border-muted" /> прошлый период
          </span>
        )}
        <MoreLink href={moreHref} />
      </div>
      <div className={`h-60 ${drill.boxProps.className}`} onPointerDown={drill.boxProps.onPointerDown}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: 0 }} onClick={drill.onClick}>
            <CartesianGrid vertical={false} stroke={colors.line} />
            <XAxis dataKey="label" tick={{ fill: colors.muted, fontSize: 11 }} tickLine={false} axisLine={{ stroke: colors.line }} minTickGap={8} />
            <YAxis tick={{ fill: colors.muted, fontSize: 11 }} tickFormatter={meta.format === 'pct' ? (v: number) => `${v} %` : compact} tickLine={false} axisLine={false} width={64} />
            <Tooltip content={<MetricTooltip metric={metric} data={data} drill={drill} />} cursor={{ stroke: colors.faint, strokeDasharray: '3 3' }} {...drill.tooltipProps} />
            <Line type="monotone" dataKey="value" stroke={colors.accent} strokeWidth={2} dot={{ r: 2, fill: colors.accent, strokeWidth: 0 }} activeDot={{ r: 4 }} connectNulls={false} isAnimationActive={false} />
            {data.previous && (
              <Line type="monotone" dataKey="prev" stroke={colors.muted} strokeDasharray="4 3" strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false} />
            )}
          </LineChart>
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

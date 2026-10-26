'use client'
// «Аналитика продаж» (шаг 10.8): показатели продаж к прошлому периоду, график с событиями (цена, акция, нет остатка,
// снова в наличии), список событий периода, таблица продаж по товарам с поиском, фильтрами, сортировкой и Excel.
// Показатели и фильтры — общие (components/analytics.tsx); таблица и события — GET /analytics/sales, /sales/events.
import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { type ChartEvent, EVENT_MARK, MetricChart } from '@/components/analytics'
import { Change, Hint } from '@/components/dashboard'
import { Card } from '@/components/home'
import { type AnalyticsFilter, METRICS, type MetricKey, type Metrics, formatMetric } from '@/lib/analytics'
import { api, errorMessage } from '@/lib/api'
import { saveFile } from '@/lib/download'

type Ref = { id: string | null; offer_id: string; name: string | null }
export type SalesRow = {
  product: Ref
  orders: number | null
  ordered_qty: number | null
  sold_qty: number | null
  returned_qty: number | null
  return_share: string | null
  revenue: string | null
  returns_amount: string | null
  avg_price: string | null
  prev_sold_qty: number | null
  expected: string | null
  change_pct: string | null
  direction: 'up' | 'down' | null
}
export type SalesTableData = {
  date_from: string
  date_to: string
  previous: { date_from: string; date_to: string }
  days: number
  prev_days: number
  orders_days: number
  compared: boolean
  items: SalesRow[]
  totals: { orders: number; ordered_qty: number; sold_qty: number; returned_qty: number; revenue: string; return_share: string | null }
}
export type SalesEvent = ChartEvent & { count: number; link: string; items: { product: Ref; text: string; link: string }[] }

const short = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`
const num = (v: string | number) => Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 1 })

function params(f: AnalyticsFilter) {
  return {
    date_from: f.range.from,
    date_to: f.range.to,
    ...(f.product ? { product_id: f.product } : {}),
    ...(f.category !== null ? { category_id: f.category } : {}),
  }
}

/** Таблица по товарам и события периода — по тем же фильтрам, что показатели. */
export function useSalesData(filter: AnalyticsFilter) {
  const [table, setTable] = useState<SalesTableData | null>(null)
  const [events, setEvents] = useState<SalesEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const { from, to } = filter.range
  useEffect(() => {
    let alive = true
    setError(null)
    const p = params(filter)
    Promise.all([api.get<SalesTableData>('/analytics/sales', { params: p }), api.get<SalesEvent[]>('/analytics/sales/events', { params: p })])
      .then(([t, e]) => {
        if (!alive) return
        setTable(t.data)
        setEvents(e.data)
      })
      .catch((err) => alive && setError(errorMessage(err)))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from, to, filter.product, filter.category])
  return { table, events, error }
}

/** Заказы, продано, возвраты, средняя цена — с изменением к прошлому периоду. */
export function SalesFigures({ data }: { data: Metrics }) {
  const t = data.totals
  const prev = data.previous?.totals ?? null
  const ch = data.changes
  const against = data.previous ? `к ${short(data.previous.date_from)}–${short(data.previous.date_to)}` : ''
  const was = (k: MetricKey) => (prev === null || prev[k] === null ? null : `было ${formatMetric(prev[k], METRICS[k].format)}`)
  const share = t.sold_qty ? (((t.returned_qty ?? 0) / t.sold_qty) * 100).toLocaleString('ru-RU', { maximumFractionDigits: 1 }) : null
  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line lg:grid-cols-4" data-testid="sales-figures">
      <Card href={null} label="Заказы" value={formatMetric(t.orders, 'count')} testid="figure-orders">
        {ch && <Change pct={ch.orders} against={against} />}
        <Hint>{t.orders === null ? 'отправления не загружены — нужен ключ Ozon' : (was('orders') ?? `${formatMetric(t.ordered_qty, 'qty')} в заказах`)}</Hint>
      </Card>
      <Card href={null} label="Продано, шт." value={formatMetric(t.sold_qty, 'qty')} testid="figure-sold_qty">
        {ch && <Change pct={ch.sold_qty} against={against} />}
        <Hint>{t.sold_qty === null ? 'нет данных за период' : (was('sold_qty') ?? 'по начислениям Ozon')}</Hint>
      </Card>
      <Card href={null} label="Возвраты, шт." value={formatMetric(t.returned_qty, 'qty')} testid="figure-returned_qty">
        {ch && <Change pct={ch.returned_qty} against={against} upIsBad />}
        <Hint>
          {t.returned_qty === null
            ? 'нет данных за период'
            : `${share !== null ? `${share} % от проданного · ` : ''}${formatMetric(t.returns_amount, 'rub')}`}
        </Hint>
      </Card>
      <Card href={null} label="Средняя цена" value={formatMetric(t.avg_price, 'rub')} testid="figure-avg_price">
        {ch && <Change pct={ch.avg_price} against={against} />}
        <Hint>{was('avg_price') ?? 'выручка / продано'}</Hint>
      </Card>
    </div>
  )
}

const CHART: { key: MetricKey; label: string; title: string }[] = [
  { key: 'orders', label: 'Заказы', title: 'Заказы — оформлены покупателями' },
  { key: 'sold_qty', label: 'Продано, шт.', title: 'Продано, шт. — начислены Ozon' },
  { key: 'returned_qty', label: 'Возвраты', title: 'Возвраты, шт.' },
  { key: 'avg_price', label: 'Средняя цена', title: 'Средняя цена' },
  { key: 'revenue', label: 'Выручка', title: 'Выручка' },
]

/** Один график с переключателем показателя и метками событий; точка — продажи за этот день, неделю или месяц. */
export function SalesChart({ data, events }: { data: Metrics; events: SalesEvent[] }) {
  const [metric, setMetric] = useState<MetricKey>('sold_qty')
  const current = CHART.find((c) => c.key === metric) ?? CHART[1]
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2" role="group" aria-label="Показатель на графике">
        {CHART.map((c) => (
          <button
            key={c.key}
            type="button"
            onClick={() => setMetric(c.key)}
            aria-pressed={metric === c.key}
            className={`rounded-lg border px-3 py-1 text-sm ${metric === c.key ? 'border-accent bg-accent/10 text-accent' : 'border-line text-muted hover:text-fg'}`}
            data-testid={`sales-metric-${c.key}`}
          >
            {c.label}
          </button>
        ))}
      </div>
      <MetricChart
        data={data}
        metric={metric}
        title={current.title}
        events={events}
        pointHref={(p) => `/dashboard/analytics/sales?period=custom&from=${p.date_from}&to=${p.date_to}`}
      />
    </div>
  )
}

const KIND_LABEL: Record<string, string> = { price: 'Цена', promo_in: 'Акция', promo_out: 'Акция', stockout: 'Нет остатка', restock: 'Поставка' }
const SHOWN = 8

/** События периода — то, что могло повлиять на продажи; каждое ведёт к товару, истории цен или акции. */
export function EventsList({ events }: { events: SalesEvent[] }) {
  const [all, setAll] = useState(false)
  const shown = all ? events : events.slice(0, SHOWN)
  return (
    <section className="rounded-xl border border-line bg-card" data-testid="sales-events">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line px-4 py-3">
        <h2 className="font-semibold">События периода</h2>
        <span className="text-xs text-muted">на графике: ₽ — цена, % — акция, 0 — нет остатка, ↑ — снова в наличии</span>
      </div>
      {events.length === 0 ? (
        <p className="px-4 py-3 text-sm text-muted" data-testid="sales-events-empty">
          Событий нет: цены не менялись, в акции не входили и не выходили, остатки не заканчивались.
        </p>
      ) : (
        <ul className="divide-y divide-line">
          {shown.map((e, i) => (
            <li key={`${e.day}-${e.kind}-${i}`} className="px-4 py-2.5 text-sm sm:flex sm:items-start sm:gap-3" data-testid="sales-event" data-kind={e.kind}>
              {/* на телефоне дата и вид — строкой над событием, шире — колонками слева */}
              <div className="flex shrink-0 items-baseline gap-3 sm:w-36">
                <span className="w-11 tabular-nums text-muted">{short(e.day)}</span>
                <span className="text-xs text-warn">
                  {EVENT_MARK[e.kind]} {KIND_LABEL[e.kind]}
                </span>
              </div>
              <div className="mt-0.5 min-w-0 flex-1 sm:mt-0">
                <Link href={e.link} className="hover:text-accent">
                  {e.title}
                </Link>
                {e.count > 1 && (
                  <p className="mt-0.5 text-xs text-faint">
                    {e.items.map((it, j) => (
                      <span key={j}>
                        {j > 0 && ', '}
                        <Link href={it.link} className="hover:text-accent">
                          {it.product.offer_id}
                        </Link>{' '}
                        {it.text}
                      </span>
                    ))}
                    {e.count > e.items.length && ` и ещё ${e.count - e.items.length}`}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {!all && events.length > SHOWN && (
        <button type="button" onClick={() => setAll(true)} className="block w-full border-t border-line px-4 py-2.5 text-left text-sm text-accent hover:underline" data-testid="sales-events-more">
          Показать все ({events.length})
        </button>
      )}
    </section>
  )
}

type SortKey = 'offer_id' | 'orders' | 'sold_qty' | 'returned_qty' | 'revenue' | 'avg_price' | 'change_pct'
type Quick = 'all' | 'returns' | 'up' | 'down'
const QUICK: { value: Quick; label: string }[] = [
  { value: 'all', label: 'Все' },
  { value: 'returns', label: 'С возвратами' },
  { value: 'up', label: 'Растут' },
  { value: 'down', label: 'Падают' },
]
const COLUMNS: { key: SortKey; label: string; right?: boolean }[] = [
  { key: 'offer_id', label: 'Товар' },
  { key: 'orders', label: 'Заказы', right: true },
  { key: 'sold_qty', label: 'Продано', right: true },
  { key: 'returned_qty', label: 'Возвраты', right: true },
  { key: 'revenue', label: 'Выручка', right: true },
  { key: 'avg_price', label: 'Ср. цена', right: true },
  { key: 'change_pct', label: 'К прошлому', right: true },
]

function value(r: SalesRow, key: SortKey): number | string | null {
  if (key === 'offer_id') return r.product.offer_id.toLowerCase()
  const v = r[key]
  return v === null ? null : Number(v)
}

function ChangeCell({ r }: { r: SalesRow }) {
  if (r.prev_sold_qty === null || r.expected === null) return <span className="text-faint">—</span>
  const tone = r.direction === 'up' ? 'text-ok' : r.direction === 'down' ? 'text-bad' : 'text-muted'
  const text =
    r.change_pct === null ? (r.direction === 'up' ? 'с нуля' : '—') : `${Number(r.change_pct) > 0 ? '+' : ''}${num(r.change_pct)} %`.replace('-', '−')
  return (
    <span className={tone} title={`было ${r.prev_sold_qty} шт., ожидали ${num(r.expected)} шт.${r.direction ? '' : ' — в пределах обычных колебаний'}`}>
      {r.direction === 'up' ? '▲ ' : r.direction === 'down' ? '▼ ' : ''}
      {text}
    </span>
  )
}

/** Продажи по товарам: поиск, быстрые фильтры, сортировка по колонкам, итог и Excel. */
export function SalesTable({ table, filter }: { table: SalesTableData; filter: AnalyticsFilter }) {
  const [search, setSearch] = useState('')
  const [quick, setQuick] = useState<Quick>('all')
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: 'revenue', desc: true })
  const [error, setError] = useState<string | null>(null)
  const counts: Record<Quick, number> = {
    all: table.items.length,
    returns: table.items.filter((r) => (r.returned_qty ?? 0) > 0).length,
    up: table.items.filter((r) => r.direction === 'up').length,
    down: table.items.filter((r) => r.direction === 'down').length,
  }
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    const picked = table.items.filter((r) => {
      if (q && !r.product.offer_id.toLowerCase().includes(q) && !(r.product.name ?? '').toLowerCase().includes(q)) return false
      if (quick === 'returns') return (r.returned_qty ?? 0) > 0
      if (quick === 'up' || quick === 'down') return r.direction === quick
      return true
    })
    // пустые значения — в конце в обе стороны
    const filled = picked.filter((r) => value(r, sort.key) !== null)
    const empty = picked.filter((r) => value(r, sort.key) === null)
    filled.sort((a, b) => {
      const x = value(a, sort.key) as number | string
      const y = value(b, sort.key) as number | string
      const c = x < y ? -1 : x > y ? 1 : a.product.offer_id.localeCompare(b.product.offer_id)
      return sort.desc ? -c : c
    })
    return [...filled, ...empty]
  }, [table.items, search, quick, sort])

  async function exportXlsx() {
    setError(null)
    try {
      await saveFile(api.get('/analytics/sales/export', { params: params(filter), responseType: 'blob' }), 'sales.xlsx')
    } catch (err) {
      setError(errorMessage(err, 'Не получилось выгрузить — попробуйте ещё раз'))
    }
  }

  const t = table.totals
  return (
    <section className="rounded-xl border border-line bg-card" data-testid="sales-table">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
        <h2 className="mr-2 font-semibold">Продажи по товарам</h2>
        {QUICK.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => setQuick(f.value)}
            aria-pressed={quick === f.value}
            className={`rounded-lg border px-2.5 py-1 text-xs ${quick === f.value ? 'border-accent bg-accent/10 text-accent' : 'border-line text-muted hover:text-fg'}`}
            data-testid={`sales-quick-${f.value}`}
          >
            {f.label} <span className="text-faint">{counts[f.value]}</span>
          </button>
        ))}
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Артикул или название"
          className="min-w-0 flex-1 basis-full rounded-lg border border-line bg-bg px-3 py-1 text-sm outline-none focus:border-accent sm:max-w-xs sm:basis-auto"
          data-testid="sales-search"
        />
        <button type="button" onClick={exportXlsx} className="rounded-lg border border-line px-3 py-1 text-sm hover:border-accent hover:text-accent" data-testid="sales-export">
          Excel
        </button>
      </div>
      <FormError message={error} />
      {!table.compared && (
        <p className="border-b border-line px-4 py-2 text-xs text-faint">
          «К прошлому» — когда в обоих периодах не меньше 7 дней с данными (сейчас {table.days}, тогда {table.prev_days}).
        </p>
      )}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-sm">
          <thead>
            <tr className="border-b border-line text-xs text-muted">
              {COLUMNS.map((c) => (
                <th key={c.key} className={`px-3 py-2 font-medium ${c.right ? 'text-right' : 'text-left'}`}>
                  <button
                    type="button"
                    onClick={() => setSort((s) => ({ key: c.key, desc: s.key === c.key ? !s.desc : c.key !== 'offer_id' }))}
                    className={`hover:text-fg ${sort.key === c.key ? 'text-fg' : ''}`}
                    data-testid={`sales-sort-${c.key}`}
                  >
                    {c.label}
                    {sort.key === c.key ? (sort.desc ? ' ↓' : ' ↑') : ''}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((r) => (
              <tr key={r.product.offer_id} data-testid="sales-row">
                <td className="max-w-[16rem] px-3 py-2">
                  {r.product.id ? (
                    <Link href={`/dashboard/products/${r.product.id}`} className="block truncate font-medium hover:text-accent">
                      {r.product.offer_id}
                    </Link>
                  ) : (
                    <span className="block truncate font-medium">{r.product.offer_id}</span>
                  )}
                  {r.product.name && <span className="block truncate text-xs text-faint">{r.product.name}</span>}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {r.orders === null ? <span className="text-faint">—</span> : r.orders}
                  {r.ordered_qty ? <span className="block text-xs text-faint">{r.ordered_qty} шт.</span> : null}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">{r.sold_qty ?? '—'}</td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {r.returned_qty ?? '—'}
                  {r.return_share !== null && Number(r.return_share) > 0 && <span className="block text-xs text-warn">{num(r.return_share)} %</span>}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">{formatMetric(r.revenue, 'rub')}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatMetric(r.avg_price, 'rub')}</td>
                <td className="px-3 py-2 text-right tabular-nums" data-testid="sales-change">
                  <ChangeCell r={r} />
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={COLUMNS.length} className="px-3 py-4 text-center text-sm text-muted">
                  Нет товаров по этому фильтру
                </td>
              </tr>
            )}
          </tbody>
          <tfoot>
            <tr className="border-t border-line font-medium" data-testid="sales-total">
              <td className="px-3 py-2">Итого</td>
              <td className="px-3 py-2 text-right tabular-nums">{t.orders}</td>
              <td className="px-3 py-2 text-right tabular-nums">{t.sold_qty}</td>
              <td className="px-3 py-2 text-right tabular-nums">
                {t.returned_qty}
                {t.return_share !== null && <span className="block text-xs font-normal text-faint">{num(t.return_share)} %</span>}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">{formatMetric(t.revenue, 'rub')}</td>
              <td colSpan={2} />
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  )
}

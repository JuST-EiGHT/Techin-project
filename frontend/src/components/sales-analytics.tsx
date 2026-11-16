'use client'
// «Аналитика продаж» (шаг 10.8): показатели продаж к прошлому периоду, график с событиями (цена, акция, нет остатка,
// снова в наличии), список событий периода, таблица продаж по товарам с поиском, фильтрами, сортировкой и Excel.
// Показатели и фильтры — общие (components/analytics.tsx); таблица и события — GET /analytics/sales, /sales/events.
import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'

import { CopyText } from '@/components/copy'
import { FormError } from '@/components/AuthForm'
import { ChipOptions, FilterBar, FilterButton, FilterField, SearchBox } from '@/components/filters'
import { SearchMiss } from '@/components/search-miss'
import { type ChartEvent, EVENT_MARK, MetricChart } from '@/components/analytics'
import { Change, Hint } from '@/components/dashboard'
import { Card } from '@/components/home'
import { SortTh, sortRows } from '@/components/table'
import { type AnalyticsFilter, METRICS, type MetricKey, type Metrics, formatMetric } from '@/lib/analytics'
import { api, errorMessage } from '@/lib/api'
import { saveFile } from '@/lib/download'

type Ref = { id: string | null; offer_id: string; name: string | null; sku?: string | null; barcode?: string | null }
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
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="sales-figures">
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
                        <Link href={it.link} className="inline-flex max-w-full min-w-0 align-bottom hover:text-accent">
                          <CopyText value={it.product.offer_id} label="артикул" />
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

/** Итог по строкам — как на сервере (`sales_analytics.table`): нет данных у строки — не прибавляем. */
function totalsOf(rows: SalesRow[]): SalesTableData['totals'] {
  const sum = (k: 'orders' | 'ordered_qty' | 'sold_qty' | 'returned_qty') => rows.reduce((a, r) => a + (r[k] ?? 0), 0)
  const sold = sum('sold_qty')
  const returned = sum('returned_qty')
  return {
    orders: sum('orders'),
    ordered_qty: sum('ordered_qty'),
    sold_qty: sold,
    returned_qty: returned,
    revenue: rows.reduce((a, r) => a + Number(r.revenue ?? 0), 0).toFixed(2),
    return_share: sold ? ((returned / sold) * 100).toFixed(1) : null,
  }
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
export function SalesTable({ table, filter, onPick }: {
  table: SalesTableData
  filter: AnalyticsFilter
  /** Найден один товар — показать его на карточках и графике страницы (общий фильтр товара). */
  onPick?: (id: string) => void
}) {
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
      // правка 16.11: как общий фильтр товара — артикул, название, SKU, штрихкод
      if (q && ![r.product.offer_id, r.product.name, r.product.sku, r.product.barcode].some((v) => (v ?? '').toLowerCase().includes(q))) return false
      if (quick === 'returns') return (r.returned_qty ?? 0) > 0
      if (quick === 'up' || quick === 'down') return r.direction === quick
      return true
    })
    // пустые значения — в конце в обе стороны (components/table.tsx)
    return sortRows(picked, sort.desc ? 'desc' : 'asc', (r) => value(r, sort.key))
  }, [table.items, search, quick, sort])

  async function exportXlsx() {
    setError(null)
    try {
      await saveFile(api.get('/analytics/sales/export', { params: params(filter), responseType: 'blob' }), 'sales.xlsx')
    } catch (err) {
      setError(errorMessage(err, 'Не получилось выгрузить — попробуйте ещё раз'))
    }
  }

  // правка 16.11: поиск или быстрый фильтр — «Итого» по найденным строкам, итог периода — подписью
  const narrowed = search.trim() !== '' || quick !== 'all'
  const t = narrowed ? totalsOf(rows) : table.totals
  const single = narrowed && rows.length === 1 && rows[0].product.id && !filter.product ? rows[0].product : null
  return (
    <section className="rounded-xl border border-line bg-card" data-testid="sales-table">
      <div className="space-y-2 border-b border-line px-4 py-3">
        <h2 className="font-semibold">Продажи по товарам</h2>
        <FilterBar
          end={
            <>
              <FilterButton count={quick !== 'all' ? 1 : 0} onReset={() => setQuick('all')} testid="sales-filters">
                <FilterField label="Показать">
                  <ChipOptions
                    options={QUICK.map((f) => ({ value: f.value, label: f.label, count: counts[f.value] }))}
                    value={quick}
                    onChange={setQuick}
                    testid="sales-quick"
                  />
                </FilterField>
              </FilterButton>
              <button type="button" onClick={exportXlsx} className="h-9 rounded-lg border border-line px-3 text-sm hover:border-accent hover:text-accent" data-testid="sales-export">
                Excel
              </button>
            </>
          }
        >
          <SearchBox value={search} onChange={setSearch} testid="sales-search" className="w-full sm:w-72" />
          {narrowed && (
            <span className="text-xs text-muted" data-testid="sales-found">
              найдено {rows.length} из {table.items.length}
              {single && onPick && (
                <>
                  {' · '}
                  <button type="button" onClick={() => onPick(single.id as string)} className="text-accent hover:underline" data-testid="sales-pick">
                    показать {single.offer_id} на графике и в цифрах →
                  </button>
                </>
              )}
            </span>
          )}
        </FilterBar>
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
                <SortTh
                  key={c.key}
                  active={sort.key === c.key ? (sort.desc ? 'desc' : 'asc') : null}
                  onSort={(dir) => setSort(dir ? { key: c.key, desc: dir === 'desc' } : { key: 'revenue', desc: true })}
                  resettable={c.key !== 'revenue'}
                  kind={c.key === 'offer_id' ? 'text' : 'number'}
                  align={c.right ? 'right' : 'left'}
                  className="px-3 py-2 font-medium"
                  testid={`sales-sort-${c.key}`}
                >
                  {c.label}
                </SortTh>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((r) => (
              <tr key={r.product.offer_id} data-testid="sales-row">
                <td className="max-w-[16rem] px-3 py-2">
                  {r.product.id ? (
                    <Link href={`/dashboard/products/${r.product.id}`} className="block min-w-0 font-medium hover:text-accent">
                      <CopyText value={r.product.offer_id} label="артикул" />
                    </Link>
                  ) : (
                    <CopyText value={r.product.offer_id} label="артикул" className="font-medium" />
                  )}
                  {r.product.name && <CopyText value={r.product.name} label="название" className="text-xs text-faint" />}
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
                <td colSpan={COLUMNS.length} className="px-3 py-4 text-center text-sm text-muted" data-testid="sales-none">
                  {search.trim() && quick === 'all' ? <SearchMiss search={search.trim()} what="продаж и заказов" /> : 'Нет товаров по этому фильтру'}
                </td>
              </tr>
            )}
          </tbody>
          <tfoot className={rows.length === 0 ? 'hidden' : ''}>
            <tr className="border-t border-line font-medium" data-testid="sales-total">
              <td className="px-3 py-2">
                {narrowed ? `Итого по найденным: ${rows.length} тов.` : 'Итого'}
                {narrowed && (
                  <span className="block whitespace-nowrap text-xs font-normal text-muted">
                    всего за период: {table.totals.sold_qty} шт., {formatMetric(table.totals.revenue, 'rub')}
                  </span>
                )}
              </td>
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

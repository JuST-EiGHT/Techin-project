'use client'
// Товары (шаг 4.1): все товары каталога Ozon — цена, комиссия, себестоимость, остаток, маржа, акции; боковая панель товара.
// «Все товары» (10.13): продано, выручка и прибыль за период, значки проблем, выбор товаров и групповые действия.
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { ProductPanel } from '@/components/product-panel'
import { BulkBar } from '@/components/products-bulk'
import { Badge, fmtDay, pctText, rub } from '@/components/reports'
import { refreshSyncStatus, timeAgo } from '@/lib/sync'
import { api, errorMessage } from '@/lib/api'
import { rangeOf, type PeriodPreset } from '@/lib/analytics'
import { saveFile } from '@/lib/download'
import { todayMsk } from '@/lib/period'
import {
  COLUMNS,
  DEFAULT_HIDDEN,
  FILTER_LABEL,
  FLAGS,
  MARGIN_TONE,
  PRICE_INDEX,
  QUICK_FILTERS,
  STOCK_STATUS,
  type ColumnKey,
  type ProductItem,
  type ProductsPage,
  type QuickFilter,
} from '@/lib/products'
import { useOnPriceSettingsChange, useSettings } from '@/lib/settings'

type Sort = 'offer_id' | 'name' | ColumnKey
type SyncStatus = {
  has_active_key: boolean
  products: { last_run: { status: string; error: string | null } | null; last_success_at: string | null }
}

const PAGE = 50
const POLL_MS = 3000
const ACTIVE = ['queued', 'running']
const COLUMNS_KEY = 'products.hidden-columns'
const ALL_FILTERS: QuickFilter[] = ['negative', 'low_stock', 'critical_stock', 'in_promo', 'no_cost']
type Period = Exclude<PeriodPreset, 'custom' | 'week' | '365d'>
// период для «Продано», «Выручка», «Прибыль»: по умолчанию 30 дней — в адресе не пишется
const PERIODS: { value: Period; label: string }[] = [
  { value: '7d', label: '7 дней' },
  { value: '30d', label: '30 дней' },
  { value: 'month', label: 'Этот месяц' },
  { value: 'prev_month', label: 'Прошлый месяц' },
  { value: '90d', label: '90 дней' },
]
const parsePeriod = (text: string | null): Period => PERIODS.find((p) => p.value === text)?.value ?? '30d'

function readHidden(): ColumnKey[] {
  try {
    const raw = window.localStorage.getItem(COLUMNS_KEY)
    const keys = raw ? (JSON.parse(raw) as string[]) : DEFAULT_HIDDEN
    return keys.filter((k): k is ColumnKey => COLUMNS.some((c) => c.key === k))
  } catch {
    return DEFAULT_HIDDEN
  }
}

function storeHidden(keys: ColumnKey[]) {
  try {
    window.localStorage.setItem(COLUMNS_KEY, JSON.stringify(keys))
  } catch {
    // нет доступа к хранилищу (приватный режим) — колонки просто не запомнятся
  }
}

const parseFilters = (text: string | null): QuickFilter[] =>
  (text ?? '').split(',').filter((f): f is QuickFilter => (ALL_FILTERS as string[]).includes(f))

export default function ProductsRoute() {
  return (
    <Suspense>
      <Products />
    </Suspense>
  )
}

function Products() {
  // фильтры и открытый товар — в адресе: на них можно сослаться с других страниц
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const filters = parseFilters(params.get('filter'))
  const selected = params.get('product')
  const period = parsePeriod(params.get('period'))
  const range = rangeOf(period)
  const periodParams = { date_from: range.from, date_to: range.to }
  const setUrl = useCallback(
    (next: { filter?: QuickFilter[]; product?: string | null; period?: Period }) => {
      const q = new URLSearchParams(params.toString())
      if (next.filter !== undefined) {
        if (next.filter.length) q.set('filter', next.filter.join(','))
        else q.delete('filter')
      }
      if (next.period !== undefined) {
        if (next.period !== '30d') q.set('period', next.period)
        else q.delete('period')
      }
      if (next.product !== undefined) {
        if (next.product) q.set('product', next.product)
        else q.delete('product')
      }
      const s = q.toString()
      router.replace(s ? `${pathname}?${s}` : pathname, { scroll: false })
    },
    [params, pathname, router],
  )

  const [searchText, setSearchText] = useState('')
  const [search, setSearch] = useState('')
  const [archived, setArchived] = useState(false)
  const [sort, setSort] = useState<Sort>('offer_id')
  const [order, setOrder] = useState<'asc' | 'desc'>('asc')
  const [offset, setOffset] = useState(0)
  const [hidden, setHidden] = useState<ColumnKey[]>(DEFAULT_HIDDEN)
  const [data, setData] = useState<ProductsPage | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  const [sync, setSync] = useState<SyncStatus | null>(null)
  const [starting, setStarting] = useState(false)
  // выбранные товары: сохраняются при сортировке, листании и смене фильтров, пока их не снимут
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const [selectingAll, setSelectingAll] = useState(false)
  const seq = useRef(0)
  const wasActive = useRef(false)
  const filterKey = filters.join(',')

  useEffect(() => setHidden(readHidden()), [])

  useEffect(() => {
    const t = setTimeout(() => {
      setSearch(searchText.trim())
      setOffset(0)
    }, 300)
    return () => clearTimeout(t)
  }, [searchText])

  useEffect(() => {
    const id = ++seq.current
    setBusy(true)
    api
      .get<ProductsPage>('/products', {
        params: {
          sort,
          order,
          limit: PAGE,
          offset,
          archived,
          date_from: range.from,
          date_to: range.to,
          ...(search ? { search } : {}),
          ...(filterKey ? { filters: filterKey } : {}),
        },
      })
      .then((r) => {
        if (id !== seq.current) return
        setData(r.data)
        setError(null)
      })
      .catch((err) => id === seq.current && setError(errorMessage(err)))
      .finally(() => id === seq.current && setBusy(false))
  }, [sort, order, offset, archived, search, filterKey, reload, range.from, range.to])

  const loadSync = useCallback(async () => {
    try {
      const { data: s } = await api.get<SyncStatus>('/sync/status')
      const nowActive = ACTIVE.includes(s.products.last_run?.status ?? '')
      if (wasActive.current && !nowActive) setReload((n) => n + 1) // синхронизация закончилась — перечитываем
      wasActive.current = nowActive
      setSync(s)
    } catch {
      // статус — подсказка; без него страница работает
    }
  }, [])

  useEffect(() => {
    loadSync()
  }, [loadSync])

  const syncing = ACTIVE.includes(sync?.products.last_run?.status ?? '')
  useEffect(() => {
    if (!syncing) return
    const t = setInterval(loadSync, POLL_MS)
    return () => clearInterval(t)
  }, [syncing, loadSync])

  async function syncNow() {
    setStarting(true)
    try {
      await api.post('/sync/products')
      refreshSyncStatus() // кнопка «Синхронизация» в шапке сразу покажет загрузку
    } catch (err) {
      if ((err as { response?: { status?: number } }).response?.status !== 409) setError(errorMessage(err))
    } finally {
      setStarting(false)
      await loadSync()
    }
  }

  function toggleFilter(f: QuickFilter, only = false) {
    const next = only ? (filters.length === 1 && filters[0] === f ? [] : [f]) : filters.includes(f) ? filters.filter((x) => x !== f) : [...filters, f]
    setOffset(0)
    setUrl({ filter: next })
  }

  function sortBy(s: Sort, first: 'asc' | 'desc') {
    if (s === sort) setOrder(order === 'desc' ? 'asc' : 'desc')
    else {
      setSort(s)
      setOrder(first)
    }
    setOffset(0)
  }

  function toggleColumn(key: ColumnKey) {
    const next = hidden.includes(key) ? hidden.filter((k) => k !== key) : [...hidden, key]
    setHidden(next)
    storeHidden(next)
  }

  async function exportCsv() {
    try {
      await saveFile(
        api.get('/products/export', {
          params: { sort, order, archived, ...periodParams, ...(search ? { search } : {}), ...(filterKey ? { filters: filterKey } : {}) },
          responseType: 'blob',
        }),
        'products.csv',
      )
    } catch (err) {
      setError(errorMessage(err, 'Не удалось скачать файл'))
    }
  }

  function toggleChosen(ids: string[], on: boolean) {
    const next = new Set(chosen)
    for (const id of ids) {
      if (on) next.add(id)
      else next.delete(id)
    }
    setChosen(next)
  }

  async function chooseAllByFilter() {
    setSelectingAll(true)
    try {
      const { data: found } = await api.get<{ ids: string[]; total: number }>('/products/ids', {
        params: { archived, ...(search ? { search } : {}), ...(filterKey ? { filters: filterKey } : {}) },
      })
      setChosen(new Set([...chosen, ...found.ids]))
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSelectingAll(false)
    }
  }

  const closePanel = useCallback(() => setUrl({ product: null }), [setUrl])
  const reloadList = useCallback(() => setReload((n) => n + 1), [])
  useOnPriceSettingsChange(reloadList) // маржа считается калькулятором цены
  const settings = useSettings() // пороги остатков (шаг 5.1) — для подсказок
  const lowDays = settings?.stock_low_days ?? 14
  const criticalDays = settings?.stock_critical_days ?? 7

  const lastSuccess = sync?.products.last_success_at
  const failed = sync?.products.last_run?.status === 'failed' ? sync.products.last_run.error : null
  const visible = COLUMNS.filter((c) => !hidden.includes(c.key))
  const c = data?.counts
  const noCatalog = data !== null && c?.all === 0 && c.archived === 0 && !search && filters.length === 0
  const pageIds = data?.items.map((r) => r.id) ?? []
  const pageChosen = pageIds.length > 0 && pageIds.every((id) => chosen.has(id))
  const allChosen = data !== null && data.total > 0 && pageChosen && chosen.size >= data.total && !busy

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h1 className="text-2xl font-semibold">Товары</h1>
        {sync?.has_active_key && (
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="text-muted" data-testid="products-synced">
              {syncing || starting ? 'Товары обновляются…' : lastSuccess ? `Обновлено ${timeAgo(lastSuccess)}` : 'Товары ещё не загружались'}
            </span>
            <button
              type="button"
              onClick={syncNow}
              disabled={syncing || starting}
              className="rounded-lg border border-line px-3 py-1.5 hover:border-accent hover:text-accent disabled:opacity-60"
            >
              Обновить
            </button>
          </div>
        )}
      </div>
      <FormError message={error ?? (failed ? `Последнее обновление не удалось: ${failed}` : null)} />

      {!data ? (
        !error && <p className="text-sm text-muted">Загрузка…</p>
      ) : noCatalog ? (
        <EmptyCatalog hasKey={sync?.has_active_key ?? null} />
      ) : (
        <div className={`space-y-4 transition-opacity ${busy ? 'opacity-60' : ''}`} aria-busy={busy}>
          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line lg:grid-cols-4" data-testid="products-counts">
            <Counter
              label={archived ? 'Товаров в архиве' : 'Всего товаров'}
              value={c!.all}
              hint={archived ? 'в архиве Ozon' : c!.archived ? `ещё ${c!.archived} в архиве` : 'в продаже'}
              active={filters.length === 0}
              onClick={() => setUrl({ filter: [] })}
              testid="count-all"
            />
            <Counter label="Без себестоимости" value={c!.no_cost} hint="маржу не посчитать" tone={c!.no_cost ? 'text-warn' : ''} active={filters.length === 1 && filters[0] === 'no_cost'} onClick={() => toggleFilter('no_cost', true)} testid="count-no-cost" />
            <Counter label="В акции" value={c!.in_promo} hint="в текущих акциях Ozon" active={filters.length === 1 && filters[0] === 'in_promo'} onClick={() => toggleFilter('in_promo', true)} testid="count-in-promo" />
            <Counter
              label="Критичный остаток"
              value={data.sales_known ? c!.critical_stock : null}
              hint={data.sales_known ? `меньше ${criticalDays} дн. запаса` : 'мало данных о продажах'}
              tone={c!.critical_stock ? 'text-bad' : ''}
              active={filters.length === 1 && filters[0] === 'critical_stock'}
              onClick={() => toggleFilter('critical_stock', true)}
              testid="count-critical"
            />
          </div>

          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Быстрые фильтры">
            {QUICK_FILTERS.map((f) => {
              const on = filters.includes(f.value)
              return (
                <button
                  key={f.value}
                  type="button"
                  title={f.value === 'low_stock' ? `Запаса меньше чем на ${lowDays} дн. продаж` : f.hint}
                  aria-pressed={on}
                  onClick={() => toggleFilter(f.value)}
                  className={`rounded-full border px-3 py-1 text-sm ${on ? 'border-accent bg-accent text-white' : 'border-line bg-card text-muted hover:border-accent hover:text-fg'}`}
                  data-testid={`filter-${f.value}`}
                >
                  {f.label} <span className={on ? '' : 'text-faint'}>{c![f.count]}</span>
                </button>
              )
            })}
            {filters.includes('critical_stock') && (
              <button type="button" aria-pressed onClick={() => toggleFilter('critical_stock')} className="rounded-full border border-accent bg-accent px-3 py-1 text-sm text-white">
                {FILTER_LABEL.critical_stock} ✕
              </button>
            )}
            {filters.length > 0 && (
              <button type="button" onClick={() => setUrl({ filter: [] })} className="px-2 text-sm text-muted hover:text-fg" data-testid="filters-reset">
                Сбросить
              </button>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <input
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="Артикул, название или SKU"
              aria-label="Поиск товара"
              className="w-full rounded-lg border border-line bg-card px-3 py-2 text-sm outline-none focus:border-accent sm:max-w-xs"
              data-testid="products-search"
            />
            <span className="text-xs text-muted" data-testid="products-found">
              {search || filters.length ? `найдено ${data.total}` : `${data.total} товаров`}
            </span>
            <label className="flex items-center gap-2 text-sm text-muted" title="За какой период показать «Продано», «Выручку» и «Прибыль»">
              Продажи за
              <select
                value={period}
                onChange={(e) => setUrl({ period: e.target.value as Period })}
                className="rounded-lg border border-line bg-card px-2 py-1.5 text-sm text-fg outline-none focus:border-accent"
                data-testid="products-period"
              >
                {PERIODS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-2 text-sm text-muted">
              <input
                type="checkbox"
                checked={archived}
                onChange={(e) => {
                  setArchived(e.target.checked)
                  setOffset(0)
                }}
                data-testid="products-archived"
              />
              Архив Ozon{c!.archived ? ` (${c!.archived})` : ''}
            </label>
            <div className="ml-auto flex gap-2">
              <ColumnsMenu hidden={hidden} onToggle={toggleColumn} />
              <button type="button" onClick={exportCsv} className="rounded-lg border border-line px-3 py-2 text-sm hover:border-accent hover:text-accent" data-testid="products-export">
                Скачать CSV
              </button>
            </div>
          </div>

          {chosen.size > 0 && (
            <BulkBar
              ids={[...chosen]}
              period={periodParams}
              onClear={() => setChosen(new Set())}
              onDone={reloadList}
              selectAll={
                !allChosen && data.total > 0 ? (
                  <button type="button" onClick={chooseAllByFilter} disabled={selectingAll} className="text-sm text-accent hover:underline disabled:opacity-60" data-testid="select-all-filter">
                    {search || filters.length ? `Выбрать все ${data.total} по фильтру` : `Выбрать все ${data.total}`}
                  </button>
                ) : null
              }
            />
          )}

          {data.total === 0 ? (
            <section className="rounded-xl border border-line bg-card p-6 text-sm text-muted" data-testid="products-nothing">
              {search ? `По запросу «${search}»` : 'Под выбранные фильтры'} товаров нет.{' '}
              {(filters.length > 0 || search) && (
                <button
                  type="button"
                  onClick={() => {
                    setSearchText('')
                    setUrl({ filter: [] })
                  }}
                  className="text-accent hover:underline"
                >
                  Показать все
                </button>
              )}
            </section>
          ) : (
            <section className="overflow-hidden rounded-xl border border-line bg-card">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[56rem] text-[13px]" data-testid="products-table">
                  <thead className="border-b border-line text-left text-xs text-muted">
                    <tr>
                      <th className="w-8 py-2 pl-3 pr-0">
                        <input
                          type="checkbox"
                          checked={pageChosen}
                          onChange={(e) => toggleChosen(pageIds, e.target.checked)}
                          aria-label="Выбрать товары на этой странице"
                          title="Выбрать товары на этой странице"
                          data-testid="select-page"
                        />
                      </th>
                      <SortTh s="offer_id" first="asc" sort={sort} order={order} onSort={sortBy} left>
                        Товар
                      </SortTh>
                      {visible.map((col) => (
                        <SortTh key={col.key} s={col.key} first={col.first} sort={sort} order={order} onSort={sortBy} title={col.hint}>
                          {col.label}
                        </SortTh>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((r) => (
                      <tr
                        key={r.id}
                        onClick={() => setUrl({ product: r.id })}
                        className={`cursor-pointer border-b border-line last:border-b-0 hover:bg-line/40 ${selected === r.id || chosen.has(r.id) ? 'bg-accent/10' : ''}`}
                        data-testid="product-row"
                      >
                        <td className="py-2 pl-3 pr-0 align-top" onClick={(e) => e.stopPropagation()}>
                          <input
                            type="checkbox"
                            checked={chosen.has(r.id)}
                            onChange={(e) => toggleChosen([r.id], e.target.checked)}
                            aria-label={`Выбрать ${r.offer_id}`}
                            className="mt-0.5"
                            data-testid="select-row"
                          />
                        </td>
                        <td className="max-w-[15rem] px-3 py-2">
                          <div className="flex items-center gap-1.5">
                            <span className="truncate font-medium">{r.offer_id}</span>
                            <Flags flags={r.flags} />
                          </div>
                          <div className="truncate text-xs text-muted">
                            {r.name ?? 'Без названия'}
                            {r.ozon_sku && <span className="text-faint"> · {r.ozon_sku}</span>}
                          </div>
                        </td>
                        {visible.map((col) => (
                          <Cell key={col.key} col={col.key} r={r} />
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {data.total > PAGE && (
                <div className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-2 text-sm">
                  <span className="text-muted">
                    {data.offset + 1}–{Math.min(data.offset + PAGE, data.total)} из {data.total}
                  </span>
                  <button type="button" disabled={data.offset === 0} onClick={() => setOffset(Math.max(0, data.offset - PAGE))} className="ml-auto rounded-md border border-line px-3 py-1 disabled:opacity-40" data-testid="page-prev">
                    ← Назад
                  </button>
                  <button type="button" disabled={data.offset + PAGE >= data.total} onClick={() => setOffset(data.offset + PAGE)} className="rounded-md border border-line px-3 py-1 disabled:opacity-40" data-testid="page-next">
                    Дальше →
                  </button>
                </div>
              )}
            </section>
          )}

          <div className="space-y-1 text-xs text-faint" data-testid="products-notes">
            <p data-testid="period-note">
              Продано, выручка и прибыль — за {fmtDay(data.period.date_from)} – {fmtDay(data.period.date_to)} по начислениям Ozon, как в
              «Прибыли по товарам»; прибыль до налога.
              {data.period.data_source === 'none'
                ? ' За этот период начислений нет — «—», а не ноль.'
                : data.period.missing_days > 0 && ` Дней без начислений: ${data.period.missing_days} из ${data.period.days_total} — цифры могут быть меньше настоящих.`}{' '}
              Значки у артикула — убыток, низкая маржа, мало остатков, нет себестоимости, недавно менялась цена; наведите — подскажут подробнее.
            </p>
            <p>{data.margin_note}</p>
            <p>
              Дни запаса — остаток на всех складах, делённый на продажи в день за {data.sales_window_days} дней.
              {!data.sales_known && ' Пока мало данных о продажах: нужны начисления хотя бы за 7 из 30 дней — синхронизация Ozon или отчёты по начислениям.'}
              {!data.stocks_known && ' Остатки ещё не загружались.'}
            </p>
          </div>
        </div>
      )}

      {selected && <ProductPanel productId={selected} onClose={closePanel} onChanged={reloadList} />}
    </div>
  )
}

function Counter({
  label,
  value,
  hint,
  tone = '',
  active,
  onClick,
  testid,
}: {
  label: string
  value: number | null
  hint: string
  tone?: string
  active: boolean
  onClick: () => void
  testid: string
}) {
  return (
    <button type="button" onClick={onClick} aria-pressed={active} className={`bg-card px-4 py-3 text-left hover:bg-line/40 ${active ? 'ring-2 ring-inset ring-accent' : ''}`} data-testid={testid}>
      <div className="text-[11px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`mt-1 text-lg font-bold tabular-nums ${tone}`}>{value === null ? '—' : value.toLocaleString('ru-RU')}</div>
      <div className="mt-0.5 text-xs text-faint">{hint}</div>
    </button>
  )
}

function SortTh({
  s,
  first,
  sort,
  order,
  onSort,
  left,
  title,
  children,
}: {
  s: Sort
  first: 'asc' | 'desc'
  sort: Sort
  order: 'asc' | 'desc'
  onSort: (s: Sort, first: 'asc' | 'desc') => void
  left?: boolean
  title?: string
  children: React.ReactNode
}) {
  const on = sort === s
  return (
    <th className={`whitespace-nowrap px-3 py-2 font-normal ${left ? '' : 'text-right'}`} aria-sort={on ? (order === 'desc' ? 'descending' : 'ascending') : undefined} title={title}>
      <button type="button" onClick={() => onSort(s, first)} className={`hover:text-fg ${on ? 'text-fg' : ''}`} data-testid={`sort-${s}`}>
        {children} {on ? (order === 'desc' ? '↓' : '↑') : <span className="text-faint">↕</span>}
      </button>
    </th>
  )
}

const td = 'whitespace-nowrap px-3 py-2 text-right tabular-nums'
/** «14.10» в этом году, «14.10.25» — в прошлые годы. */
const shortDay = (iso: string) => (iso.slice(0, 4) === todayMsk().slice(0, 4) ? fmtDay(iso).slice(0, 5) : `${fmtDay(iso).slice(0, 6)}${iso.slice(2, 4)}`)
const sub = 'text-[11px] text-faint'

function Cell({ col, r }: { col: ColumnKey; r: ProductItem }) {
  switch (col) {
    case 'price':
      return (
        <td className={td}>
          {rub(r.price)}
          {r.old_price && <div className={`${sub} line-through`}>{rub(r.old_price)}</div>}
        </td>
      )
    case 'min_price':
      return <td className={`${td} text-muted`}>{rub(r.min_price)}</td>
    case 'sold':
      return (
        <td className={td} title={r.sold_qty === null ? 'За период нет начислений' : undefined} data-testid="sold-cell">
          {r.sold_qty === null ? <span className="text-faint">—</span> : r.sold_qty.toLocaleString('ru-RU')}
        </td>
      )
    case 'revenue':
      return <td className={`${td} ${r.revenue === null ? 'text-faint' : ''}`}>{rub(r.revenue)}</td>
    case 'profit': {
      const n = r.period_profit === null ? null : Number(r.period_profit)
      return (
        <td
          className={`${td} ${n === null ? 'text-faint' : n < 0 ? 'text-bad' : ''}`}
          title={n === null && r.sold_qty !== null ? 'Нет себестоимости — прибыль не посчитать' : undefined}
          data-testid="profit-cell"
        >
          {rub(r.period_profit)}
        </td>
      )
    }
    case 'commission':
      return (
        <td className={td}>
          {pctText(r.commission_pct)}
          {r.commission && <div className={sub}>{rub(r.commission)}</div>}
        </td>
      )
    case 'cost':
      return <td className={`${td} ${r.cost_price === null ? 'text-warn' : ''}`}>{r.cost_price === null ? 'нет' : rub(r.cost_price)}</td>
    case 'stock':
      return (
        <td className={td}>
          {r.stock === null ? '—' : r.stock.toLocaleString('ru-RU')}
          {r.in_transit ? <div className={sub}>+{r.in_transit} в пути</div> : null}
        </td>
      )
    case 'days': {
      const status = r.stock_status ? STOCK_STATUS[r.stock_status] : null
      return (
        <td className={`${td} ${status?.tone ?? 'text-muted'}`} data-testid="days-cell">
          {r.days_of_supply === null ? (status ? '—' : 'нет данных') : Number(r.days_of_supply).toLocaleString('ru-RU')}
          {status && <div className="text-[11px]">{status.label}</div>}
        </td>
      )
    }
    case 'margin':
      return (
        <td className={`${td} font-medium ${r.margin_level ? MARGIN_TONE[r.margin_level] : 'text-muted'}`} data-testid="margin-cell">
          {pctText(r.margin)}
          {r.unit_profit && <div className="text-[11px] font-normal">{rub(r.unit_profit)} с шт.</div>}
        </td>
      )
    case 'index': {
      const idx = r.price_index ? PRICE_INDEX[r.price_index] : null
      return (
        <td className={td} title={idx?.hint}>
          {idx ? (
            <span className="inline-flex items-center gap-1.5">
              <span className={`inline-block h-2 w-2 rounded-full ${idx.dot}`} />
              {idx.label}
            </span>
          ) : (
            <span className="text-faint">—</span>
          )}
        </td>
      )
    }
    case 'promo':
      return (
        <td className={`${td} max-w-[9rem]`} title={r.promotions.map((p) => p.title).join(', ')}>
          {r.promotions.length === 0 ? (
            <span className="text-faint">—</span>
          ) : (
            <>
              <div className="truncate">{r.promotions[0].title}</div>
              <div className={sub}>
                {r.promotions.length > 1 && `ещё ${r.promotions.length - 1} · `}
                {r.promotions.some((p) => p.added_automatically) && (
                  <Badge tone="warn" title="Ozon добавил товар в акцию без вас">
                    авто
                  </Badge>
                )}
              </div>
            </>
          )}
        </td>
      )
    case 'last_sale':
      return (
        <td className={`${td} text-muted`} title={r.last_sale ? fmtDay(r.last_sale) : 'Продаж в загруженных начислениях нет'}>
          {r.last_sale ? shortDay(r.last_sale) : '—'}
        </td>
      )
  }
}

/** Значки проблем у артикула: цвет — важность, подробности — в подсказке. */
function Flags({ flags }: { flags: ProductItem['flags'] }) {
  if (!flags.length) return null
  return (
    <span className="flex shrink-0 gap-0.5" data-testid="row-flags">
      {flags.map((f) => {
        const meta = FLAGS[f.code]
        return (
          <span
            key={f.code}
            title={f.text}
            aria-label={`${meta.label}: ${f.text}`}
            role="img"
            className={`inline-flex h-4 min-w-4 items-center justify-center rounded px-0.5 text-[10px] font-bold leading-none ${meta.tone}`}
            data-testid={`flag-${f.code}`}
          >
            {meta.mark}
          </span>
        )
      })}
    </span>
  )
}

function ColumnsMenu({ hidden, onToggle }: { hidden: ColumnKey[]; onToggle: (k: ColumnKey) => void }) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => !box.current?.contains(e.target as Node) && setOpen(false)
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('pointerdown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('pointerdown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])
  return (
    <div className="relative" ref={box}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="rounded-lg border border-line px-3 py-2 text-sm hover:border-accent hover:text-accent"
        data-testid="columns-button"
      >
        Колонки ▾
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-1 w-64 rounded-lg border border-line bg-card p-2 shadow-lg" data-testid="columns-menu">
          <p className="px-2 pb-1 text-xs text-faint">Артикул и название видны всегда. Выбор запоминается в этом браузере.</p>
          {COLUMNS.map((col) => (
            <label key={col.key} className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-line/40" title={col.hint}>
              <input type="checkbox" className="mt-0.5" checked={!hidden.includes(col.key)} onChange={() => onToggle(col.key)} data-testid={`column-${col.key}`} />
              <span>
                {col.label}
                <span className="block text-xs text-faint">{col.hint}</span>
              </span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

function EmptyCatalog({ hasKey }: { hasKey: boolean | null }) {
  return (
    <section className="rounded-xl border border-line bg-card p-6" data-testid="products-empty">
      <h2 className="font-semibold">Товаров пока нет</h2>
      {hasKey === false ? (
        <p className="mt-2 text-sm text-muted">
          Товары, цены, комиссии и остатки загружаются из Ozon по API-ключу — раз в час, сами.{' '}
          <Link href="/dashboard/settings/integrations" className="text-accent hover:underline">
            Подключить ключ →
          </Link>
        </p>
      ) : (
        <p className="mt-2 text-sm text-muted">Ключ Ozon подключён, но товары ещё не загрузились — нажмите «Обновить» вверху или подождите до часа.</p>
      )}
      <p className="mt-2 text-sm text-muted">
        Себестоимость можно указать заранее —{' '}
        <Link href="/dashboard/products/costs" className="text-accent hover:underline">
          в справочнике себестоимости
        </Link>
        .
      </p>
    </section>
  )
}

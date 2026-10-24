'use client'
// «Остатки» — главная таблица (шаг 10.17): по товарам, складам или кластерам; доступно, резерв, в пути, продажи в день,
// дни запаса, дата окончания, стоимость запаса по себестоимости (нет себестоимости — «нет данных», не 0), статус.
// Быстрые фильтры, настраиваемые колонки (запоминаются в браузере), сортировка по колонкам, Excel и прежний CSV.
// Склад, статус и группировка — в адресе страницы (?warehouse=&status=&group=): на них ссылаются главная и карточки.
import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { fmtDay, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { saveFile } from '@/lib/download'
import {
  STATUS,
  STATUS_ORDER,
  type StockGroup,
  type StockTableData,
  type StockTableRow,
  type TableSort,
  type TableStatus,
  type Thresholds,
  type WarehouseSummary,
  num,
  perDay,
  productHref,
  statusHint,
  warehouseLabel,
} from '@/lib/stocks'

type ColumnKey = Exclude<TableSort, 'offer_id' | 'available'> | 'unit_cost' | 'status'
type Column = { key: ColumnKey; label: string; hint: string; sort?: TableSort; first?: 'asc' | 'desc' }

const COLUMNS: Column[] = [
  { key: 'reserved', label: 'Резерв', hint: 'Под заказы, ещё не отгружено (у складов FBO Ozon не отдаёт)', sort: 'reserved', first: 'desc' },
  { key: 'in_transit', label: 'В пути', hint: 'Ожидается к поступлению — поставки в пути на склады Ozon', sort: 'in_transit', first: 'desc' },
  { key: 'avg_daily_sales', label: 'Продажи/день', hint: 'По товару в целом: продано за вычетом возвратов за 30 дней / дней с данными', sort: 'avg_daily_sales', first: 'desc' },
  { key: 'days_of_supply', label: 'Дней запаса', hint: 'Весь остаток товара на всех складах / продажи в день', sort: 'days_of_supply', first: 'asc' },
  { key: 'end_date', label: 'Закончится', hint: 'Сегодня + дни запаса; не продаётся — даты нет', sort: 'end_date', first: 'asc' },
  { key: 'unit_cost', label: 'Себестоимость', hint: 'С НДС поставщика, на сегодня — из справочника себестоимости' },
  { key: 'stock_value', label: 'Стоимость запаса', hint: 'Доступно × себестоимость с НДС; нет себестоимости — «нет данных»', sort: 'stock_value', first: 'desc' },
  { key: 'last_sale', label: 'Последняя продажа', hint: 'Последний день с продажей товара — на любом складе', sort: 'last_sale', first: 'desc' },
  { key: 'status', label: 'Статус', hint: 'На сколько дней продаж хватит остатка — по порогам из «Настроек»' },
]
const DEFAULT_HIDDEN: ColumnKey[] = ['unit_cost']
const COLUMNS_KEY = 'stocks.hidden-columns'
const GROUPS: { value: StockGroup; label: string }[] = [
  { value: 'product', label: 'По товарам' },
  { value: 'warehouse', label: 'По складам' },
  { value: 'cluster', label: 'По кластерам' },
]
export const TABLE_STATUSES: TableStatus[] = [...STATUS_ORDER, 'no_sales']
const NO_SALES_HINT = 'за 30 дней продаж нет, а товар лежит'
const SEARCH_DEBOUNCE_MS = 300

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

const statusLabel = (s: TableStatus) => (s === 'no_sales' ? 'Нет продаж' : STATUS[s].label)
const statusTitle = (s: TableStatus, t: Thresholds) => (s === 'no_sales' ? NO_SALES_HINT : statusHint(s, t))
const plural = (n: number, one: string, few: string, many: string) =>
  n % 10 === 1 && n % 100 !== 11 ? one : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? few : many

function daysFrom(today: string, iso: string) {
  return Math.round((Date.parse(iso) - Date.parse(today)) / 86_400_000)
}

/** «28.10 · через 4 дн.», «кончился», «не продаётся», «—». */
function EndDate({ r, today }: { r: StockTableRow; today: string }) {
  if (r.end_date === null) return <span className="text-faint">{r.sells === false ? 'не продаётся' : '—'}</span>
  const days = daysFrom(today, r.end_date)
  if (days <= 0) return <span className="text-bad">{r.product_available === 0 ? 'кончился' : 'сегодня'}</span>
  return (
    <>
      {fmtDay(r.end_date)}
      <span className="block text-xs text-faint">через {days} дн.</span>
    </>
  )
}

function Where({ r, onWarehouse }: { r: StockTableRow; onWarehouse: (id: string) => void }) {
  if (r.warehouse)
    return (
      <>
        <button type="button" onClick={() => onWarehouse(r.warehouse!.id)} className="text-left hover:text-accent" title="Показать только этот склад" data-testid="row-warehouse">
          {r.warehouse.name}
        </button>
        <div className="text-xs text-muted">
          {r.warehouse.type}
          {r.warehouse.cluster_name ? ` · ${r.warehouse.cluster_name}` : ''}
        </div>
      </>
    )
  if (r.cluster)
    return (
      <>
        <div>{r.cluster}</div>
        <div className="text-xs text-muted">{r.cluster_type === 'FBO' ? `кластер Ozon · складов ${r.places}` : `свой склад (${r.cluster_type})`}</div>
      </>
    )
  return <span className="whitespace-nowrap text-muted">складов: {r.places}</span>
}

export function StockTablePanel({
  warehouses,
  warehouseId,
  status,
  group,
  reloadKey,
  onUrl,
}: {
  warehouses: WarehouseSummary[]
  warehouseId: string
  status: TableStatus | ''
  group: StockGroup
  reloadKey: number
  onUrl: (next: { warehouse?: string; status?: string; group?: string }) => void
}) {
  const [data, setData] = useState<StockTableData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sort, setSort] = useState<TableSort>('offer_id')
  const [order, setOrder] = useState<'asc' | 'desc'>('asc')
  const [includeEmpty, setIncludeEmpty] = useState(false)
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [hidden, setHidden] = useState<ColumnKey[]>(DEFAULT_HIDDEN)

  useEffect(() => setHidden(readHidden()), [])
  useEffect(() => {
    const t = setTimeout(() => setQuery(search.trim()), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [search])

  const params = useCallback(() => {
    const q: Record<string, string | boolean> = { group, sort, order }
    if (warehouseId) q.warehouse_id = warehouseId
    if (status) q.status = status
    if (query) q.search = query
    if (includeEmpty) q.include_empty = true
    return q
  }, [group, sort, order, warehouseId, status, query, includeEmpty])

  useEffect(() => {
    let alive = true
    api
      .get<StockTableData>('/stocks/table', { params: params() })
      .then(({ data: d }) => alive && (setData(d), setError(null)))
      .catch((err) => alive && setError(errorMessage(err)))
    return () => {
      alive = false
    }
  }, [params, reloadKey])

  function sortBy(s: TableSort, first: 'asc' | 'desc') {
    if (s === sort) setOrder(order === 'asc' ? 'desc' : 'asc')
    else {
      setSort(s)
      setOrder(first)
    }
  }

  function toggle(k: ColumnKey) {
    const next = hidden.includes(k) ? hidden.filter((h) => h !== k) : [...hidden, k]
    setHidden(next)
    storeHidden(next)
  }

  async function exportXlsx() {
    setError(null)
    try {
      await saveFile(api.get('/stocks/table/export', { params: params(), responseType: 'blob' }), 'stocks.xlsx')
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  async function exportCsv() {
    setError(null)
    try {
      const q: Record<string, string | boolean> = {}
      if (warehouseId) q.warehouse_id = warehouseId
      if (status && status !== 'no_sales') q.status = status
      if (includeEmpty) q.include_empty = true
      await saveFile(api.get('/stocks/export', { params: q, responseType: 'blob' }), 'stocks.csv')
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const t = data?.thresholds
  const shown = COLUMNS.filter((c) => !hidden.includes(c.key))
  const selected = warehouses.find((w) => w.id === warehouseId)
  const totals = data?.totals

  return (
    <>
      <div className="space-y-3 border-b border-line p-4">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex w-full overflow-hidden rounded-lg border border-line text-sm sm:w-auto" role="group" aria-label="Строка таблицы" data-testid="stock-group">
            {GROUPS.map((g) => (
              <button
                key={g.value}
                type="button"
                aria-pressed={group === g.value}
                onClick={() => onUrl({ group: g.value === 'product' ? '' : g.value })}
                className={`flex-1 whitespace-nowrap px-2.5 py-1.5 sm:flex-none sm:px-3 ${group === g.value ? 'bg-accent text-white' : 'hover:bg-bg'}`}
                data-testid={`group-${g.value}`}
              >
                {g.label}
              </button>
            ))}
          </div>
          <select
            aria-label="Склад"
            value={warehouseId}
            onChange={(e) => onUrl({ warehouse: e.target.value })}
            className="min-w-0 max-w-full flex-1 rounded-lg border border-line bg-bg px-3 py-1.5 text-sm sm:flex-none"
            data-testid="filter-warehouse"
          >
            <option value="">Все склады</option>
            {warehouses.map((w) => (
              <option key={w.id} value={w.id}>
                {warehouseLabel(w)}
                {w.is_active ? '' : ' — неактивен'}
              </option>
            ))}
          </select>
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Артикул, название, SKU"
            aria-label="Поиск"
            className="min-w-0 flex-1 rounded-lg border border-line bg-bg px-3 py-1.5 text-sm outline-none focus:border-accent sm:max-w-xs"
            data-testid="stock-search"
          />
        </div>
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Быстрые фильтры" data-testid="stock-filters">
          <button
            type="button"
            aria-pressed={!status}
            onClick={() => onUrl({ status: '' })}
            className={`rounded-full border px-3 py-1 text-sm ${!status ? 'border-accent bg-accent/10 text-accent' : 'border-line hover:border-muted'}`}
          >
            Все
          </button>
          {t &&
            TABLE_STATUSES.map((s) => (
              <button
                key={s}
                type="button"
                aria-pressed={status === s}
                title={statusTitle(s, t)}
                onClick={() => onUrl({ status: status === s ? '' : s })}
                className={`rounded-full border px-3 py-1 text-sm ${status === s ? 'border-accent bg-accent/10 text-accent' : 'border-line hover:border-muted'}`}
                data-testid={`quick-${s}`}
              >
                {statusLabel(s)}
              </button>
            ))}
          <label className="ml-1 flex items-center gap-2 text-sm text-muted">
            <input type="checkbox" checked={includeEmpty} onChange={(e) => setIncludeEmpty(e.target.checked)} />
            Нулевые остатки
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          {totals && (
            <p className="text-sm text-muted" data-testid="stock-totals">
              {totals.rows === totals.products ? `Товаров: ${totals.products}` : `Строк: ${totals.rows} · товаров ${totals.products}`} ·{' '}
              {totals.available.toLocaleString('ru-RU')} шт.
              {totals.in_transit > 0 && ` · в пути ${totals.in_transit.toLocaleString('ru-RU')}`} · стоимость запаса{' '}
              <span className="font-medium text-fg" data-testid="stock-value-total">
                {totals.stock_value === null ? 'нет данных' : rub(totals.stock_value)}
              </span>
              {totals.without_cost > 0 && (
                <>
                  {' '}
                  <span className="text-warn">
                    (без {totals.without_cost} {plural(totals.without_cost, 'товара', 'товаров', 'товаров')} — нет себестоимости,{' '}
                    <Link href="/dashboard/products/costs" className="underline">
                      заполнить
                    </Link>
                    )
                  </span>
                </>
              )}
            </p>
          )}
          <div className="flex items-center gap-2 sm:ml-auto">
            <ColumnsMenu hidden={hidden} onToggle={toggle} />
            <button
              type="button"
              onClick={exportXlsx}
              disabled={!data?.rows.length}
              className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent disabled:opacity-60"
              data-testid="export-xlsx"
            >
              Excel
            </button>
            <button
              type="button"
              onClick={exportCsv}
              disabled={!data?.rows.length}
              className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent disabled:opacity-60"
              data-testid="export-csv"
            >
              CSV
            </button>
          </div>
        </div>
      </div>
      {selected && (
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2 text-sm" data-testid="table-warehouse">
          <span className="text-muted">Склад:</span>
          <span className="font-medium">{selected.name}</span>
          <span className="text-faint">
            {selected.type}
            {selected.cluster_name ? ` · ${selected.cluster_name}` : ''}
          </span>
          <button type="button" onClick={() => onUrl({ warehouse: '', status: '' })} className="ml-auto text-accent hover:underline">
            ✕ Все склады
          </button>
        </div>
      )}
      <FormError message={error} />
      <Rows data={data} columns={shown} sort={sort} order={order} onSort={sortBy} filtered={Boolean(warehouseId || status || query)} onWarehouse={(id) => onUrl({ warehouse: id })} />
      {data && data.rows.length > 0 && (
        <p className="border-t border-line px-4 py-2 text-xs text-faint">
          Продажи в день, дни запаса и дата окончания — по товару в целом (по всем складам): склада в начислениях Ozon нет.
          Стоимость запаса — по себестоимости с НДС на сегодня; без себестоимости не считаем её нулём.
        </p>
      )}
    </>
  )
}

function Rows({
  data,
  columns,
  sort,
  order,
  onSort,
  filtered,
  onWarehouse,
}: {
  data: StockTableData | null
  columns: Column[]
  sort: TableSort
  order: 'asc' | 'desc'
  onSort: (s: TableSort, first: 'asc' | 'desc') => void
  filtered: boolean
  onWarehouse: (id: string) => void
}) {
  if (data === null) return <p className="p-4 text-sm text-muted">Загрузка…</p>
  if (data.rows.length === 0) return <p className="p-4 text-sm text-muted">Остатков нет{filtered ? ' по выбранным фильтрам' : ''}.</p>
  const on = (k: ColumnKey) => columns.some((c) => c.key === k)
  const key = (r: StockTableRow) => `${r.product.id}-${r.warehouse?.id ?? r.cluster ?? ''}`
  return (
    <>
      <ul className="divide-y divide-line sm:hidden" data-testid="stock-cards">
        {data.rows.map((r) => {
          const st = STATUS[r.status ?? 'no_data']
          return (
            <li key={key(r)} className={`border-l-4 px-4 py-2 ${st.row}`} data-testid="stock-card">
              <div className="flex items-start gap-2">
                <Link href={productHref(r.product.id)} className="min-w-0 flex-1 truncate font-medium">
                  {r.product.offer_id}
                </Link>
                <span className={`shrink-0 rounded px-2 py-0.5 text-xs ${st.badge}`}>{st.label}</span>
              </div>
              {r.product.name && <div className="truncate text-xs text-muted">{r.product.name}</div>}
              {data.group !== 'product' && (
                <div className="mt-0.5 text-xs text-faint">
                  {r.warehouse ? `${r.warehouse.name} · ${r.warehouse.type}` : r.cluster}
                </div>
              )}
              <div className="mt-1 flex flex-wrap gap-x-3 text-sm">
                <span>
                  <span className="text-muted">доступно</span> <span className="font-medium tabular-nums">{r.available}</span>
                </span>
                {r.in_transit ? <span className="text-muted">в пути {r.in_transit}</span> : null}
                {r.reserved ? <span className="text-muted">резерв {r.reserved}</span> : null}
                <span className="text-muted">{perDay(r.avg_daily_sales)} в день</span>
                {r.days_of_supply !== null && <span className="text-muted">запас {num(r.days_of_supply)} дн.</span>}
                {r.end_date && <span className="text-muted">до {fmtDay(r.end_date)}</span>}
                <span className={r.stock_value === null ? 'text-faint' : 'text-muted'}>
                  {r.stock_value === null ? 'стоимость — нет данных' : rub(r.stock_value)}
                </span>
              </div>
            </li>
          )
        })}
      </ul>
      <div className="hidden overflow-x-auto sm:block">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted">
            <tr className="border-b border-line">
              <SortTh s="offer_id" first="asc" sort={sort} order={order} onSort={onSort} left>
                Товар
              </SortTh>
              <th className="px-3 py-2 font-normal">{data.group === 'cluster' ? 'Кластер' : 'Склад'}</th>
              <SortTh s="available" first="desc" sort={sort} order={order} onSort={onSort} title="Доступно к продаже здесь">
                Доступно
              </SortTh>
              {columns.map((c) =>
                c.sort ? (
                  <SortTh key={c.key} s={c.sort} first={c.first ?? 'desc'} sort={sort} order={order} onSort={onSort} title={c.hint} left={c.key === 'last_sale'}>
                    {c.label}
                  </SortTh>
                ) : (
                  <th key={c.key} className={`whitespace-nowrap px-3 py-2 font-normal ${c.key === 'status' ? '' : 'text-right'}`} title={c.hint}>
                    {c.label}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => {
              const st = STATUS[r.status ?? 'no_data']
              return (
                <tr key={key(r)} data-testid="stock-row" data-status={r.status ?? 'none'} className={`border-b border-l-4 border-b-line last:border-b-0 ${st.row}`}>
                  <td className="px-3 py-2">
                    <Link href={productHref(r.product.id)} className="font-medium hover:text-accent" data-testid="row-product">
                      {r.product.offer_id}
                    </Link>
                    {r.product.name && <div className="max-w-[14rem] truncate text-xs text-muted">{r.product.name}</div>}
                  </td>
                  <td className="px-3 py-2">
                    <Where r={r} onWarehouse={onWarehouse} />
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {r.available}
                    {data.group !== 'product' && r.product_available !== r.available && (
                      <div className="text-xs text-faint">из {r.product_available}</div>
                    )}
                  </td>
                  {on('reserved') && <td className={td}>{r.reserved ?? '—'}</td>}
                  {on('in_transit') && <td className={td}>{r.in_transit ?? '—'}</td>}
                  {on('avg_daily_sales') && <td className={td}>{perDay(r.avg_daily_sales)}</td>}
                  {on('days_of_supply') && <td className={td}>{num(r.days_of_supply)}</td>}
                  {on('end_date') && (
                    <td className={td} data-testid="cell-end-date">
                      <EndDate r={r} today={data.today} />
                    </td>
                  )}
                  {on('unit_cost') && <td className={td}>{r.unit_cost === null ? <span className="text-faint">нет</span> : rub(r.unit_cost)}</td>}
                  {on('stock_value') && (
                    <td className={td} data-testid="cell-stock-value">
                      {r.stock_value !== null ? (
                        rub(r.stock_value)
                      ) : r.available > 0 ? (
                        <Link href="/dashboard/products/costs" className="text-faint hover:text-accent" title="Нет себестоимости — заполните справочник">
                          нет данных
                        </Link>
                      ) : (
                        '—'
                      )}
                    </td>
                  )}
                  {on('last_sale') && (
                    <td className="whitespace-nowrap px-3 py-2">
                      {r.last_sale ? fmtDay(r.last_sale) : <span className="text-faint">нет в данных</span>}
                      {r.days_without_sales !== null && r.days_without_sales > 0 && <div className="text-xs text-faint">{r.days_without_sales} дн. назад</div>}
                    </td>
                  )}
                  {on('status') && (
                    <td className="px-3 py-2">
                      <span className={`whitespace-nowrap rounded px-2 py-0.5 text-xs ${st.badge}`}>{st.label}</span>
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}

const td = 'whitespace-nowrap px-3 py-2 text-right tabular-nums'

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
  s: TableSort
  first: 'asc' | 'desc'
  sort: TableSort
  order: 'asc' | 'desc'
  onSort: (s: TableSort, first: 'asc' | 'desc') => void
  left?: boolean
  title?: string
  children: React.ReactNode
}) {
  const active = sort === s
  return (
    <th className={`whitespace-nowrap px-3 py-2 font-normal ${left ? '' : 'text-right'}`} aria-sort={active ? (order === 'desc' ? 'descending' : 'ascending') : undefined} title={title}>
      <button type="button" onClick={() => onSort(s, first)} className={`hover:text-fg ${active ? 'text-fg' : ''}`} data-testid={`sort-${s}`}>
        {children} {active ? (order === 'desc' ? '↓' : '↑') : <span className="text-faint">↕</span>}
      </button>
    </th>
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
    <div className="relative hidden sm:block" ref={box}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent"
        data-testid="columns-button"
      >
        Колонки ▾
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-1 w-72 rounded-lg border border-line bg-card p-2 shadow-lg" data-testid="columns-menu">
          <p className="px-2 pb-1 text-xs text-faint">Товар, склад и «Доступно» видны всегда. Выбор запоминается в этом браузере.</p>
          {COLUMNS.map((col) => (
            <label key={col.key} className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-line/40">
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

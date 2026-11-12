'use client'
// «Остатки» — главная таблица (шаг 10.17): по товарам, складам или кластерам; доступно, резерв, в пути, продажи в день,
// дни запаса, дата окончания, стоимость запаса по себестоимости (нет себестоимости — «нет данных», не 0), статус.
// Быстрые фильтры, настраиваемые колонки (запоминаются в браузере), сортировка по колонкам, Excel и прежний CSV.
// Склад, статус и группировка — в адресе страницы (?warehouse=&status=&group=): на них ссылаются главная и карточки.
import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { CheckOption, ChipOptions, FilterButton, FilterField, SearchBox, selectClass } from '@/components/filters'
import { fmtDay, rub } from '@/components/reports'
import { HelpTip, Segmented, Skeleton, Sub, buttonClass } from '@/components/ui'
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
      <div className="space-y-3 border-b border-line p-4" data-testid="stock-toolbar">
        <div className="flex flex-wrap items-center gap-2">
          {/* 10.27c — вид, как «Товары / Кластеры» в Ozon; по складам и кластерам — дерево */}
          <Segmented
            label="Строка таблицы"
            value={group}
            onChange={(v) => onUrl({ group: v === 'product' ? '' : v })}
            options={GROUPS}
            testid="stock-group"
            optionTestid={(v) => `group-${v}`}
          />
          <SearchBox value={search} onChange={setSearch} placeholder="Артикул, название, штрихкод или SKU" testid="stock-search" className="min-w-0 flex-1 sm:max-w-xs" />
          {/* правка 11.11: склад, статус и «нулевые» — в «Фильтрах» */}
          <div className="ml-auto">
            <FilterButton
              count={(warehouseId ? 1 : 0) + (status ? 1 : 0) + (includeEmpty ? 1 : 0)}
              onReset={() => {
                onUrl({ warehouse: '', status: '' })
                setIncludeEmpty(false)
              }}
              testid="stock-filter"
            >
              <FilterField label="Склад">
                <select aria-label="Склад" value={warehouseId} onChange={(e) => onUrl({ warehouse: e.target.value })} className={selectClass} data-testid="filter-warehouse">
                  <option value="">Все склады</option>
                  {warehouses.map((w) => (
                    <option key={w.id} value={w.id}>
                      {warehouseLabel(w)}
                      {w.is_active ? '' : ' — неактивен'}
                    </option>
                  ))}
                </select>
              </FilterField>
              {t && (
                <FilterField label="Статус">
                  <div data-testid="stock-filters">
                    <ChipOptions
                      options={[{ value: '', label: 'Все' }, ...TABLE_STATUSES.map((st) => ({ value: st as string, label: statusLabel(st), hint: statusTitle(st, t) }))]}
                      value={status ?? ''}
                      onChange={(v) => onUrl({ status: v })}
                      testid="quick"
                    />
                  </div>
                </FilterField>
              )}
              <FilterField label="Строки">
                <CheckOption checked={includeEmpty} onChange={setIncludeEmpty} label="Показывать нулевые остатки" testid="stock-include-empty" />
              </FilterField>
            </FilterButton>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          {totals && (
            <p className="text-xs text-muted" data-testid="stock-totals">
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
            <span className="flex items-center gap-1.5 text-xs text-muted" data-testid="stock-note">
              Как считаем
              <HelpTip label="Как считаются продажи, запас и стоимость">
                Продажи в день, дни запаса и дата окончания — по товару в целом (по всем складам): склада в начислениях Ozon нет.
                Стоимость запаса — по себестоимости с НДС на сегодня; без себестоимости не считаем её нулём. У кластера и склада —
                сумма; «≥» — у части товаров нет себестоимости.
              </HelpTip>
            </span>
            <ColumnsMenu hidden={hidden} onToggle={toggle} />
            <button
              type="button"
              onClick={exportXlsx}
              disabled={!data?.rows.length}
              className={buttonClass('secondary', 'sm')}
              data-testid="export-xlsx"
            >
              Excel
            </button>
            <button
              type="button"
              onClick={exportCsv}
              disabled={!data?.rows.length}
              className={buttonClass('secondary', 'sm')}
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
    </>
  )
}

type TreeNode = { key: string; title: string; sub: string; rows: StockTableRow[]; children: TreeNode[] | null; onPick?: () => void }

/** Дерево, как «Управление остатками» в Ozon: по складам — кластер → склад → товар, по кластерам — кластер → товар. */
function buildTree(data: StockTableData, onWarehouse: (id: string) => void): TreeNode[] {
  const order: string[] = []
  const byCluster = new Map<string, StockTableRow[]>()
  for (const r of data.rows) {
    const c = data.group === 'cluster' ? r.cluster ?? '—' : r.warehouse?.cluster_name ?? (r.warehouse?.type && r.warehouse.type !== 'FBO' ? 'Свои склады' : 'Без кластера')
    if (!byCluster.has(c)) {
      byCluster.set(c, [])
      order.push(c)
    }
    byCluster.get(c)!.push(r)
  }
  return order.map((c) => {
    const rows = byCluster.get(c)!
    if (data.group === 'cluster') {
      const r0 = rows[0]
      const sub = r0.cluster_type === 'FBO' ? `кластер Ozon · складов ${r0.places}` : `свой склад (${r0.cluster_type})`
      return { key: c, title: c, sub: `${sub} · товаров ${rows.length}`, rows, children: null }
    }
    const wOrder: string[] = []
    const byW = new Map<string, StockTableRow[]>()
    for (const r of rows) {
      const id = r.warehouse?.id ?? '—'
      if (!byW.has(id)) {
        byW.set(id, [])
        wOrder.push(id)
      }
      byW.get(id)!.push(r)
    }
    const children = wOrder.map((id) => {
      const wr = byW.get(id)!
      const w = wr[0].warehouse
      return { key: `${c}/${id}`, title: w?.name ?? '—', sub: `${w?.type ?? ''} · товаров ${wr.length}`, rows: wr, children: null, onPick: w ? () => onWarehouse(w.id) : undefined }
    })
    return { key: c, title: c, sub: `складов ${children.length} · товаров ${new Set(rows.map((r) => r.product.id)).size}`, rows, children }
  })
}

/** Сумма по группе; у всех строк «нет данных» (резерв FBO) — null, а не 0. */
const sum = (rows: StockTableRow[], f: (r: StockTableRow) => number | null) =>
  rows.some((r) => f(r) !== null) ? rows.reduce((n, r) => n + (f(r) ?? 0), 0) : null
const fmtSum = (v: number | null) => (v === null ? '—' : v.toLocaleString('ru-RU'))
/** Стоимость группы: сумма известных; если у части нет себестоимости — «≥ …», ничего не известно — null (не 0). */
function groupValue(rows: StockTableRow[]): { value: number | null; partial: boolean } {
  const known = rows.filter((r) => r.stock_value !== null)
  const missing = rows.some((r) => r.stock_value === null && r.available > 0)
  return { value: known.length ? known.reduce((n, r) => n + Number(r.stock_value), 0) : null, partial: missing && known.length > 0 }
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
  const [open, setOpen] = useState<Set<string>>(new Set())
  const group = data?.group
  useEffect(() => setOpen(new Set()), [group])
  if (data === null)
    return (
      <div className="space-y-2 p-4">
        <Skeleton className="h-8" />
        <Skeleton className="h-8" />
        <Skeleton className="h-8" />
      </div>
    )
  if (data.rows.length === 0) return <p className="p-4 text-sm text-muted">Остатков нет{filtered ? ' по выбранным фильтрам' : ''}.</p>
  const on = (k: ColumnKey) => columns.some((c) => c.key === k)
  const key = (r: StockTableRow) => `${r.product.id}-${r.warehouse?.id ?? r.cluster ?? ''}`
  const tree = data.group === 'product' ? null : buildTree(data, onWarehouse)
  // немного строк или фильтр — сразу раскрыто; иначе свёрнуто, как в Ozon
  const autoOpen = filtered || data.rows.length <= 25
  const isOpen = (k: string) => autoOpen !== open.has(k)
  const flip = (k: string) =>
    setOpen((o) => {
      const next = new Set(o)
      if (next.has(k)) next.delete(k)
      else next.add(k)
      return next
    })

  const productCells = (r: StockTableRow) => {
    const st = STATUS[r.status ?? 'no_data']
    return (
      <>
        <td className="px-3 py-2">
          <span className={`whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${st.badge}`}>{st.label}</span>
          {r.days_of_supply !== null && <Sub>на {num(r.days_of_supply)} дн.</Sub>}
        </td>
        <td className="px-3 py-2 text-right tabular-nums">
          {r.available}
          {data.group !== 'product' && r.product_available !== r.available && <Sub>из {r.product_available}</Sub>}
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
            {r.days_without_sales !== null && r.days_without_sales > 0 && <Sub>{r.days_without_sales} дн. назад</Sub>}
          </td>
        )}
      </>
    )
  }
  const sumCells = (rows: StockTableRow[], strong = false) => {
    // итоговая строка — по итогам API (точнее: там все строки, а не только показанные)
    const v = strong && data.totals ? { value: data.totals.stock_value === null ? null : Number(data.totals.stock_value), partial: data.totals.without_cost > 0 && data.totals.stock_value !== null } : groupValue(rows)
    const cls = `${td} ${strong ? 'font-semibold' : 'font-medium'}`
    return (
      <>
        <td className="px-3 py-2" />
        <td className={cls}>{fmtSum(strong ? data.totals.available : sum(rows, (r) => r.available))}</td>
        {on('reserved') && <td className={cls}>{fmtSum(sum(rows, (r) => r.reserved))}</td>}
        {on('in_transit') && <td className={cls}>{fmtSum(strong ? data.totals.in_transit : sum(rows, (r) => r.in_transit))}</td>}
        {on('avg_daily_sales') && <td className={td} />}
        {on('days_of_supply') && <td className={td} />}
        {on('end_date') && <td className={td} />}
        {on('unit_cost') && <td className={td} />}
        {on('stock_value') && (
          <td className={cls}>
            {v.value === null ? <span className="font-normal text-faint">нет данных</span> : `${v.partial ? '≥ ' : ''}${rub(String(v.value))}`}
          </td>
        )}
        {on('last_sale') && <td className={td} />}
      </>
    )
  }
  const productRow = (r: StockTableRow, indent: number) => (
    <tr key={key(r)} data-testid="stock-row" data-status={r.status ?? 'none'} className={r.status === 'critical' ? 'bg-bad/5' : undefined}>
      <td className="py-2 pr-3" style={{ paddingLeft: `${0.75 + indent * 1.25}rem` }}>
        <Link href={productHref(r.product.id)} className="font-medium hover:text-accent" data-testid="row-product">
          {r.product.offer_id}
        </Link>
        {r.product.name && <div className="max-w-[16rem] truncate text-xs text-faint">{r.product.name}</div>}
        {data.group === 'product' && <Where r={r} onWarehouse={onWarehouse} />}
      </td>
      {productCells(r)}
    </tr>
  )
  const groupRow = (n: TreeNode, depth: number) => (
    <tr key={n.key} className={depth === 0 ? 'bg-soft/40' : undefined} data-testid={depth === 0 ? 'stock-cluster' : 'stock-subgroup'}>
      <td className="py-2 pr-3" style={{ paddingLeft: `${0.75 + depth * 1.25}rem` }}>
        <div className="flex items-start gap-2">
          <button type="button" onClick={() => flip(n.key)} aria-expanded={isOpen(n.key)} className="mt-0.5 w-3 text-accent" aria-label={isOpen(n.key) ? 'Свернуть' : 'Развернуть'} data-testid="stock-group-toggle">
            {isOpen(n.key) ? '▾' : '▸'}
          </button>
          <div>
            {n.onPick ? (
              <button type="button" onClick={n.onPick} className="text-left font-medium hover:text-accent" title="Показать только этот склад" data-testid="row-warehouse">
                {n.title}
              </button>
            ) : (
              <span className="font-medium">{n.title}</span>
            )}
            <Sub>{n.sub}</Sub>
          </div>
        </div>
      </td>
      {sumCells(n.rows)}
    </tr>
  )

  return (
    <>
      <ul className="divide-y divide-line sm:hidden" data-testid="stock-cards">
        {data.rows.map((r) => {
          const st = STATUS[r.status ?? 'no_data']
          return (
            <li key={key(r)} className="px-4 py-2.5" data-testid="stock-card">
              <div className="flex items-start gap-2">
                <Link href={productHref(r.product.id)} className="min-w-0 flex-1 truncate font-medium">
                  {r.product.offer_id}
                </Link>
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${st.badge}`}>{st.label}</span>
              </div>
              {r.product.name && <div className="truncate text-xs text-faint">{r.product.name}</div>}
              {data.group !== 'product' && <div className="mt-0.5 text-xs text-faint">{r.warehouse ? `${r.warehouse.name} · ${r.warehouse.type}` : r.cluster}</div>}
              <div className="mt-1 flex flex-wrap gap-x-3 text-sm">
                <span>
                  <span className="text-muted">доступно</span> <span className="font-medium tabular-nums">{r.available}</span>
                </span>
                {r.in_transit ? <span className="text-muted">в пути {r.in_transit}</span> : null}
                <span className="text-muted">{perDay(r.avg_daily_sales)} в день</span>
                {r.days_of_supply !== null && <span className="text-muted">запас {num(r.days_of_supply)} дн.</span>}
                <span className={r.stock_value === null ? 'text-faint' : 'text-muted'}>{r.stock_value === null ? 'стоимость — нет данных' : rub(r.stock_value)}</span>
              </div>
            </li>
          )
        })}
      </ul>
      <div className="hidden overflow-x-auto sm:block">
        <table className="w-full text-sm">
          <thead className="bg-soft/60 text-left text-xs text-muted">
            <tr>
              <SortTh s="offer_id" first="asc" sort={sort} order={order} onSort={onSort} left>
                {data.group === 'warehouse' ? 'Кластер, склад или товар' : data.group === 'cluster' ? 'Кластер или товар' : 'Товар'}
              </SortTh>
              <th className="whitespace-nowrap px-3 py-2.5 font-medium" title="На сколько дней продаж хватит остатка — по порогам из «Настроек»">
                Ликвидность
                <div className="text-[11px] font-normal text-faint">статус · запас</div>
              </th>
              <SortTh s="available" first="desc" sort={sort} order={order} onSort={onSort} title="Доступно к продаже здесь">
                Доступно
              </SortTh>
              {columns
                .filter((c) => c.key !== 'status')
                .map((c) =>
                  c.sort ? (
                    <SortTh key={c.key} s={c.sort} first={c.first ?? 'desc'} sort={sort} order={order} onSort={onSort} title={c.hint} left={c.key === 'last_sale'}>
                      {c.label}
                    </SortTh>
                  ) : (
                    <th key={c.key} className="whitespace-nowrap px-3 py-2.5 text-right font-medium" title={c.hint}>
                      {c.label}
                    </th>
                  ),
                )}
            </tr>
          </thead>
          <tbody>
            {tree && (
              <tr className="font-semibold" data-testid="stock-total-row">
                <td className="px-3 py-2">
                  Всего {tree.length} {plural(tree.length, 'кластер', 'кластера', 'кластеров')}
                  <button
                    type="button"
                    onClick={() => setOpen(autoOpen === (open.size > 0) ? new Set() : new Set(tree.flatMap((n) => [n.key, ...(n.children ?? []).map((c) => c.key)])))}
                    className="ml-3 text-xs font-normal text-accent hover:underline"
                    data-testid="stock-expand-all"
                  >
                    {tree.every((n) => isOpen(n.key)) ? 'Свернуть все' : 'Развернуть все'}
                  </button>
                </td>
                {sumCells(data.rows, true)}
              </tr>
            )}
            {tree
              ? tree.flatMap((n) => [
                  groupRow(n, 0),
                  ...(isOpen(n.key)
                    ? n.children
                      ? n.children.flatMap((c) => [groupRow(c, 1), ...(isOpen(c.key) ? c.rows.map((r) => productRow(r, 2)) : [])])
                      : n.rows.map((r) => productRow(r, 1))
                    : []),
                ])
              : data.rows.map((r) => productRow(r, 0))}
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
        className={buttonClass('secondary', 'sm')}
        data-testid="columns-button"
      >
        Колонки ▾
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-1 w-72 rounded-xl border border-line bg-card p-2 shadow-pop" data-testid="columns-menu">
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

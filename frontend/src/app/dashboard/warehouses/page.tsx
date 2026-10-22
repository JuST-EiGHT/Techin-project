'use client'
// Склады (шаги 1.3, 5.1): итоги по статусам остатка, «Требуют внимания», карточки складов со счётчиками и топ-5,
// таблица по складу с фильтром статуса и CSV. Склад и статус — в адресе (?warehouse=&status=): на них ссылаются главная
// и карточки. Статус — по товару в целом (весь остаток / продажи в день) и порогам из «Настроек».
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { fmtDay } from '@/components/reports'
import { timeAgo } from '@/components/SyncStatus'
import { api, errorMessage } from '@/lib/api'
import { saveFile } from '@/lib/download'
import {
  type ProductStock,
  STATUS,
  STATUS_ORDER,
  type StatusKey,
  type StockRow,
  type Summary,
  type Thresholds,
  type WarehouseSummary,
  idleText,
  num,
  perDay,
  productHref,
  qty,
  statusHint,
  warehouseLabel,
} from '@/lib/stocks'

type SyncStatus = {
  has_active_key: boolean
  stocks: { last_run: { status: string; error: string | null } | null; last_success_at: string | null }
}

const POLL_MS = 3000
const ATTENTION_SHOWN = 5

export default function WarehousesRoute() {
  return (
    <Suspense>
      <Warehouses />
    </Suspense>
  )
}

function Warehouses() {
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const warehouseId = params.get('warehouse') ?? ''
  const rawStatus = params.get('status')
  const status = (STATUS_ORDER as string[]).includes(rawStatus ?? '') ? (rawStatus as StatusKey) : ''
  const [includeEmpty, setIncludeEmpty] = useState(false)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [rows, setRows] = useState<StockRow[] | null>(null)
  const [sync, setSync] = useState<SyncStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [showInactive, setShowInactive] = useState(false)
  const wasActive = useRef(false)
  const tableRef = useRef<HTMLElement>(null)

  const setUrl = useCallback(
    (next: { warehouse?: string; status?: string }, scroll = false) => {
      const q = new URLSearchParams(params.toString())
      for (const [key, value] of Object.entries(next)) {
        if (value) q.set(key, value)
        else q.delete(key)
      }
      const s = q.toString()
      router.replace(s ? `${pathname}?${s}` : pathname, { scroll: false })
      if (scroll) setTimeout(() => tableRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50)
    },
    [params, pathname, router],
  )

  const loadSummary = useCallback(async () => {
    try {
      setSummary((await api.get<Summary>('/warehouses/summary')).data)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  const tableParams = useCallback(() => {
    const q: Record<string, string | boolean> = {}
    if (status) q.status = status
    if (includeEmpty) q.include_empty = true
    return q
  }, [status, includeEmpty])

  const loadRows = useCallback(async () => {
    try {
      const url = warehouseId ? `/warehouses/${warehouseId}/products` : '/stocks'
      setRows((await api.get<StockRow[]>(url, { params: tableParams() })).data)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [warehouseId, tableParams])

  const loadSync = useCallback(async () => {
    try {
      const { data } = await api.get<SyncStatus>('/sync/status')
      const nowActive = ['queued', 'running'].includes(data.stocks.last_run?.status ?? '')
      if (wasActive.current && !nowActive) {
        loadSummary() // синхронизация закончилась — перечитываем
        loadRows()
      }
      wasActive.current = nowActive
      setSync(data)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [loadSummary, loadRows])

  useEffect(() => {
    loadSummary()
  }, [loadSummary])
  useEffect(() => {
    loadRows()
  }, [loadRows])
  useEffect(() => {
    loadSync()
  }, [loadSync])

  const active = ['queued', 'running'].includes(sync?.stocks.last_run?.status ?? '')
  useEffect(() => {
    if (!active) return
    const t = setInterval(loadSync, POLL_MS)
    return () => clearInterval(t)
  }, [active, loadSync])

  async function syncNow() {
    setStarting(true)
    setError(null)
    try {
      await api.post('/sync/stocks')
    } catch (err) {
      if ((err as { response?: { status?: number } }).response?.status !== 409) setError(errorMessage(err))
    } finally {
      setStarting(false)
      await loadSync()
    }
  }

  async function exportCsv() {
    setError(null)
    try {
      const q = { ...tableParams(), ...(warehouseId ? { warehouse_id: warehouseId } : {}) }
      await saveFile(api.get('/stocks/export', { params: q, responseType: 'blob' }), 'stocks.csv')
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const lastRun = sync?.stocks.last_run
  const lastSuccess = sync?.stocks.last_success_at
  const t = summary?.thresholds
  const activeWarehouses = summary?.warehouses.filter((w) => w.is_active) ?? []
  const inactive = summary?.warehouses.filter((w) => !w.is_active) ?? []
  const selected = summary?.warehouses.find((w) => w.id === warehouseId)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h1 className="text-2xl font-semibold">Склады и остатки</h1>
        {sync?.has_active_key && (
          <div className="flex flex-wrap items-center gap-3 text-sm sm:ml-auto">
            <span className="text-muted">
              {active || starting
                ? 'Остатки обновляются…'
                : lastSuccess
                  ? `Остатки обновлены ${timeAgo(lastSuccess)}`
                  : 'Остатки ещё не загружались'}
            </span>
            <button
              onClick={syncNow}
              disabled={active || starting}
              className="rounded-lg border border-line px-3 py-1.5 hover:border-accent hover:text-accent disabled:opacity-60"
            >
              Обновить остатки
            </button>
          </div>
        )}
      </div>

      {sync && !sync.has_active_key && (
        <p className="text-sm text-warn">
          Остатки приходят только из Ozon API —{' '}
          <Link href="/dashboard/settings/integrations" className="underline">
            подключите API-ключ Ozon
          </Link>
          .
        </p>
      )}
      <FormError message={error ?? (lastRun?.status === 'failed' ? `Последнее обновление не удалось: ${lastRun.error}` : null)} />

      {summary && summary.stocks_known && t && (
        <>
          <StatusTotals summary={summary} status={status} onPick={(s) => setUrl({ warehouse: '', status: s === status ? '' : s }, true)} />
          <p className="-mt-3 text-xs text-faint" data-testid="thresholds-note">
            Статус — на сколько дней продаж хватит остатка товара на всех складах: «Критично» — меньше {t.critical} дн., «Мало» —
            меньше {t.low}, «Избыток» — больше {t.excess} дн. или товар не продаётся.{' '}
            <Link href="/dashboard/settings" className="text-accent hover:underline">
              Изменить пороги →
            </Link>
          </p>
          {!summary.sales_known && (
            <p className="rounded-lg border border-line bg-card px-4 py-3 text-sm text-muted" data-testid="no-sales">
              Мало данных о продажах: дни запаса и статус считаются, когда за последние 30 дней загружены начисления хотя бы за 7
              дней — синхронизацией Ozon API или отчётами по начислениям. Пока у всех товаров — «Нет данных».
            </p>
          )}
          {summary.sales_known && <Attention summary={summary} thresholds={t} />}
          <section className="space-y-3">
            <div className="flex flex-wrap items-baseline gap-x-3">
              <h2 className="text-lg font-semibold">Склады</h2>
              <span className="text-sm text-muted">нажмите на склад или статус — покажем его товары</span>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" data-testid="warehouse-cards">
              {activeWarehouses.map((w) => (
                <WarehouseCard
                  key={w.id}
                  w={w}
                  selected={w.id === warehouseId}
                  status={w.id === warehouseId ? status : ''}
                  onPick={(s) => setUrl({ warehouse: w.id, status: s }, true)}
                />
              ))}
            </div>
            {inactive.length > 0 && (
              <button type="button" onClick={() => setShowInactive((v) => !v)} className="text-sm text-accent hover:underline">
                {showInactive ? 'Скрыть неактивные' : `Неактивные склады (${inactive.length})`}
              </button>
            )}
            {showInactive && (
              <div className="grid gap-3 opacity-70 sm:grid-cols-2 xl:grid-cols-3">
                {inactive.map((w) => (
                  <WarehouseCard key={w.id} w={w} selected={w.id === warehouseId} status="" onPick={(s) => setUrl({ warehouse: w.id, status: s }, true)} />
                ))}
              </div>
            )}
          </section>
        </>
      )}

      {summary && !summary.stocks_known && sync?.has_active_key && (
        <p className="rounded-lg border border-line bg-card px-4 py-3 text-sm text-muted">
          Остатки ещё не загружены — нажмите «Обновить остатки». Дальше они обновляются сами каждые 30 минут.
        </p>
      )}

      {summary?.stocks_known && (
        <section ref={tableRef} className="scroll-mt-4 rounded-xl border border-line bg-card" data-testid="stock-table">
          <div className="flex flex-wrap items-center gap-3 border-b border-line p-4">
            <select
              aria-label="Склад"
              value={warehouseId}
              onChange={(e) => setUrl({ warehouse: e.target.value })}
              className="max-w-full rounded-lg border border-line bg-bg px-3 py-1.5 text-sm"
              data-testid="filter-warehouse"
            >
              <option value="">Все склады</option>
              {summary.warehouses.map((w) => (
                <option key={w.id} value={w.id}>
                  {warehouseLabel(w)}
                  {w.is_active ? '' : ' — неактивен'}
                </option>
              ))}
            </select>
            <select
              aria-label="Статус"
              value={status}
              onChange={(e) => setUrl({ status: e.target.value })}
              className="max-w-full rounded-lg border border-line bg-bg px-3 py-1.5 text-sm"
              data-testid="filter-status"
            >
              <option value="">Все статусы</option>
              {t &&
                STATUS_ORDER.map((s) => (
                  <option key={s} value={s}>
                    {STATUS[s].label} — {statusHint(s, t)}
                  </option>
                ))}
            </select>
            <label className="flex items-center gap-2 text-sm text-muted">
              <input type="checkbox" checked={includeEmpty} onChange={(e) => setIncludeEmpty(e.target.checked)} />
              Нулевые остатки
            </label>
            <div className="flex items-center gap-3 sm:ml-auto">
              {rows && <span className="text-sm text-faint">Строк: {rows.length}</span>}
              <button
                type="button"
                onClick={exportCsv}
                disabled={!rows?.length}
                className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent disabled:opacity-60"
                data-testid="export-csv"
              >
                Скачать CSV
              </button>
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
              <button type="button" onClick={() => setUrl({ warehouse: '', status: '' })} className="ml-auto text-accent hover:underline">
                ✕ Все склады
              </button>
            </div>
          )}
          <StockTable rows={rows} filtered={Boolean(warehouseId || status)} />
        </section>
      )}
    </div>
  )
}

function StatusTotals({ summary, status, onPick }: { summary: Summary; status: string; onPick: (s: StatusKey) => void }) {
  const { totals, thresholds } = summary
  const shown = STATUS_ORDER.filter((s) => s !== 'no_data' || totals.counts.no_data > 0)
  const five = shown.length === 4 // «Нет данных» не показываем — 5 ячеек: первая на телефоне во всю ширину
  return (
    <div
      className={`grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line ${five ? 'sm:grid-cols-5' : 'sm:grid-cols-3 lg:grid-cols-6'}`}
      data-testid="status-totals"
    >
      <div className={`bg-card p-4 ${five ? 'col-span-2 sm:col-span-1' : ''}`}>
        <div className="text-xs text-muted">Товаров на складах</div>
        <div className="mt-1 text-xl font-semibold tabular-nums">{totals.products}</div>
        <div className="text-xs text-faint">
          {totals.present.toLocaleString('ru-RU')} шт.{totals.in_transit > 0 && ` · в пути ${totals.in_transit.toLocaleString('ru-RU')}`}
        </div>
      </div>
      {shown.map((s) => (
        <button
          key={s}
          type="button"
          onClick={() => onPick(s)}
          aria-pressed={status === s}
          title={statusHint(s, thresholds)}
          className={`bg-card p-4 text-left hover:bg-bg ${status === s ? 'ring-2 ring-inset ring-accent' : ''}`}
          data-testid={`total-${s}`}
        >
          <div className="text-xs text-muted">{STATUS[s].label}</div>
          <div className={`mt-1 text-xl font-semibold tabular-nums ${totals.counts[s] ? STATUS[s].text : ''}`}>{totals.counts[s]}</div>
          <div className="text-xs text-faint">{statusHint(s, thresholds)}</div>
        </button>
      ))}
    </div>
  )
}

function ProductLine({ p, children }: { p: ProductStock; children: React.ReactNode }) {
  return (
    <li>
      <Link href={productHref(p.product.id)} className="flex items-start gap-3 px-4 py-2 hover:bg-bg">
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">{p.product.offer_id}</span>
          {p.product.name && <span className="block truncate text-xs text-muted">{p.product.name}</span>}
        </span>
        <span className="shrink-0 text-right text-sm">{children}</span>
      </Link>
    </li>
  )
}

function Attention({ summary, thresholds }: { summary: Summary; thresholds: Thresholds }) {
  const ending = summary.running_out.slice(0, ATTENTION_SHOWN)
  const idle = summary.lying.slice(0, ATTENTION_SHOWN)
  return (
    <section className="space-y-3" data-testid="attention">
      <h2 className="text-lg font-semibold">Требуют внимания</h2>
      <div className="grid gap-3 lg:grid-cols-2">
        <div className="rounded-xl border border-line bg-card" data-testid="attention-ending">
          <div className="flex flex-wrap items-baseline gap-x-2 border-b border-line px-4 py-3">
            <h3 className="font-semibold">Заканчиваются</h3>
            <span className="text-sm text-muted">{summary.running_out_total}</span>
            <span className="text-xs text-faint">запаса меньше чем на {thresholds.low} дн.</span>
            {summary.running_out_total > 0 && (
              <Link href="/dashboard/products?filter=low_stock" className="ml-auto text-sm text-accent hover:underline">
                Все →
              </Link>
            )}
          </div>
          {ending.length === 0 ? (
            <p className="px-4 py-3 text-sm text-muted">Запаса хватает всем товарам.</p>
          ) : (
            <ul className="divide-y divide-line">
              {ending.map((p) => (
                <ProductLine key={p.product.id} p={p}>
                  <span className={`block font-medium ${STATUS[p.status ?? 'no_data'].text}`}>
                    {p.available === 0 ? 'кончился' : `на ${num(p.days_of_supply)} дн.`}
                  </span>
                  <span className="block text-xs text-faint">
                    {qty(p.available)} · {perDay(p.avg_daily_sales)} в день
                    {p.in_transit ? ` · в пути ${p.in_transit}` : ''}
                  </span>
                </ProductLine>
              ))}
            </ul>
          )}
        </div>
        <div className="rounded-xl border border-line bg-card" data-testid="attention-idle">
          <div className="flex flex-wrap items-baseline gap-x-2 border-b border-line px-4 py-3">
            <h3 className="font-semibold">Лежат без движения</h3>
            <span className="text-sm text-muted">{summary.lying_total}</span>
            <span className="text-xs text-faint">избыток — больше {thresholds.excess} дн.</span>
            <Link href="/dashboard/warehouses/withdrawal" className="ml-auto text-sm text-accent hover:underline">
              Кандидаты на вывоз →
            </Link>
          </div>
          {idle.length === 0 ? (
            <p className="px-4 py-3 text-sm text-muted">Залежавшихся товаров нет.</p>
          ) : (
            <ul className="divide-y divide-line">
              {idle.map((p) => (
                <ProductLine key={p.product.id} p={p}>
                  <span className="block font-medium">{qty(p.available)}</span>
                  <span className="block text-xs text-faint">
                    {idleText(p)}
                    {p.days_of_supply !== null && ` · запас ${num(p.days_of_supply)} дн.`}
                  </span>
                </ProductLine>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  )
}

function WarehouseCard({ w, selected, status, onPick }: { w: WarehouseSummary; selected: boolean; status: string; onPick: (s: string) => void }) {
  const chips = STATUS_ORDER.filter((s) => w.counts[s] > 0)
  return (
    <div className={`flex flex-col rounded-xl border bg-card ${selected ? 'border-accent' : 'border-line'}`} data-testid="warehouse-card" data-type={w.type}>
      <button type="button" onClick={() => onPick('')} className="rounded-t-xl p-4 pb-2 text-left hover:bg-bg">
        <div className="text-xs text-muted">
          {w.type}
          {w.cluster_name ? ` · ${w.cluster_name}` : ''}
          {!w.is_active && ' · неактивен'}
        </div>
        <div className="mt-0.5 truncate font-medium" title={w.name}>
          {w.name}
        </div>
        <div className="mt-1 text-sm text-muted">
          {w.total_present.toLocaleString('ru-RU')} шт. · товаров {w.skus}
          {w.total_in_transit > 0 && ` · в пути ${w.total_in_transit.toLocaleString('ru-RU')}`}
        </div>
      </button>
      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-4 pb-2">
          {chips.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => onPick(s)}
              aria-pressed={status === s}
              className={`rounded px-2 py-0.5 text-xs ${STATUS[s].badge} ${status === s ? 'ring-1 ring-current' : ''}`}
              data-testid={`chip-${s}`}
            >
              {STATUS[s].label}: {w.counts[s]}
            </button>
          ))}
        </div>
      )}
      {w.top.length > 0 && (
        <ul className="mt-auto border-t border-line px-4 py-2 text-sm">
          {w.top.map((p) => (
            <li key={p.product.id} className="flex items-center gap-2 py-0.5">
              <span className={`h-2 w-2 shrink-0 rounded-full ${STATUS[p.status ?? 'no_data'].dot}`} aria-hidden />
              <Link href={productHref(p.product.id)} className="min-w-0 flex-1 truncate hover:text-accent" title={p.product.name ?? undefined}>
                {p.product.offer_id}
              </Link>
              <span className="shrink-0 tabular-nums text-muted">{p.present.toLocaleString('ru-RU')} шт.</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function StockTable({ rows, filtered }: { rows: StockRow[] | null; filtered: boolean }) {
  if (rows === null) return <p className="p-4 text-sm text-muted">Загрузка…</p>
  if (rows.length === 0)
    return <p className="p-4 text-sm text-muted">Остатков нет{filtered ? ' по выбранным фильтрам' : ''}.</p>
  return (
    <>
      <ul className="divide-y divide-line sm:hidden" data-testid="stock-cards">
        {rows.map((s) => {
          const st = STATUS[s.status ?? 'no_data']
          return (
            <li key={`${s.product.id}-${s.warehouse.id}`} className={`border-l-4 px-4 py-2 ${st.row}`}>
              <div className="flex items-start gap-2">
                <Link href={productHref(s.product.id)} className="min-w-0 flex-1 truncate font-medium">
                  {s.product.offer_id}
                </Link>
                <span className={`shrink-0 rounded px-2 py-0.5 text-xs ${st.badge}`}>{st.label}</span>
              </div>
              {s.product.name && <div className="truncate text-xs text-muted">{s.product.name}</div>}
              <div className="mt-0.5 text-xs text-faint">
                {s.warehouse.name} · {s.warehouse.type}
                {s.warehouse.cluster_name ? ` · ${s.warehouse.cluster_name}` : ''}
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 text-sm">
                <span>
                  <span className="text-muted">доступно</span> <span className="font-medium tabular-nums">{s.present}</span>
                </span>
                {s.in_transit ? <span className="text-muted">в пути {s.in_transit}</span> : null}
                {s.reserved ? <span className="text-muted">резерв {s.reserved}</span> : null}
                <span className="text-muted">{perDay(s.avg_daily_sales)} в день</span>
                {s.days_of_supply !== null && <span className="text-muted">запас {num(s.days_of_supply)} дн.</span>}
                <span className="text-muted">{s.last_sale ? `продажа ${fmtDay(s.last_sale)}` : 'продаж нет в данных'}</span>
              </div>
            </li>
          )
        })}
      </ul>
      <div className="hidden overflow-x-auto sm:block">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted">
            <tr className="border-b border-line">
              <th className="px-4 py-2 font-normal">Товар</th>
              <th className="px-4 py-2 font-normal">Склад</th>
              <th className="px-4 py-2 text-right font-normal">Доступно</th>
              <th className="px-4 py-2 text-right font-normal">Резерв</th>
              <th className="px-4 py-2 text-right font-normal">В пути</th>
              <th className="px-4 py-2 text-right font-normal" title="По товару в целом: продано за вычетом возвратов за 30 дней / дней с данными. Склада в начислениях Ozon нет">
                Продажи/день
              </th>
              <th className="px-4 py-2 text-right font-normal" title="По товару в целом: весь остаток на всех складах / продажи в день">
                Дней запаса
              </th>
              <th className="px-4 py-2 font-normal" title="Последний день с продажей товара — на любом складе">
                Последняя продажа
              </th>
              <th className="px-4 py-2 font-normal">Статус</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => {
              const st = STATUS[s.status ?? 'no_data']
              return (
                <tr
                  key={`${s.product.id}-${s.warehouse.id}`}
                  data-testid="stock-row"
                  data-status={s.status ?? 'none'}
                  className={`border-b border-l-4 border-b-line last:border-b-0 ${st.row}`}
                >
                  <td className="px-4 py-2">
                    <Link href={productHref(s.product.id)} className="font-medium hover:text-accent">
                      {s.product.offer_id}
                    </Link>
                    {s.product.name && <div className="max-w-[14rem] truncate text-xs text-muted">{s.product.name}</div>}
                  </td>
                  <td className="px-4 py-2">
                    <div>{s.warehouse.name}</div>
                    <div className="text-xs text-muted">
                      {s.warehouse.type}
                      {s.warehouse.cluster_name ? ` · ${s.warehouse.cluster_name}` : ''}
                    </div>
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums">{s.present}</td>
                  <td className="px-4 py-2 text-right tabular-nums">{s.reserved ?? '—'}</td>
                  <td className="px-4 py-2 text-right tabular-nums">{s.in_transit ?? '—'}</td>
                  <td className="px-4 py-2 text-right tabular-nums">{perDay(s.avg_daily_sales)}</td>
                  <td className="px-4 py-2 text-right tabular-nums">
                    {num(s.days_of_supply)}
                    {s.days_of_supply !== null && s.product_available !== s.present && (
                      <div className="text-xs text-faint">из {s.product_available} шт. на всех складах</div>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-4 py-2">
                    {s.last_sale ? fmtDay(s.last_sale) : <span className="text-faint">нет в данных</span>}
                    {s.days_without_sales !== null && s.days_without_sales > 0 && (
                      <div className="text-xs text-faint">{s.days_without_sales} дн. назад</div>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    <span className={`whitespace-nowrap rounded px-2 py-0.5 text-xs ${st.badge}`}>{st.label}</span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}

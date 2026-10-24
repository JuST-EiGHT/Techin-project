'use client'
// Склады (шаги 1.3, 5.1): итоги по статусам остатка, «Требуют внимания», карточки складов со счётчиками и топ-5,
// таблица по складу с фильтром статуса и CSV. Склад и статус — в адресе (?warehouse=&status=): на них ссылаются главная
// и карточки. Статус — по товару в целом (весь остаток / продажи в день) и порогам из «Настроек».
// 10.17: таблица «Остатки» — по товарам, складам или кластерам (?group=), стоимость запаса, дата окончания, колонки,
// сортировка, Excel (components/stock-table.tsx); в «Требуют внимания» — лежат без себестоимости.
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { StockTablePanel, TABLE_STATUSES } from '@/components/stock-table'
import { refreshSyncStatus, timeAgo } from '@/lib/sync'
import { api, errorMessage } from '@/lib/api'
import {
  type ProductStock,
  STATUS,
  STATUS_ORDER,
  type StatusKey,
  type StockGroup,
  type Summary,
  type TableStatus,
  type Thresholds,
  type WarehouseSummary,
  idleText,
  num,
  perDay,
  productHref,
  qty,
  statusHint,
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
  const status = (TABLE_STATUSES as string[]).includes(rawStatus ?? '') ? (rawStatus as TableStatus) : ''
  const rawGroup = params.get('group')
  const group: StockGroup = rawGroup === 'warehouse' || rawGroup === 'cluster' ? rawGroup : 'product'
  const [summary, setSummary] = useState<Summary | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [sync, setSync] = useState<SyncStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [showInactive, setShowInactive] = useState(false)
  const wasActive = useRef(false)
  const tableRef = useRef<HTMLElement>(null)

  const setUrl = useCallback(
    (next: { warehouse?: string; status?: string; group?: string }, scroll = false) => {
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

  const loadSync = useCallback(async () => {
    try {
      const { data } = await api.get<SyncStatus>('/sync/status')
      const nowActive = ['queued', 'running'].includes(data.stocks.last_run?.status ?? '')
      if (wasActive.current && !nowActive) {
        loadSummary() // синхронизация закончилась — перечитываем
        setReloadKey((k) => k + 1)
      }
      wasActive.current = nowActive
      setSync(data)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [loadSummary])

  useEffect(() => {
    loadSummary()
  }, [loadSummary])
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
      refreshSyncStatus() // кнопка «Синхронизация» в шапке сразу покажет загрузку
    } catch (err) {
      if ((err as { response?: { status?: number } }).response?.status !== 409) setError(errorMessage(err))
    } finally {
      setStarting(false)
      await loadSync()
    }
  }

  const lastRun = sync?.stocks.last_run
  const lastSuccess = sync?.stocks.last_success_at
  const t = summary?.thresholds
  const activeWarehouses = summary?.warehouses.filter((w) => w.is_active) ?? []
  const inactive = summary?.warehouses.filter((w) => !w.is_active) ?? []
  const totalsStatus = (STATUS_ORDER as string[]).includes(status) ? (status as StatusKey) : ''

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
          <StatusTotals summary={summary} status={totalsStatus} onPick={(s) => setUrl({ warehouse: '', status: s === status ? '' : s }, true)} />
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
                  status={w.id === warehouseId ? totalsStatus : ''}
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
          <div className="flex flex-wrap items-baseline gap-x-3 px-4 pt-4">
            <h2 className="text-lg font-semibold">Остатки</h2>
            <span className="text-sm text-muted">по товарам, складам или кластерам — со стоимостью запаса и датой окончания</span>
          </div>
          <StockTablePanel warehouses={summary.warehouses} warehouseId={warehouseId} status={status} group={group} reloadKey={reloadKey} onUrl={(next) => setUrl(next)} />
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
  const noCost = (summary.no_cost ?? []).slice(0, ATTENTION_SHOWN)
  return (
    <section className="space-y-3" data-testid="attention">
      <h2 className="text-lg font-semibold">Требуют внимания</h2>
      <div className={`grid gap-3 ${noCost.length ? 'lg:grid-cols-2 xl:grid-cols-3' : 'lg:grid-cols-2'}`}>
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
              На вывоз →
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
        {noCost.length > 0 && (
          <div className="rounded-xl border border-line bg-card" data-testid="attention-no-cost">
            <div className="flex flex-wrap items-baseline gap-x-2 border-b border-line px-4 py-3">
              <h3 className="font-semibold">Без себестоимости</h3>
              <span className="text-sm text-muted">{summary.no_cost_total}</span>
              <span className="text-xs text-faint">стоимость запаса не посчитать</span>
              <Link href="/dashboard/products/costs" className="ml-auto text-sm text-accent hover:underline">
                Заполнить →
              </Link>
            </div>
            <ul className="divide-y divide-line">
              {noCost.map((p) => (
                <ProductLine key={p.product.id} p={p}>
                  <span className="block font-medium">{qty(p.available)}</span>
                  <span className="block text-xs text-faint">на складах · себестоимости нет</span>
                </ProductLine>
              ))}
            </ul>
          </div>
        )}
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

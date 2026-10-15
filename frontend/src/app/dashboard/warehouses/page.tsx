'use client'

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { timeAgo } from '@/components/SyncStatus'
import { api, errorMessage } from '@/lib/api'

type Warehouse = {
  id: string
  name: string
  type: 'FBO' | 'FBS' | 'rFBS'
  cluster_name: string | null
  is_active: boolean
  products_in_stock: number
  total_present: number
  total_in_transit: number
}

type StockStatus = 'critical' | 'low' | 'normal' | 'excess' | null

type Stock = {
  product: { id: string; offer_id: string; name: string | null; ozon_sku: number | null }
  warehouse: { id: string; name: string; type: string; cluster_name: string | null }
  present: number
  reserved: number | null
  in_transit: number | null
  avg_daily_sales: string | null
  days_of_supply: string | null
  product_available: number // доступно на всех складах — от него дни запаса
  status: StockStatus
}

type SyncStatus = {
  has_active_key: boolean
  stocks: {
    last_run: { status: string; error: string | null } | null
    last_success_at: string | null
  }
}

// Цвета статусов — палитра из CLAUDE.md
const STATUS: Record<string, { label: string; badge: string; row: string }> = {
  critical: { label: 'Критично', badge: 'bg-bad/15 text-bad', row: 'border-l-bad bg-bad/5' },
  low: { label: 'Мало', badge: 'bg-warn/15 text-warn', row: 'border-l-warn bg-warn/5' },
  normal: { label: 'Норма', badge: 'bg-ok/15 text-ok', row: 'border-l-ok' },
  excess: { label: 'Избыток', badge: 'bg-accent/15 text-accent', row: 'border-l-accent bg-accent/5' },
  none: { label: 'Нет данных', badge: 'bg-line text-muted', row: 'border-l-transparent' },
}

const STATUS_FILTERS = [
  { value: '', label: 'Все статусы' },
  { value: 'critical', label: 'Критично — меньше 7 дней' },
  { value: 'low', label: 'Мало — меньше 14 дней' },
  { value: 'normal', label: 'Норма' },
  { value: 'excess', label: 'Избыток — больше 60 дней' },
  { value: 'no_data', label: 'Нет данных о продажах' },
]

const POLL_MS = 3000
const dash = (v: number | string | null) => (v === null || v === undefined ? '—' : v)

function warehouseLabel(w: { name: string; type: string; cluster_name: string | null }) {
  return `${w.cluster_name ? `${w.cluster_name} · ` : ''}${w.name} (${w.type})`
}

export default function WarehousesPage() {
  const [warehouses, setWarehouses] = useState<Warehouse[] | null>(null)
  const [stocks, setStocks] = useState<Stock[] | null>(null)
  const [sync, setSync] = useState<SyncStatus | null>(null)
  const [filters, setFilters] = useState({ warehouse_id: '', status: '', include_empty: false })
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const wasActive = useRef(false)

  const loadData = useCallback(async () => {
    try {
      const params = Object.fromEntries(Object.entries(filters).filter(([, v]) => v !== '' && v !== false))
      const [w, s] = await Promise.all([
        api.get<Warehouse[]>('/warehouses'),
        api.get<Stock[]>('/stocks', { params }),
      ])
      setWarehouses(w.data)
      setStocks(s.data)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [filters])

  const loadSync = useCallback(async () => {
    try {
      const { data } = await api.get<SyncStatus>('/sync/status')
      const nowActive = ['queued', 'running'].includes(data.stocks.last_run?.status ?? '')
      if (wasActive.current && !nowActive) loadData() // синхронизация закончилась — перечитываем таблицу
      wasActive.current = nowActive
      setSync(data)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [loadData])

  useEffect(() => {
    loadData()
  }, [loadData])

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

  const lastRun = sync?.stocks.last_run
  const lastSuccess = sync?.stocks.last_success_at
  const noSalesYet = stocks !== null && stocks.length > 0 && stocks.every((s) => s.status === null)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-4">
        <h1 className="text-2xl font-semibold">Склады и остатки</h1>
        {sync?.has_active_key && (
          <div className="ml-auto flex items-center gap-3 text-sm">
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
          Чтобы загрузить остатки,{' '}
          <Link href="/dashboard/settings/api-keys" className="underline">
            подключите API-ключ Ozon
          </Link>
          .
        </p>
      )}
      <FormError message={error ?? (lastRun?.status === 'failed' ? `Последнее обновление не удалось: ${lastRun.error}` : null)} />

      {warehouses && warehouses.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {warehouses
            .filter((w) => w.is_active)
            .map((w) => (
              <button
                key={w.id}
                onClick={() => setFilters((f) => ({ ...f, warehouse_id: f.warehouse_id === w.id ? '' : w.id }))}
                className={`rounded-xl border bg-card p-4 text-left hover:border-accent ${
                  filters.warehouse_id === w.id ? 'border-accent' : 'border-line'
                }`}
              >
                <div className="text-xs text-muted">
                  {w.type}
                  {w.cluster_name ? ` · ${w.cluster_name}` : ''}
                </div>
                <div className="mt-1 truncate font-medium" title={w.name}>
                  {w.name}
                </div>
                <div className="mt-2 text-sm text-muted">
                  {w.total_present} шт. · товаров: {w.products_in_stock}
                  {w.total_in_transit > 0 && ` · в пути ${w.total_in_transit}`}
                </div>
              </button>
            ))}
        </div>
      )}

      <section className="rounded-xl border border-line bg-card">
        <div className="flex flex-wrap items-center gap-3 border-b border-line p-4">
          <select
            aria-label="Склад"
            value={filters.warehouse_id}
            onChange={(e) => setFilters((f) => ({ ...f, warehouse_id: e.target.value }))}
            className="rounded-lg border border-line bg-bg px-3 py-1.5 text-sm"
          >
            <option value="">Все склады</option>
            {warehouses?.map((w) => (
              <option key={w.id} value={w.id}>
                {warehouseLabel(w)}
                {w.is_active ? '' : ' — неактивен'}
              </option>
            ))}
          </select>
          <select
            aria-label="Статус"
            value={filters.status}
            onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}
            className="rounded-lg border border-line bg-bg px-3 py-1.5 text-sm"
          >
            {STATUS_FILTERS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-2 text-sm text-muted">
            <input
              type="checkbox"
              checked={filters.include_empty}
              onChange={(e) => setFilters((f) => ({ ...f, include_empty: e.target.checked }))}
            />
            Показывать нулевые остатки
          </label>
          {stocks && <span className="ml-auto text-sm text-faint">Строк: {stocks.length}</span>}
        </div>

        {noSalesYet && (
          <p className="border-b border-line px-4 py-2 text-sm text-muted">
            Мало данных о продажах: дни запаса и статус считаются, когда за последние 30 дней загружены начисления хотя бы
            за 7 дней — синхронизацией Ozon API или отчётами по начислениям. Пока у всех строк — «Нет данных».
          </p>
        )}

        {stocks === null ? (
          <p className="p-4 text-sm text-muted">Загрузка…</p>
        ) : stocks.length === 0 ? (
          <p className="p-4 text-sm text-muted">
            Остатков нет{filters.warehouse_id || filters.status ? ' по выбранным фильтрам' : ' — нажмите «Обновить остатки»'}.
          </p>
        ) : (
          <div className="overflow-x-auto">
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
                  <th className="px-4 py-2 font-normal">Статус</th>
                </tr>
              </thead>
              <tbody>
                {stocks.map((s) => {
                  const st = STATUS[s.status ?? 'none']
                  return (
                    <tr
                      key={`${s.product.id}-${s.warehouse.id}`}
                      data-testid="stock-row"
                      data-status={s.status ?? 'none'}
                      className={`border-b border-l-4 border-b-line last:border-b-0 ${st.row}`}
                    >
                      <td className="px-4 py-2">
                        <div className="font-medium">{s.product.offer_id}</div>
                        {s.product.name && <div className="max-w-xs truncate text-xs text-muted">{s.product.name}</div>}
                      </td>
                      <td className="px-4 py-2">
                        <div>{s.warehouse.name}</div>
                        <div className="text-xs text-muted">
                          {s.warehouse.type}
                          {s.warehouse.cluster_name ? ` · ${s.warehouse.cluster_name}` : ''}
                        </div>
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums">{s.present}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{dash(s.reserved)}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{dash(s.in_transit)}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{dash(s.avg_daily_sales)}</td>
                      <td className="px-4 py-2 text-right tabular-nums">
                        {dash(s.days_of_supply)}
                        {s.days_of_supply !== null && s.product_available !== s.present && (
                          <div className="text-xs text-faint">из {s.product_available} шт. на всех складах</div>
                        )}
                      </td>
                      <td className="px-4 py-2">
                        <span className={`rounded px-2 py-0.5 text-xs ${st.badge}`}>{st.label}</span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

'use client'
// Поставки (шаг 5.2, BUSINESS_LOGIC §5): что, в какой кластер Ozon и сколько везти, что закупить на свой склад — по
// заказам за 30 дней и текущим остаткам. Группы «Срочно / Скоро / Плановые», параметры меняются на странице и
// сохраняются в аккаунте, Excel для логиста.
// 10.18: вкладки (?tab=) — «Рекомендации» (+ страховой запас, «уже в планах и заявках», выбор товаров → план поставки с
// вашими количествами), «Планы» (components/supply-plans.tsx: правка, дата, Excel, заявка в Ozon), «Заявки Ozon» и
// «Календарь» (components/supply-orders.tsx).
// 10.18a: склады Ozon — по данным Ozon о кластере (продажи за 28 дн. из остатков, рекомендованная поставка), «Обновить
// данные» — остатки, рекомендации (вместе с заявками) и заказы одной кнопкой.
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { fmtDateTime, fmtDay } from '@/components/reports'
import { CalendarTab, OrdersTab } from '@/components/supply-orders'
import { PlansTab } from '@/components/supply-plans'
import { syncAll, timeAgo } from '@/lib/sync'
import { useAuth } from '@/contexts/AuthContext'
import { api, errorMessage } from '@/lib/api'
import { saveFile } from '@/lib/download'
import { saveSettings } from '@/lib/settings'
import { num, perDay, productHref } from '@/lib/stocks'
import {
  GRADE_TEXT,
  KIND,
  LEAD_WHY,
  type PlanDetail,
  type RowStatus,
  SAFETY_WHY,
  SHOW,
  skipText,
  STATUS,
  STATUS_ORDER,
  type SupplyItem,
  type SupplyKind,
  type SupplyParams,
  type SupplyReport,
  type SupplyShow,
  type SupplyView,
  TARGET_WHY,
  URGENCY,
  URGENCY_ORDER,
  urgencyHint,
  VIEW_WHY,
} from '@/lib/supply'

type KindState = { last_run: { status: string; error: string | null } | null; last_success_at: string | null } | null
type SyncState = { has_active_key: boolean; stocks: KindState; postings: KindState; supplies?: KindState }
type Form = { target_days: string; lead_time_days: string; safety_days: string }
type Tab = 'recommendations' | 'plans' | 'orders' | 'calendar'
/** Выбранные в план: ключ — тип|куда|товар. */
type Picked = Record<string, { item: SupplyItem; qty: string }>

const DEBOUNCE_MS = 400
const POLL_MS = 3000
const TABS: { value: Tab; label: string }[] = [
  { value: 'recommendations', label: 'Рекомендации' },
  { value: 'plans', label: 'Планы' },
  { value: 'orders', label: 'Заявки Ozon' },
  { value: 'calendar', label: 'Календарь' },
]
const toForm = (p: SupplyParams): Form => ({
  target_days: String(p.target_days),
  lead_time_days: String(p.lead_time_days),
  safety_days: String(p.safety_days ?? 0),
})
/** От чего зависит расчёт: остатки и продажи Ozon по кластерам, рекомендации Ozon (грузятся с заявками), заказы. */
const DATA_KINDS = ['stocks', 'supplies', 'postings'] as const
const kindsOf = (s: SyncState | null) => DATA_KINDS.map((k) => s?.[k] ?? null)
const isActive = (s: SyncState | null) => kindsOf(s).some((k) => ['queued', 'running'].includes(k?.last_run?.status ?? ''))
/** Самое старое из обновлений; что-то не загружалось — null. */
const oldestSuccess = (s: SyncState | null) => {
  const at = kindsOf(s).map((k) => k?.last_success_at ?? null)
  return at.some((t) => t === null) ? null : (at as string[]).reduce((a, b) => (a < b ? a : b))
}
const failedRun = (s: SyncState | null) => kindsOf(s).find((k) => k?.last_run?.status === 'failed')?.last_run ?? null
const pickKey = (i: SupplyItem) => `${i.kind}|${i.destination.key}|${i.product.id}`
const defaultQty = (i: SupplyItem) => String(i.still_needed > 0 ? i.still_needed : i.to_order)

function query(f: Form): { params?: SupplyParams; error?: string } {
  const target = Number(f.target_days)
  const lead = Number(f.lead_time_days)
  const safety = Number(f.safety_days)
  if (!/^\d+$/.test(f.target_days) || target < 1 || target > 365) return { error: 'Запас после поставки — от 1 до 365 дней' }
  if (!/^\d+$/.test(f.lead_time_days) || lead > 180) return { error: 'Дней в пути — от 0 до 180' }
  if (!/^\d+$/.test(f.safety_days) || safety > 180) return { error: 'Страховой запас — от 0 до 180 дней' }
  return { params: { target_days: target, lead_time_days: lead, safety_days: safety } }
}

const pieces = (n: number) => `${n.toLocaleString('ru-RU')} шт.`

export default function SupplyRoute() {
  return (
    <Suspense>
      <SupplyPage />
    </Suspense>
  )
}

function SupplyPage() {
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const rawTab = params.get('tab')
  const tab: Tab = TABS.some((t) => t.value === rawTab) ? (rawTab as Tab) : 'recommendations'
  const setUrl = useCallback(
    (next: Record<string, string | null>) => {
      const q = new URLSearchParams(params.toString())
      for (const [k, v] of Object.entries(next)) {
        if (v) q.set(k, v)
        else q.delete(k)
      }
      if (q.get('tab') === 'recommendations') q.delete('tab')
      const qs = q.toString()
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false })
    },
    [params, pathname, router],
  )
  const nav = useRef<HTMLElement>(null)
  useEffect(() => {
    // на телефоне вкладки прокручиваются вбок — открытая должна быть видна
    nav.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [tab])
  const rawView = params.get('view')
  const view: SupplyView = rawView === 'products' ? 'products' : 'clusters'
  const rawShow = params.get('show')
  const show: SupplyShow = SHOW.some((s) => s.value === rawShow) ? (rawShow as SupplyShow) : 'all'
  const openPlan = (id: string | null) => setUrl({ tab: 'plans', plan: id, order: null })
  const openOrder = (id: string | null) => setUrl({ tab: 'orders', order: id, plan: null })

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Поставки</h1>
        <p className="mt-1 text-sm text-muted">
          Что, в какой кластер Ozon и сколько везти, что закупить на свой склад; планы поставок, заявки Ozon и их результаты.
        </p>
      </div>
      <nav ref={nav} className="flex gap-1 overflow-x-auto border-b border-line" aria-label="Поставки" data-testid="supply-tabs">
        {TABS.map((t) => (
          <button
            key={t.value}
            type="button"
            onClick={() => setUrl({ tab: t.value, plan: null, order: null })}
            aria-current={tab === t.value ? 'page' : undefined}
            className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm sm:px-4 ${tab === t.value ? 'border-accent font-medium text-fg' : 'border-transparent text-muted hover:text-fg'}`}
            data-testid={`supply-tab-${t.value}`}
          >
            {t.label}
          </button>
        ))}
      </nav>
      {tab === 'recommendations' && (
        <Recommendations
          onPlans={(id) => openPlan(id)}
          view={view}
          show={show}
          onView={(v) => setUrl({ view: v === 'clusters' ? null : v })}
          onShow={(v) => setUrl({ show: v === 'all' ? null : v })}
        />
      )}
      {tab === 'plans' && <PlansTab openId={params.get('plan')} onOpen={openPlan} onOrder={(id) => openOrder(id)} />}
      {tab === 'orders' && <OrdersTab openId={params.get('order')} onOpen={openOrder} onPlan={(id) => openPlan(id)} />}
      {tab === 'calendar' && <CalendarTab onPlan={(id) => openPlan(id)} onOrder={(id) => openOrder(id)} />}
    </div>
  )
}

function Recommendations({ onPlans, view, show, onView, onShow }: {
  onPlans: (id: string | null) => void
  view: SupplyView
  show: SupplyShow
  onView: (v: SupplyView) => void
  onShow: (s: SupplyShow) => void
}) {
  const { user } = useAuth()
  const [form, setForm] = useState<Form | null>(null)
  const [data, setData] = useState<SupplyReport | null>(null)
  const [sync, setSync] = useState<SyncState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [starting, setStarting] = useState(false)
  const [picked, setPicked] = useState<Picked>({})
  const first = useRef(true)
  const wasActive = useRef(false)

  const load = useCallback(async (params?: SupplyParams) => {
    const { data } = await api.get<SupplyReport>('/supply/recommendations', { params })
    setData(data)
    return data
  }, [])

  useEffect(() => {
    load()
      .then((d) => setForm(toForm(d.params)))
      .catch((err) => setError(errorMessage(err)))
  }, [load])

  // параметры поменяли — пересчитать (без сохранения)
  useEffect(() => {
    if (!form) return
    if (first.current) {
      first.current = false
      return
    }
    const q = query(form)
    if (q.error) return setError(q.error)
    setError(null)
    setBusy(true)
    let alive = true
    const timer = setTimeout(() => {
      load(q.params)
        .catch((err) => alive && setError(errorMessage(err)))
        .finally(() => alive && setBusy(false))
    }, DEBOUNCE_MS)
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [form, load])

  const loadSync = useCallback(async () => {
    try {
      const { data: s } = await api.get<SyncState>('/sync/status')
      const nowActive = isActive(s)
      if (wasActive.current && !nowActive && form) {
        const q = query(form)
        if (q.params) load(q.params).catch((err) => setError(errorMessage(err))) // данные загрузились — пересчитать
      }
      wasActive.current = nowActive
      setSync(s)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [form, load])

  useEffect(() => {
    loadSync()
  }, [loadSync])
  const active = isActive(sync)
  useEffect(() => {
    if (!active) return
    const t = setInterval(loadSync, POLL_MS)
    return () => clearInterval(t)
  }, [active, loadSync])

  async function syncNow() {
    setStarting(true)
    setError(null)
    try {
      await syncAll([...DATA_KINDS]) // только что обновлённое пропустит; шапка сразу покажет загрузку
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setStarting(false)
      await loadSync()
    }
  }

  async function save() {
    if (!form || !user) return
    const q = query(form)
    if (!q.params) return setError(q.error ?? null)
    setError(null)
    try {
      await saveSettings(user.id, {
        supply_target_days: q.params.target_days,
        supply_lead_time_days: q.params.lead_time_days,
        supply_safety_days: q.params.safety_days,
      })
      await load(q.params)
      setSaved(true)
    } catch (err) {
      setError(errorMessage(err, 'Не сохранилось — попробуйте ещё раз'))
    }
  }

  async function exportXlsx() {
    if (!form) return
    const q = query(form)
    if (!q.params) return setError(q.error ?? null)
    setError(null)
    try {
      await saveFile(api.get('/supply/export', { params: q.params, responseType: 'blob' }), 'supply.xlsx')
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  function change(next: Partial<Form>) {
    setSaved(false)
    setForm((f) => (f ? { ...f, ...next } : f))
  }

  function toggle(items: SupplyItem[], on: boolean) {
    setPicked((p) => {
      const next = { ...p }
      for (const i of items) {
        if (on) next[pickKey(i)] = next[pickKey(i)] ?? { item: i, qty: defaultQty(i) }
        else delete next[pickKey(i)]
      }
      return next
    })
  }

  /** План на каждое место (кластер или свой склад) из выбранных строк. */
  async function makePlans(rows: { item: SupplyItem; qty: string }[], day: string): Promise<string[]> {
    const groups = new Map<string, { item: SupplyItem; qty: string }[]>()
    for (const p of rows) {
      const k = `${p.item.kind}|${p.item.destination.key}`
      groups.set(k, [...(groups.get(k) ?? []), p])
    }
    const made: string[] = []
    for (const part of groups.values()) {
      const d = part[0].item
      const { data: plan } = await api.post<PlanDetail>('/supply/plans', {
        kind: d.kind,
        destination_key: d.destination.key,
        destination_name: d.destination.name,
        planned_date: day || null,
        items: part.map((r) => ({ product_id: r.item.product.id, qty: Number(r.qty || 0), recommended: Number(defaultQty(r.item)) })),
      })
      made.push(plan.id)
    }
    return made
  }

  async function createPlans(day: string) {
    setError(null)
    try {
      const made = await makePlans(Object.values(picked), day)
      setPicked({})
      onPlans(made.length === 1 ? made[0] : null)
    } catch (err) {
      setError(errorMessage(err, 'План не создался — проверьте количества'))
    }
  }

  /** «Сделать поставку» у кластера: план со всем, что туда нужно ещё, — и открыть его. */
  async function supplyGroup(items: SupplyItem[]) {
    setError(null)
    const rows = items.filter((i) => i.still_needed > 0).map((i) => ({ item: i, qty: String(i.still_needed) }))
    if (!rows.length) return
    try {
      const made = await makePlans(rows, '')
      setPicked((p) => {
        const next = { ...p }
        for (const r of rows) delete next[pickKey(r.item)]
        return next
      })
      onPlans(made[0] ?? null)
    } catch (err) {
      setError(errorMessage(err, 'План не создался — попробуйте ещё раз'))
    }
  }

  const lastRun = failedRun(sync)
  const lastSuccess = oldestSuccess(sync)
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
        <p className="min-w-0 flex-1 text-sm text-muted">
          Склады Ozon — по продажам и рекомендациям Ozon в каждом кластере, свои склады — по заказам; отметьте товары, поправьте
          количество и создайте план поставки.
        </p>
        {sync?.has_active_key && (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="text-muted" data-testid="postings-synced">
              {active || starting ? 'Данные загружаются…' : lastSuccess ? `Данные обновлены ${timeAgo(lastSuccess)}` : 'Данные ещё не загружались'}
            </span>
            <button
              type="button"
              onClick={syncNow}
              disabled={active || starting}
              className="rounded-lg border border-line px-3 py-1.5 hover:border-accent hover:text-accent disabled:opacity-60"
              data-testid="postings-sync"
            >
              Обновить данные
            </button>
          </div>
        )}
      </div>

      <FormError message={error ?? (lastRun ? `Данные не загрузились: ${lastRun.error}` : null)} />

      {form && data && (
        <section className="rounded-xl border border-line bg-card p-5" data-testid="supply-params">
          <h2 className="font-semibold">Параметры</h2>
          <div className="mt-3 grid gap-4 sm:grid-cols-3">
            <DaysField id="target_days" label="Запас после поставки" value={form.target_days} onChange={(v) => change({ target_days: v })} why={TARGET_WHY} />
            <DaysField id="lead_time_days" label="Поставка в пути" value={form.lead_time_days} onChange={(v) => change({ lead_time_days: v })} why={LEAD_WHY} />
            <DaysField id="safety_days" label="Страховой запас" value={form.safety_days} onChange={(v) => change({ safety_days: v })} why={SAFETY_WHY} />
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={save}
              disabled={data.is_saved}
              className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
              data-testid="supply-save"
            >
              Сохранить параметры
            </button>
            {!data.is_saved && (
              <button
                type="button"
                onClick={() => {
                  setForm(toForm(data.saved))
                  setSaved(false)
                }}
                className="text-sm text-accent hover:underline"
              >
                Вернуть сохранённые
              </button>
            )}
            <span className="text-xs text-faint" data-testid="supply-saved-note">
              {saved ? 'Сохранено — страница будет открываться с ними' : data.is_saved ? 'Сохранены в аккаунте — их же видно в «Настройках»' : 'Есть несохранённые изменения'}
            </span>
          </div>
        </section>
      )}

      {data && (
        <Result
          data={data}
          busy={busy}
          onExport={exportXlsx}
          onSync={syncNow}
          syncing={active || starting}
          picked={picked}
          onToggle={toggle}
          onQty={(i, qty) => setPicked((p) => ({ ...p, [pickKey(i)]: { item: i, qty } }))}
          view={view}
          show={show}
          onView={onView}
          onShow={onShow}
          onSupply={supplyGroup}
        />
      )}
      {!data && !error && <p className="text-sm text-muted">Загрузка…</p>}
      {Object.keys(picked).length > 0 && <PlanBar picked={picked} onCreate={createPlans} onClear={() => setPicked({})} />}
    </div>
  )
}

function PlanBar({ picked, onCreate, onClear }: { picked: Picked; onCreate: (day: string) => void; onClear: () => void }) {
  const [day, setDay] = useState('')
  const [busy, setBusy] = useState(false)
  const rows = Object.values(picked)
  const units = rows.reduce((s, r) => s + Number(r.qty || 0), 0)
  const places = new Set(rows.map((r) => `${r.item.kind}|${r.item.destination.key}`)).size
  return (
    <div className="sticky bottom-3 z-10 rounded-xl border border-accent bg-card p-3 shadow-lg" data-testid="plan-bar">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-sm">
          Выбрано: <span className="font-medium">{rows.length}</span> · {pieces(units)}
          <span className="text-muted"> → {places === 1 ? 'один план' : `${places} плана(ов) — по одному на кластер или склад`}</span>
        </span>
        <label className="flex items-center gap-2 text-sm text-muted">
          когда везти
          <input type="date" value={day} onChange={(e) => setDay(e.target.value)} className="rounded-lg border border-line bg-bg px-2 py-1 text-fg" data-testid="plan-bar-date" />
        </label>
        <div className="flex items-center gap-2 sm:ml-auto">
          <button type="button" onClick={onClear} className="text-sm text-muted hover:text-fg">
            Сбросить
          </button>
          <button
            type="button"
            disabled={busy || units === 0}
            onClick={async () => {
              setBusy(true)
              await onCreate(day)
              setBusy(false)
            }}
            className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
            data-testid="plan-create"
          >
            Создать план
          </button>
        </div>
      </div>
    </div>
  )
}

function DaysField({ id, label, value, onChange, why }: { id: string; label: string; value: string; onChange: (v: string) => void; why: string }) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm text-muted">
        {label}
      </label>
      <div className="mt-1 flex items-center gap-2">
        <input
          id={id}
          inputMode="numeric"
          value={value}
          onChange={(e) => onChange(e.target.value.replace(/\D/g, ''))}
          className="w-24 rounded-lg border border-line bg-bg px-3 py-1.5 outline-none focus:border-accent"
          data-testid={`param-${id}`}
        />
        <span className="text-sm text-muted">дн.</span>
      </div>
      <p className="mt-1 text-xs text-faint">{why}</p>
    </div>
  )
}

function Empty({ children, testid }: { children: React.ReactNode; testid: string }) {
  return (
    <div className="rounded-xl border border-line bg-card px-4 py-3 text-sm text-muted" data-testid={testid}>
      {children}
    </div>
  )
}

function Result({ data, busy, onExport, onSync, syncing, picked, onToggle, onQty, view, show, onView, onShow, onSupply }: {
  data: SupplyReport
  busy: boolean
  onExport: () => void
  onSync: () => void
  syncing: boolean
  picked: Picked
  onToggle: (items: SupplyItem[], on: boolean) => void
  onQty: (item: SupplyItem, qty: string) => void
  view: SupplyView
  show: SupplyShow
  onView: (v: SupplyView) => void
  onShow: (s: SupplyShow) => void
  onSupply: (items: SupplyItem[]) => Promise<void>
}) {
  if (!data.has_key)
    return (
      <Empty testid="supply-empty">
        Продажи по кластерам и рекомендации поставок Ozon отдаёт только по API.{' '}
        <Link href="/dashboard/settings/integrations" className="text-accent hover:underline">
          Подключите API-ключ Ozon →
        </Link>
      </Empty>
    )
  if (!data.ozon_data.clusters_known && data.items.length === 0)
    return (
      <Empty testid="supply-empty">
        Ozon ещё не посчитал продажи по кластерам — они приходят вместе с остатками (каждые 30 минут) и рекомендациями
        поставок (каждые 3 часа).{' '}
        <button type="button" onClick={onSync} disabled={syncing} className="text-accent hover:underline disabled:opacity-60">
          Обновить данные →
        </button>
      </Empty>
    )
  const od = data.ozon_data
  const t = data.thresholds
  return (
    <section className={`space-y-4 transition-opacity ${busy ? 'opacity-60' : ''}`} aria-busy={busy} data-testid="supply-result">
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-5" data-testid="supply-totals">
        {URGENCY_ORDER.map((u) => (
          <button
            key={u}
            type="button"
            onClick={() => onShow(show === u ? 'all' : u)}
            aria-pressed={show === u}
            className={`bg-card p-4 text-left hover:bg-bg ${show === u ? 'ring-1 ring-inset ring-accent' : ''}`}
          >
            <div className="text-xs text-muted">{URGENCY[u].label}</div>
            <div className={`mt-1 text-xl font-semibold tabular-nums ${data.totals[u] ? URGENCY[u].text : ''}`} data-testid={`total-${u}`}>
              {data.totals[u]}
            </div>
            <div className="text-xs text-faint">{u === 'planned' ? 'запаса меньше цели' : urgencyHint(u, t).split(' — ')[0]}</div>
          </button>
        ))}
        <div className="bg-card p-4">
          <div className="text-xs text-muted">Отправить в Ozon</div>
          <div className="mt-1 text-xl font-semibold tabular-nums" data-testid="total-supply">
            {pieces(data.totals.supply_units)}
          </div>
          <div className="text-xs text-faint">по кластерам</div>
        </div>
        <div className="col-span-2 bg-card p-4 sm:col-span-1">
          <div className="text-xs text-muted">Закупить на свои склады</div>
          <div className="mt-1 text-xl font-semibold tabular-nums">{data.has_own_warehouses ? pieces(data.totals.purchase_units) : '—'}</div>
          <div className="text-xs text-faint">{data.has_own_warehouses ? 'FBS / rFBS' : 'своих складов нет'}</div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-faint">
        <span data-testid="supply-window">
          Склады Ozon — продажи по данным Ozon за 28 дн.;{' '}
          {od.recs_known && od.recs_synced_at ? `рекомендации Ozon обновлены ${fmtDateTime(od.recs_synced_at)}` : 'рекомендаций Ozon пока нет — только по продажам'}
          {data.has_own_warehouses && data.fbs && ` · свои склады — заказы за ${data.fbs.days} дн.`}
          {' · '}минимум {data.min_qty} шт. на склад ·{' '}
          <Link href="/dashboard/settings#supply" className="text-accent hover:underline">
            склады кластеров →
          </Link>
        </span>
        {data.items.length > 0 && (
          <button
            type="button"
            onClick={onExport}
            className="ml-auto rounded-lg border border-line px-3 py-1.5 text-sm text-fg hover:border-accent hover:text-accent"
            data-testid="supply-export"
          >
            Скачать Excel для логиста
          </button>
        )}
      </div>

      {od.recs_ok === false && od.recs_error && (
        <p className="rounded-lg border border-warn/40 bg-warn/10 px-4 py-2 text-sm text-warn" data-testid="recs-error">
          Рекомендации Ozon в последний раз не загрузились ({od.recs_error}) — считаем по продажам; при следующем обновлении
          попробуем снова.
        </p>
      )}

      {data.items.length === 0 && (
        <Empty testid="supply-nothing">
          Везти ничего не нужно: Ozon не советует поставок, а запаса в кластерах хватает на {data.params.target_days} дн. и дорогу.
        </Empty>
      )}
      {data.items.length + data.rest.length > 0 && (
        <Grouped
          data={data}
          view={view}
          show={show}
          onView={onView}
          onShow={onShow}
          lead={data.params.lead_time_days}
          picked={picked}
          onToggle={onToggle}
          onQty={onQty}
          onSupply={onSupply}
        />
      )}

      <p className="text-xs text-faint">
        Склады Ozon — по данным Ozon о кластере: продажи в день и остаток на складах кластера. «По продажам» — продажи в день ×
        (запас после поставки + страховой + дни в пути) минус остаток и то, что уже едет. «Отправить» — не меньше, чем
        рекомендует Ozon (это необходимый минимум), и не меньше {data.min_qty} шт. одного товара на склад. Кластер уже затоварен
        и Ozon не советует — не везём. Свои склады (FBS / rFBS) — закупка по заказам. «Хватит на» — остаток сейчас / продажи в
        день. «Нужно ещё» — за вычетом того, что уже в ваших планах и в заявках Ozon, которые готовятся. Пороги «Срочно» и
        «Скоро» — те же, что у остатков, меняются в{' '}
        <Link href="/dashboard/settings" className="text-accent hover:underline">
          «Настройках»
        </Link>
        .
      </p>
    </section>
  )
}

function Days({ item, lead }: { item: SupplyItem; lead: number }) {
  if (item.days_left === null)
    return <span className="text-muted">{item.kind === 'supply' ? 'Ozon не видит продаж здесь' : 'не продавался 7 дн.'}</span>
  return (
    <>
      <span className={`font-medium ${STATUS[item.status].text}`}>{item.stock === 0 ? 'нет на складе' : `${num(item.days_left)} дн.`}</span>
      {item.arrives_late && (
        <span className="block text-xs text-bad" title={`Поставка едет ${lead} дн. — товар кончится раньше`}>
          кончится до прихода поставки
        </span>
      )}
    </>
  )
}

function Where({ item }: { item: SupplyItem }) {
  const d = item.destination
  return (
    <>
      <span className="block">{d.name}</span>
      <span className="block text-xs text-faint">
        {item.kind === 'supply' ? (d.warehouses.length ? `кластер · лежит: ${d.warehouses.join(', ')}` : 'кластер · товара там нет') : `${KIND.purchase} · ${d.warehouse_type}`}
      </span>
      {d.warehouse && (
        <span className="block text-xs text-accent" data-testid="preferred-warehouse">
          → склад {d.warehouse.name}
        </span>
      )}
    </>
  )
}

/** Откуда число «отправить»: рекомендация Ozon (нижняя планка) или продажи, и довели ли до минимума на склад. */
function Basis({ item, minQty }: { item: SupplyItem; minQty: number }) {
  if (item.kind !== 'supply') return <span className="block text-xs text-faint">закупить</span>
  const ozon = item.ozon_recommended === null ? 'Ozon — нет данных' : `Ozon советует ${item.ozon_recommended}`
  const own = item.own_need === null ? 'продаж здесь нет' : `по продажам ${item.own_need}`
  return (
    <>
      <span className="block text-xs text-faint" data-testid="supply-basis">
        <span className={item.basis === 'ozon' ? 'font-medium text-fg' : ''}>{ozon}</span> ·{' '}
        <span className={item.basis === 'sales' ? 'font-medium text-fg' : ''}>{own}</span>
      </span>
      {item.min_applied && <span className="block text-xs text-warn">до минимума {minQty} шт. на склад</span>}
      {item.days_after !== null && <span className="block text-xs text-faint">хватит на {num(item.days_after)} дн. после</span>}
    </>
  )
}

function Sales({ item }: { item: SupplyItem }) {
  if (item.kind === 'supply')
    return (
      <>
        <span className="tabular-nums">{perDay(item.avg_daily)}</span>
        <span className="block text-xs text-faint">в кластере, Ozon</span>
      </>
    )
  // стрелка — только при заметной разнице (больше 10 %): иначе 0,57 ↑ 0,57 после округления
  const up = Number(item.rate) > Number(item.avg_daily) * 1.1
  const down = Number(item.rate) < Number(item.avg_daily) * 0.9
  return (
    <>
      <span className="tabular-nums">{perDay(item.avg_daily)}</span>
      <span className={`block text-xs ${up ? 'text-ok' : down ? 'text-warn' : 'text-faint'}`}>
        за 7 дн. {perDay(item.rate)} {up ? '↑' : down ? '↓' : ''}
      </span>
    </>
  )
}

function Planned({ item }: { item: SupplyItem }) {
  if (!item.in_plans && !item.in_orders) return null
  return (
    <span className="block text-xs text-faint" data-testid="already-planned">
      {item.in_plans > 0 && `в планах ${item.in_plans}`}
      {item.in_plans > 0 && item.in_orders > 0 && ' · '}
      {item.in_orders > 0 && `в заявках ${item.in_orders}`}
      {' · '}
      <span className={item.still_needed > 0 ? 'text-warn' : 'text-ok'}>{item.still_needed > 0 ? `нужно ещё ${item.still_needed}` : 'хватает'}</span>
    </span>
  )
}

function Pick({ item, picked, onToggle, onQty }: { item: SupplyItem; picked: Picked; onToggle: (items: SupplyItem[], on: boolean) => void; onQty: (item: SupplyItem, qty: string) => void }) {
  const p = picked[pickKey(item)]
  return (
    <span className="inline-flex items-center gap-2">
      <input type="checkbox" checked={Boolean(p)} onChange={(e) => onToggle([item], e.target.checked)} aria-label={`В план: ${item.product.offer_id}`} data-testid="pick" />
      {p && (
        <input
          inputMode="numeric"
          value={p.qty}
          onChange={(e) => onQty(item, e.target.value.replace(/\D/g, '').slice(0, 6))}
          className="w-20 rounded-lg border border-line bg-bg px-2 py-0.5 text-right text-sm tabular-nums outline-none focus:border-accent"
          aria-label="Сколько в план"
          data-testid="pick-qty"
        />
      )}
    </span>
  )
}

// ── 10.18a: как в Ozon — группы по кластерам или по товарам, раскрываются; статус у каждой строки ──
type GroupT = {
  key: string
  title: string
  sub: string | null
  href: string | null // товар — на страницу товара
  kind: SupplyKind | null // по кластерам — куда: кластер Ozon или свой склад
  items: SupplyItem[]
  status: RowStatus
  units: number
  ozon: number | null
  ads: number
  stock: number
  transit: number
  days: number | null // по товарам — весь остаток / продажи; по кластерам — кто кончится первым
  needed: SupplyItem[]
}

const rank = (s: RowStatus) => STATUS_ORDER.indexOf(s)
const daysOf = (i: SupplyItem) => (i.days_left === null ? Number.POSITIVE_INFINITY : Number(i.days_left))

function matches(i: SupplyItem, show: SupplyShow) {
  if (show === 'all') return true
  if (show === 'needed') return i.to_order > 0
  if (show === 'first') return i.first
  return i.status === show
}

function buildGroups(rows: SupplyItem[], view: SupplyView): GroupT[] {
  const map = new Map<string, GroupT>()
  for (const i of rows) {
    const key = view === 'clusters' ? `${i.kind}|${i.destination.key}` : i.product.id
    let g = map.get(key)
    if (!g) {
      const head =
        view === 'clusters'
          ? {
              title: i.destination.name,
              sub:
                i.kind === 'supply'
                  ? i.destination.warehouse
                    ? `кластер Ozon → склад ${i.destination.warehouse.name}`
                    : 'кластер Ozon'
                  : `${KIND.purchase} · ${i.destination.warehouse_type}`,
              href: null,
              kind: i.kind,
            }
          : { title: i.product.offer_id, sub: i.product.name, href: productHref(i.product.id), kind: null }
      g = { key, ...head, items: [], status: 'idle', units: 0, ozon: null, ads: 0, stock: 0, transit: 0, days: null, needed: [] }
      map.set(key, g)
    }
    g.items.push(i)
  }
  const groups = [...map.values()]
  for (const g of groups) {
    g.items.sort((a, b) => rank(a.status) - rank(b.status) || daysOf(a) - daysOf(b) || Number(b.avg_daily) - Number(a.avg_daily))
    g.status = g.items[0].status
    g.needed = g.items.filter((i) => i.to_order > 0)
    g.units = g.needed.reduce((s, i) => s + i.to_order, 0)
    const recs = g.items.filter((i) => i.kind === 'supply' && i.ozon_recommended !== null)
    g.ozon = recs.length ? recs.reduce((s, i) => s + (i.ozon_recommended ?? 0), 0) : null
    g.ads = g.items.reduce((s, i) => s + Number(i.avg_daily), 0)
    g.stock = g.items.reduce((s, i) => s + i.stock, 0)
    g.transit = g.items.reduce((s, i) => s + i.in_transit, 0)
    const soonest = Math.min(...g.items.map(daysOf))
    g.days = view === 'products' ? (g.ads > 0 ? g.stock / g.ads : null) : Number.isFinite(soonest) ? soonest : null
  }
  return groups.sort((a, b) => rank(a.status) - rank(b.status) || b.units - a.units || b.ads - a.ads || a.title.localeCompare(b.title, 'ru'))
}

function StatusBadge({ item, status }: { item?: SupplyItem; status: RowStatus }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <span className={`whitespace-nowrap rounded px-1.5 py-0.5 text-xs font-medium ${STATUS[status].badge}`} data-testid="row-status">
        {STATUS[status].label}
      </span>
      {item?.first && <span className="whitespace-nowrap rounded bg-accent/15 px-1.5 py-0.5 text-xs text-accent">впервые</span>}
    </span>
  )
}

/** Статус строки и почему: «Пока хватает» / «Не поставлять» — причина; статус и причины Ozon. */
function StatusCell({ item, minQty }: { item: SupplyItem; minQty: number }) {
  const skip = skipText(item, minQty)
  return (
    <>
      <StatusBadge item={item} status={item.status} />
      {skip && <span className="block text-xs text-faint">{skip}</span>}
      {item.ozon_grade && GRADE_TEXT[item.ozon_grade] && <span className="block text-xs text-faint">{GRADE_TEXT[item.ozon_grade]}</span>}
      {item.ozon_reasons.map((r) => (
        <span key={r} className="block text-xs text-warn">
          Ozon: {r}
        </span>
      ))}
    </>
  )
}

function ProductCell({ item }: { item: SupplyItem }) {
  return (
    <>
      <Link href={productHref(item.product.id)} className="block truncate font-medium hover:text-accent">
        {item.product.offer_id}
      </Link>
      {item.product.name && <div className="max-w-[16rem] truncate text-xs text-muted">{item.product.name}</div>}
      {item.kind === 'supply' && (
        <div className="text-xs text-faint">{item.destination.warehouses.length ? `лежит: ${item.destination.warehouses.join(', ')}` : 'в кластере нет'}</div>
      )}
    </>
  )
}

function Send({ item, minQty }: { item: SupplyItem; minQty: number }) {
  return (
    <>
      {item.to_order > 0 ? <span className="font-semibold tabular-nums">{pieces(item.to_order)}</span> : <span className="text-muted">—</span>}
      <Basis item={item} minQty={minQty} />
      <Planned item={item} />
    </>
  )
}

function GroupActions({ g, picked, onToggle, onSupply, compact }: {
  g: GroupT
  picked: Picked
  onToggle: (items: SupplyItem[], on: boolean) => void
  onSupply: (items: SupplyItem[]) => Promise<void>
  compact?: boolean
}) {
  const [busy, setBusy] = useState(false)
  if (!g.needed.length) return null
  const all = g.needed.every((i) => picked[pickKey(i)])
  const left = g.needed.filter((i) => i.still_needed > 0)
  return (
    <span className={`inline-flex flex-wrap items-center gap-x-3 gap-y-1 ${compact ? '' : 'justify-end'}`}>
      <label className="inline-flex items-center gap-1 text-xs text-muted">
        <input type="checkbox" checked={all} onChange={(e) => onToggle(g.needed, e.target.checked)} data-testid="group-pick" />
        {compact ? 'выбрать в план' : 'в план'}
      </label>
      {g.kind && (
        <button
          type="button"
          disabled={busy || left.length === 0}
          onClick={async () => {
            setBusy(true)
            await onSupply(g.needed)
            setBusy(false)
          }}
          title={
            left.length
              ? 'Создаст план поставки сюда с рекомендованным количеством — его можно поправить и отправить заявкой в Ozon'
              : 'Всё уже в планах или заявках'
          }
          className="whitespace-nowrap rounded-lg border border-accent px-2.5 py-1 text-xs text-accent hover:bg-accent/10 disabled:opacity-50"
          data-testid="group-supply"
        >
          {g.kind === 'supply' ? 'Сделать поставку' : 'План закупки'}
        </button>
      )}
    </span>
  )
}

function groupDays(g: GroupT, view: SupplyView) {
  if (g.days === null) return <span className="text-muted">—</span>
  if (g.days === 0)
    return <span className={`font-medium ${STATUS[g.status].text}`}>{g.items.length > 1 ? 'у части кончился' : 'кончился'}</span>
  return (
    <span className={`font-medium ${STATUS[g.status].text}`}>
      {view === 'clusters' && g.items.length > 1 ? 'от ' : ''}
      {num(g.days.toFixed(1))} дн.
    </span>
  )
}

function Grouped({ data, view, show, onView, onShow, lead, picked, onToggle, onQty, onSupply }: {
  data: SupplyReport
  view: SupplyView
  show: SupplyShow
  onView: (v: SupplyView) => void
  onShow: (s: SupplyShow) => void
  lead: number
  picked: Picked
  onToggle: (items: SupplyItem[], on: boolean) => void
  onQty: (item: SupplyItem, qty: string) => void
  onSupply: (items: SupplyItem[]) => Promise<void>
}) {
  const [open, setOpen] = useState<Set<string>>(new Set())
  useEffect(() => setOpen(new Set()), [view])
  const all = [...data.items, ...data.rest]
  const counts: Record<SupplyShow, number> = {
    all: all.length,
    needed: data.items.length,
    critical: data.totals.critical,
    urgent: data.totals.urgent,
    planned: data.totals.planned,
    first: data.totals.first,
  }
  const groups = buildGroups(all.filter((i) => matches(i, show)), view)
  const allOpen = groups.length > 0 && groups.every((g) => open.has(g.key))
  const flip = (key: string) =>
    setOpen((o) => {
      const next = new Set(o)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  const minQty = data.min_qty

  return (
    <section className="rounded-xl border border-line bg-card" data-testid="supply-groups">
      <div className="space-y-2 border-b border-line px-4 py-3">
        <div className="flex flex-wrap items-center gap-2" data-testid="supply-toolbar">
          <div role="group" aria-label="Группировка" className="inline-flex rounded-lg border border-line p-0.5">
            {(['clusters', 'products'] as const).map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => onView(v)}
                aria-pressed={view === v}
                className={`rounded-md px-3 py-1 text-sm ${view === v ? 'bg-accent text-white' : 'text-muted hover:text-fg'}`}
                data-testid={`supply-view-${v}`}
              >
                {v === 'clusters' ? 'По кластерам' : 'По товарам'}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap gap-1">
            {SHOW.map((s) => (
              <button
                key={s.value}
                type="button"
                onClick={() => onShow(s.value)}
                aria-pressed={show === s.value}
                className={`whitespace-nowrap rounded-lg border px-2.5 py-1 text-xs ${show === s.value ? 'border-accent text-accent' : 'border-line text-muted hover:text-fg'}`}
                data-testid={`supply-show-${s.value}`}
              >
                {s.label} <span className="tabular-nums">{counts[s.value]}</span>
              </button>
            ))}
          </div>
          {groups.length > 0 && (
            <button
              type="button"
              onClick={() => setOpen(allOpen ? new Set() : new Set(groups.map((g) => g.key)))}
              className="ml-auto text-xs text-accent hover:underline"
              data-testid="expand-all"
            >
              {allOpen ? 'Свернуть все' : 'Развернуть все'}
            </button>
          )}
        </div>
        <p className="text-xs text-faint">{VIEW_WHY}</p>
      </div>

      {groups.length === 0 ? (
        <p className="px-4 py-3 text-sm text-muted" data-testid="supply-none">
          {show === 'all' ? 'Данных Ozon по кластерам пока нет.' : 'Под этот фильтр ничего нет.'}
        </p>
      ) : (
        <>
          <ul className="divide-y divide-line sm:hidden">
            {groups.map((g) => (
              <li key={g.key} data-testid="supply-group-card">
                <button
                  type="button"
                  onClick={() => flip(g.key)}
                  aria-expanded={open.has(g.key)}
                  className={`flex w-full items-start gap-2 border-l-4 px-4 pt-3 text-left ${STATUS[g.status].border}`}
                  data-testid="group-toggle"
                >
                  <span className="mt-0.5 w-3 text-muted">{open.has(g.key) ? '▾' : '▸'}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{g.title}</span>
                    {g.sub && <span className="block truncate text-xs text-faint">{g.sub}</span>}
                    <span className="mt-1 block">
                      <StatusBadge status={g.status} />
                    </span>
                  </span>
                  <span className="shrink-0 text-right">
                    <span className="block font-semibold tabular-nums">{g.units ? pieces(g.units) : '—'}</span>
                    <span className="block text-xs text-faint">{g.needed.length ? `${g.needed.length} из ${g.items.length} везти` : `${g.items.length} — не везти`}</span>
                  </span>
                </button>
                <div className={`flex flex-wrap items-center gap-x-3 gap-y-1 border-l-4 px-4 pb-3 pl-9 pt-1 text-xs text-muted ${STATUS[g.status].border}`}>
                  <span>{perDay(g.ads.toFixed(3))} в день</span>
                  <span>
                    там {g.stock}
                    {g.transit ? ` + в пути ${g.transit}` : ''}
                  </span>
                  <span>
                    {g.days === 0 ? '' : 'хватит на '}
                    {groupDays(g, view)}
                  </span>
                  {g.ozon !== null && <span>Ozon советует {g.ozon}</span>}
                  {g.href && (
                    <Link href={g.href} className="text-accent">
                      товар →
                    </Link>
                  )}
                  <GroupActions g={g} picked={picked} onToggle={onToggle} onSupply={onSupply} compact />
                </div>
                {open.has(g.key) && (
                  <ul className="divide-y divide-line border-t border-line bg-bg/40">
                    {g.items.map((i) => (
                      <li key={pickKey(i)} className={`border-l-4 px-4 py-3 pl-9 ${STATUS[i.status].border}`} data-testid="supply-card">
                        <div className="flex items-start gap-3">
                          <div className="min-w-0 flex-1 text-sm">{view === 'clusters' ? <ProductCell item={i} /> : <Where item={i} />}</div>
                          <div className="shrink-0 text-right">
                            {i.to_order > 0 ? <span className="font-semibold tabular-nums">{pieces(i.to_order)}</span> : <span className="text-muted">—</span>}
                            <span className="block text-xs text-faint">{i.kind === 'supply' ? 'отправить' : 'закупить'}</span>
                          </div>
                        </div>
                        <div className="text-xs">
                          {i.kind === 'supply' && <Basis item={i} minQty={minQty} />}
                          <Planned item={i} />
                        </div>
                        <div className="mt-1 text-sm">
                          <StatusCell item={i} minQty={minQty} />
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-x-3 text-sm">
                          {i.to_order > 0 && <Pick item={i} picked={picked} onToggle={onToggle} onQty={onQty} />}
                          <span>
                            <span className="text-muted">хватит на </span>
                            <Days item={i} lead={lead} />
                          </span>
                          <span className="text-muted">
                            {i.kind === 'supply' ? `${perDay(i.avg_daily)} в день по Ozon` : `${perDay(i.avg_daily)} в день (7 дн.: ${perDay(i.rate)})`}
                          </span>
                          <span className="text-muted">
                            там {i.stock}
                            {i.in_transit ? ` + в пути ${i.in_transit}` : ''}
                          </span>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
          <div className="hidden overflow-x-auto sm:block">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="px-3 py-2 font-normal">{view === 'clusters' ? 'Кластер → товар' : 'Товар → куда'}</th>
                  <th className="px-3 py-2 font-normal">Статус</th>
                  <th className="px-3 py-2 font-normal" title="Остаток сейчас / продажи в день; у кластера — кто кончится первым">
                    Хватит на
                  </th>
                  <th className="px-3 py-2 text-right font-normal" title="Склады Ozon — продажи в день в кластере по Ozon (28 дн.); свой склад — по заказам">
                    Продажи/день
                  </th>
                  <th className="px-3 py-2 text-right font-normal">Сейчас там</th>
                  <th className="px-3 py-2 text-right font-normal" title="Не меньше рекомендации Ozon и минимума на склад; по продажам — продажи в день × (запас + страховой + дни в пути) − остаток − в пути">
                    Отправить
                  </th>
                </tr>
              </thead>
              {groups.map((g) => (
                <tbody key={g.key}>
                  <tr className={`border-b border-l-4 border-b-line bg-bg/40 ${STATUS[g.status].border}`} data-testid="supply-group">
                    <td className="px-3 py-2">
                      <button type="button" onClick={() => flip(g.key)} aria-expanded={open.has(g.key)} className="flex items-start gap-2 text-left" data-testid="group-toggle">
                        <span className="mt-0.5 w-3 text-muted">{open.has(g.key) ? '▾' : '▸'}</span>
                        <span>
                          <span className="block font-medium">{g.title}</span>
                          {g.sub && <span className="block max-w-[18rem] truncate text-xs text-faint">{g.sub}</span>}
                        </span>
                      </button>
                      {g.href && (
                        <Link href={g.href} className="ml-5 text-xs text-accent hover:underline">
                          статистика товара →
                        </Link>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <StatusBadge status={g.status} />
                      <span className="block text-xs text-faint">
                        {g.needed.length ? `${g.needed.length} из ${g.items.length} — везти` : `${g.items.length} — везти не нужно`}
                      </span>
                    </td>
                    <td className="px-3 py-2">{groupDays(g, view)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{perDay(g.ads.toFixed(3))}</td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {g.stock}
                      {g.transit > 0 && <span className="block text-xs text-faint">+ в пути {g.transit}</span>}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <span className="font-semibold tabular-nums">{g.units ? pieces(g.units) : '—'}</span>
                      {g.ozon !== null && <span className="block text-xs text-faint">Ozon советует {g.ozon}</span>}
                      <GroupActions g={g} picked={picked} onToggle={onToggle} onSupply={onSupply} />
                    </td>
                  </tr>
                  {open.has(g.key) &&
                    g.items.map((i) => (
                      <tr key={pickKey(i)} className={`border-b border-l-4 border-b-line ${STATUS[i.status].border}`} data-testid="supply-row">
                        <td className="py-2 pl-8 pr-3">
                          {view === 'clusters' ? <ProductCell item={i} /> : <Where item={i} />}
                        </td>
                        <td className="px-3 py-2">
                          <StatusCell item={i} minQty={minQty} />
                        </td>
                        <td className="px-3 py-2">
                          <Days item={i} lead={lead} />
                        </td>
                        <td className="px-3 py-2 text-right">
                          <Sales item={i} />
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums">
                          {i.stock}
                          {i.in_transit > 0 && <span className="block text-xs text-faint">+ в пути {i.in_transit}</span>}
                        </td>
                        <td className="px-3 py-2 text-right">
                          <Send item={i} minQty={minQty} />
                          {i.to_order > 0 && (
                            <span className="mt-1 flex justify-end">
                              <Pick item={i} picked={picked} onToggle={onToggle} onQty={onQty} />
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                </tbody>
              ))}
            </table>
          </div>
        </>
      )}
    </section>
  )
}

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
  SAFETY_WHY,
  type SupplyItem,
  type SupplyParams,
  type SupplyReport,
  TARGET_WHY,
  URGENCY,
  URGENCY_ORDER,
  type Urgency,
  urgencyHint,
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
      {tab === 'recommendations' && <Recommendations onPlans={(id) => openPlan(id)} />}
      {tab === 'plans' && <PlansTab openId={params.get('plan')} onOpen={openPlan} onOrder={(id) => openOrder(id)} />}
      {tab === 'orders' && <OrdersTab openId={params.get('order')} onOpen={openOrder} onPlan={(id) => openPlan(id)} />}
      {tab === 'calendar' && <CalendarTab onPlan={(id) => openPlan(id)} onOrder={(id) => openOrder(id)} />}
    </div>
  )
}

function Recommendations({ onPlans }: { onPlans: (id: string | null) => void }) {
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

  async function createPlans(day: string) {
    setError(null)
    const groups = new Map<string, { item: SupplyItem; qty: string }[]>()
    for (const p of Object.values(picked)) {
      const k = `${p.item.kind}|${p.item.destination.key}`
      groups.set(k, [...(groups.get(k) ?? []), p])
    }
    try {
      const made: string[] = []
      for (const rows of groups.values()) {
        const d = rows[0].item
        const { data: plan } = await api.post<PlanDetail>('/supply/plans', {
          kind: d.kind,
          destination_key: d.destination.key,
          destination_name: d.destination.name,
          planned_date: day || null,
          items: rows.map((r) => ({ product_id: r.item.product.id, qty: Number(r.qty || 0), recommended: Number(defaultQty(r.item)) })),
        })
        made.push(plan.id)
      }
      setPicked({})
      onPlans(made.length === 1 ? made[0] : null)
    } catch (err) {
      setError(errorMessage(err, 'План не создался — проверьте количества'))
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

function Result({ data, busy, onExport, onSync, syncing, picked, onToggle, onQty }: {
  data: SupplyReport
  busy: boolean
  onExport: () => void
  onSync: () => void
  syncing: boolean
  picked: Picked
  onToggle: (items: SupplyItem[], on: boolean) => void
  onQty: (item: SupplyItem, qty: string) => void
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
  const groups = URGENCY_ORDER.map((u) => ({ u, items: data.items.filter((i) => i.urgency === u) }))
  return (
    <section className={`space-y-4 transition-opacity ${busy ? 'opacity-60' : ''}`} aria-busy={busy} data-testid="supply-result">
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-5" data-testid="supply-totals">
        {URGENCY_ORDER.map((u) => (
          <a key={u} href={`#group-${u}`} className="bg-card p-4 hover:bg-bg">
            <div className="text-xs text-muted">{URGENCY[u].label}</div>
            <div className={`mt-1 text-xl font-semibold tabular-nums ${data.totals[u] ? URGENCY[u].text : ''}`} data-testid={`total-${u}`}>
              {data.totals[u]}
            </div>
            <div className="text-xs text-faint">{u === 'planned' ? 'запаса меньше цели' : urgencyHint(u, t).split(' — ')[0]}</div>
          </a>
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

      {data.items.length === 0 ? (
        <Empty testid="supply-none">
          Везти ничего не нужно: Ozon не советует поставок, а запаса в кластерах хватает на {data.params.target_days} дн. и дорогу.
        </Empty>
      ) : (
        groups.map(({ u, items }) => (
          <Group key={u} urgency={u} items={items} hint={urgencyHint(u, t)} lead={data.params.lead_time_days} minQty={data.min_qty} picked={picked} onToggle={onToggle} onQty={onQty} />
        ))
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
      <span className={`font-medium ${URGENCY[item.urgency].text}`}>{item.stock === 0 ? 'нет на складе' : `${num(item.days_left)} дн.`}</span>
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
      {item.ozon_grade && GRADE_TEXT[item.ozon_grade] && <span className="block text-xs text-faint">{GRADE_TEXT[item.ozon_grade]}</span>}
      {item.ozon_reasons.map((r) => (
        <span key={r} className="block text-xs text-warn">
          Ozon: {r}
        </span>
      ))}
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

function Group({ urgency, items, hint, lead, minQty, picked, onToggle, onQty }: {
  urgency: Urgency
  items: SupplyItem[]
  hint: string
  lead: number
  minQty: number
  picked: Picked
  onToggle: (items: SupplyItem[], on: boolean) => void
  onQty: (item: SupplyItem, qty: string) => void
}) {
  const meta = URGENCY[urgency]
  const units = items.reduce((s, i) => s + i.to_order, 0)
  const all = items.length > 0 && items.every((i) => picked[pickKey(i)])
  return (
    <section id={`group-${urgency}`} className="scroll-mt-4 rounded-xl border border-line bg-card" data-testid={`group-${urgency}`}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line px-4 py-3">
        <h2 className={`font-semibold ${meta.text}`}>{meta.label}</h2>
        <span className="text-sm text-muted">{items.length ? `${items.length} · ${pieces(units)}` : 'нет'}</span>
        <span className="text-xs text-faint">{hint}</span>
        {items.length > 0 && (
          <button type="button" onClick={() => onToggle(items, !all)} className="ml-auto text-xs text-accent hover:underline" data-testid={`pick-all-${urgency}`}>
            {all ? 'Снять выбор' : 'Выбрать все в план'}
          </button>
        )}
      </div>
      {items.length > 0 && (
        <>
          <ul className="divide-y divide-line sm:hidden">
            {items.map((i) => (
              <li key={`${i.kind}-${i.destination.key}-${i.product.id}`} className={`border-l-4 px-4 py-3 ${meta.border}`} data-testid="supply-card">
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <Link href={productHref(i.product.id)} className="block truncate font-medium">
                      {i.product.offer_id}
                    </Link>
                    {i.product.name && <div className="truncate text-xs text-muted">{i.product.name}</div>}
                  </div>
                  <div className="shrink-0 text-right">
                    <div className="font-semibold tabular-nums">{pieces(i.to_order)}</div>
                    <div className="text-xs text-faint">{i.kind === 'supply' ? 'отправить' : 'закупить'}</div>
                  </div>
                </div>
                <div className="text-right">
                  <Basis item={i} minQty={minQty} />
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-x-3 text-sm">
                  <Pick item={i} picked={picked} onToggle={onToggle} onQty={onQty} />
                  <Planned item={i} />
                </div>
                <div className="mt-1 text-sm">
                  <Where item={i} />
                </div>
                <div className="mt-1 flex flex-wrap gap-x-3 text-sm">
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
          <div className="hidden overflow-x-auto sm:block">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="px-4 py-2 font-normal">В план</th>
                  <th className="px-4 py-2 font-normal">Товар</th>
                  <th className="px-4 py-2 font-normal">Куда</th>
                  <th className="px-4 py-2 font-normal" title="Остаток сейчас / продажи в день за последние 7 дней">
                    Хватит на
                  </th>
                  <th className="px-4 py-2 text-right font-normal" title="Склады Ozon — продажи в день в кластере по Ozon; свой склад — по заказам за период и за 7 дней">
                    Продажи/день
                  </th>
                  <th className="px-4 py-2 text-right font-normal">Сейчас там</th>
                  <th className="px-4 py-2 text-right font-normal" title="Не меньше рекомендации Ozon и минимума на склад; по продажам — продажи в день × (запас + страховой + дни в пути) − остаток − в пути">
                    Отправить
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((i) => (
                  <tr key={`${i.kind}-${i.destination.key}-${i.product.id}`} className={`border-b border-l-4 border-b-line last:border-b-0 ${meta.border}`} data-testid="supply-row">
                    <td className="whitespace-nowrap px-4 py-2">
                      <Pick item={i} picked={picked} onToggle={onToggle} onQty={onQty} />
                    </td>
                    <td className="px-4 py-2">
                      <Link href={productHref(i.product.id)} className="font-medium hover:text-accent">
                        {i.product.offer_id}
                      </Link>
                      {i.product.name && <div className="max-w-[16rem] truncate text-xs text-muted">{i.product.name}</div>}
                    </td>
                    <td className="px-4 py-2">
                      <Where item={i} />
                    </td>
                    <td className="px-4 py-2">
                      <Days item={i} lead={lead} />
                    </td>
                    <td className="px-4 py-2 text-right">
                      <Sales item={i} />
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums">
                      {i.stock}
                      {i.in_transit > 0 && <span className="block text-xs text-faint">+ в пути {i.in_transit}</span>}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <span className="font-semibold tabular-nums">{pieces(i.to_order)}</span>
                      <Basis item={i} minQty={minQty} />
                      <Planned item={i} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}

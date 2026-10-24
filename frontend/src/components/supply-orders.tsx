'use client'
// Заявки на поставку Ozon и календарь (шаг 10.18). Заявки — из синхронизации (раз в 3 часа или «Обновить»): статус и
// даты, таймслот, склад и кластер, заявлено и принято; в заявке — история статусов, что привезли против плана
// (план → заявлено → принято), как продавалось после приёмки. Календарь — даты планов, таймслоты, дни приёмки.
import Link from 'next/link'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { fmtDateTime, fmtDay } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { saveFile } from '@/lib/download'
import { refreshSyncStatus, timeAgo } from '@/lib/sync'
import { productHref } from '@/lib/stocks'
import {
  type CalendarEvent,
  GROUP_ORDER,
  GROUP_TONE,
  type OrderGroup,
  type SupplyOrder,
  type SupplyOrderDetail,
  type SupplyOrders,
  pieces,
} from '@/lib/supply'

type KindStatus = { last_run: { status: string; error: string | null } | null; last_success_at: string | null } | null
const POLL_MS = 3000
const diffTone = (d: number | null) => (d === null ? 'text-faint' : d < 0 ? 'text-bad' : d > 0 ? 'text-warn' : 'text-ok')
const signed = (d: number | null) => (d === null ? '—' : d > 0 ? `+${d}` : String(d))

export function OrdersTab({ openId, onOpen, onPlan }: { openId: string | null; onOpen: (id: string | null) => void; onPlan: (id: string) => void }) {
  const [group, setGroup] = useState<OrderGroup | ''>('')
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [data, setData] = useState<SupplyOrders | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sync, setSync] = useState<KindStatus>(null)
  const [hasKey, setHasKey] = useState(true)
  const [reload, setReload] = useState(0)
  const wasActive = useRef(false)

  useEffect(() => {
    const t = setTimeout(() => setQuery(search.trim()), 300)
    return () => clearTimeout(t)
  }, [search])
  useEffect(() => {
    let alive = true
    api
      .get<SupplyOrders>('/supply/orders', { params: { ...(group ? { group } : {}), ...(query ? { search: query } : {}) } })
      .then(({ data: d }) => alive && setData(d))
      .catch((err) => alive && setError(errorMessage(err)))
    return () => {
      alive = false
    }
  }, [group, query, reload])

  const loadSync = useCallback(async () => {
    try {
      const { data: s } = await api.get<{ has_active_key: boolean; supplies: KindStatus }>('/sync/status')
      const now = ['queued', 'running'].includes(s.supplies?.last_run?.status ?? '')
      if (wasActive.current && !now) setReload((n) => n + 1) // заявки загрузились — перечитать
      wasActive.current = now
      setHasKey(s.has_active_key)
      setSync(s.supplies)
    } catch {
      /* статус синхронизации — не главное на странице */
    }
  }, [])
  useEffect(() => {
    loadSync()
  }, [loadSync])
  const active = ['queued', 'running'].includes(sync?.last_run?.status ?? '')
  useEffect(() => {
    if (!active) return
    const t = setInterval(loadSync, POLL_MS)
    return () => clearInterval(t)
  }, [active, loadSync])

  async function syncNow() {
    setError(null)
    try {
      await api.post('/sync/supplies')
      refreshSyncStatus()
    } catch (err) {
      if ((err as { response?: { status?: number } }).response?.status !== 409) setError(errorMessage(err))
    } finally {
      await loadSync()
    }
  }

  async function exportXlsx() {
    try {
      await saveFile(api.get('/supply/orders/export', { params: { ...(group ? { group } : {}), ...(query ? { search: query } : {}) }, responseType: 'blob' }), 'supply-orders.xlsx')
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <section className="space-y-3" data-testid="orders">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Статус заявок">
          <Chip on={!group} onClick={() => setGroup('')}>
            Все{data ? ` · ${data.total}` : ''}
          </Chip>
          {data &&
            GROUP_ORDER.map((g) => (
              <Chip key={g} on={group === g} onClick={() => setGroup(group === g ? '' : g)} testid={`orders-${g}`}>
                {data.group_text[g]} · {data.counts[g]}
              </Chip>
            ))}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Номер, артикул, кластер"
          aria-label="Поиск заявок"
          className="min-w-0 flex-1 rounded-lg border border-line bg-bg px-3 py-1.5 text-sm outline-none focus:border-accent sm:max-w-xs"
          data-testid="orders-search"
        />
        <div className="flex items-center gap-2 text-sm sm:ml-auto">
          {hasKey && (
            <>
              <span className="text-xs text-faint" data-testid="orders-synced">
                {active ? 'Заявки загружаются…' : sync?.last_success_at ? `обновлены ${timeAgo(sync.last_success_at)}` : 'ещё не загружались'}
              </span>
              <button type="button" onClick={syncNow} disabled={active} className="rounded-lg border border-line px-3 py-1.5 hover:border-accent hover:text-accent disabled:opacity-60" data-testid="orders-sync">
                Обновить
              </button>
            </>
          )}
          <button type="button" onClick={exportXlsx} disabled={!data?.items.length} className="rounded-lg border border-line px-3 py-1.5 hover:border-accent hover:text-accent disabled:opacity-60" data-testid="orders-export">
            Excel
          </button>
        </div>
      </div>
      <FormError message={error ?? (sync?.last_run?.status === 'failed' ? `Заявки не загрузились: ${sync.last_run.error}` : null)} />
      {!hasKey && (
        <p className="text-sm text-warn">
          Заявки на поставку приходят только из Ozon API —{' '}
          <Link href="/dashboard/settings/integrations" className="underline">
            подключите API-ключ Ozon
          </Link>
          .
        </p>
      )}
      {data === null ? (
        <p className="text-sm text-muted">Загрузка…</p>
      ) : data.items.length === 0 ? (
        <p className="rounded-xl border border-line bg-card px-4 py-3 text-sm text-muted" data-testid="orders-empty">
          {data.total ? 'По фильтру заявок нет.' : 'Заявок на поставку пока нет — или они ещё не загрузились.'}
        </p>
      ) : (
        <ul className="space-y-2" data-testid="orders-list">
          {data.items.map((o) => (
            <li key={o.id} className="rounded-xl border border-line bg-card" data-testid="order-row">
              <button type="button" onClick={() => onOpen(openId === o.id ? null : o.id)} aria-expanded={openId === o.id} className="block w-full px-4 py-3 text-left hover:bg-bg/50">
                <OrderLine o={o} />
              </button>
              {openId === o.id && <OrderView id={o.id} onPlan={onPlan} />}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function Chip({ on, onClick, children, testid }: { on: boolean; onClick: () => void; children: React.ReactNode; testid?: string }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={`rounded-full border px-3 py-1 text-sm ${on ? 'border-accent bg-accent/10 text-accent' : 'border-line hover:border-muted'}`}
      data-testid={testid}
    >
      {children}
    </button>
  )
}

function OrderLine({ o }: { o: SupplyOrder }) {
  const diff = o.accepted !== null && o.declared !== null ? o.accepted - o.declared : null
  return (
    <div className="flex flex-wrap items-start gap-x-4 gap-y-1">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">№{o.number ?? o.ozon_order_id}</span>
          <span className={`rounded px-2 py-0.5 text-xs ${GROUP_TONE[o.group]}`}>{o.state_text}</span>
          {o.plan && <span className="text-xs text-faint">по плану</span>}
        </div>
        <div className="truncate text-xs text-muted">
          {o.cluster ?? '—'}
          {o.storage_warehouse ? ` · ${o.storage_warehouse}` : ''}
          {o.created_at ? ` · создана ${fmtDay(o.created_at.slice(0, 10))}` : ''}
        </div>
      </div>
      <div className="text-sm">
        <span className="text-xs text-muted">таймслот </span>
        {o.timeslot_day ? `${fmtDay(o.timeslot_day)}${o.timeslot_time ? ` ${o.timeslot_time}` : ''}` : '—'}
      </div>
      <div className="text-right text-sm tabular-nums">
        <span className="block">{pieces(o.declared)}</span>
        <span className={`block text-xs ${o.accepted === null ? 'text-faint' : diffTone(diff)}`}>
          {o.accepted === null ? 'актов нет' : `принято ${o.accepted}${diff ? ` (${signed(diff)})` : ''}`}
        </span>
      </div>
    </div>
  )
}

function OrderView({ id, onPlan }: { id: string; onPlan: (id: string) => void }) {
  const [d, setD] = useState<SupplyOrderDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api
      .get<SupplyOrderDetail>(`/supply/orders/${id}`)
      .then(({ data }) => setD(data))
      .catch((err) => setError(errorMessage(err)))
  }, [id])
  if (!d) return <div className="border-t border-line px-4 py-3 text-sm text-muted">{error ? <FormError message={error} /> : 'Загрузка…'}</div>
  const t = d.totals
  return (
    <div className="space-y-3 border-t border-line px-4 py-3" data-testid="order-view">
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
        {d.plan && (
          <span>
            План:{' '}
            <button type="button" onClick={() => onPlan(d.plan!.id)} className="text-accent hover:underline">
              {d.plan.destination_name}
            </button>
          </span>
        )}
        {d.dropoff && <span className="text-muted">отгрузка: {d.dropoff}</span>}
        {d.accepted_on && <span className="text-muted">принята {fmtDay(d.accepted_on)}</span>}
      </div>
      <ol className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted" data-testid="order-events">
        {d.events.map((e, i) => (
          <li key={i}>
            <span className="text-fg">{e.state_text}</span> · {fmtDateTime(e.at)}
          </li>
        ))}
      </ol>
      <div className="text-sm" data-testid="order-totals">
        {t.planned !== null && <span className="mr-3">в плане {pieces(t.planned)}</span>}
        <span className="mr-3">заявлено {pieces(t.declared)}</span>
        {d.has_acts ? (
          <span className={diffTone(t.diff)}>
            принято {pieces(t.accepted)}
            {t.short > 0 && ` · недовоз ${pieces(t.short)}`}
          </span>
        ) : (
          <span className="text-faint">принято — актов приёмки ещё нет</span>
        )}
      </div>
      <div className="-mx-4 overflow-x-auto px-4">
        <table className="w-full min-w-[40rem] text-[13px]" data-testid="order-items">
          <thead className="text-left text-xs text-faint">
            <tr>
              <th className="py-1 pr-2 font-normal">Товар</th>
              <th className="py-1 pr-2 text-right font-normal">В плане</th>
              <th className="py-1 pr-2 text-right font-normal">Заявлено</th>
              <th className="py-1 pr-2 text-right font-normal">Принято</th>
              <th className="py-1 pr-2 font-normal" title="С дня приёмки (не больше 30 дней) против такого же числа дней до">
                Продажи после приёмки
              </th>
              <th className="py-1 text-right font-normal">Хватит на</th>
            </tr>
          </thead>
          <tbody>
            {d.items.map((i, k) => {
              const a = i.sales_after
              return (
                <tr key={i.product.id ?? `${i.product.offer_id}-${k}`} className="border-t border-line" data-testid="order-item">
                  <td className="max-w-[14rem] py-1.5 pr-2">
                    {i.product.id ? (
                      <Link href={productHref(i.product.id)} className="block truncate font-medium hover:text-accent">
                        {i.product.offer_id}
                      </Link>
                    ) : (
                      <span className="block truncate">{i.product.offer_id}</span>
                    )}
                    {!i.product.id && <span className="text-xs text-faint">нет в каталоге</span>}
                  </td>
                  <td className="py-1.5 pr-2 text-right tabular-nums text-muted">{i.planned ?? '—'}</td>
                  <td className="py-1.5 pr-2 text-right tabular-nums">
                    {i.declared ?? '—'}
                    {i.plan_diff ? <span className="block text-xs text-faint">к плану {signed(i.plan_diff)}</span> : null}
                  </td>
                  <td className="py-1.5 pr-2 text-right tabular-nums">
                    {i.accepted === null ? <span className="text-faint">—</span> : i.accepted}
                    {i.diff ? <span className={`block text-xs ${diffTone(i.diff)}`}>{signed(i.diff)}</span> : null}
                    {i.defect && <span className="block text-xs text-bad">брак</span>}
                  </td>
                  <td className="py-1.5 pr-2 text-xs text-muted">
                    {!a ? (
                      '—'
                    ) : !a.known ? (
                      'начислений после приёмки ещё нет'
                    ) : (
                      <>
                        {a.after_qty} шт. за {a.after_days_with_data} дн. с данными
                        {a.before_qty !== null && (
                          <span className={`block ${a.change_pct && Number(a.change_pct) < 0 ? 'text-warn' : ''}`}>
                            до приёмки {a.before_qty}
                            {a.change_pct ? ` (${Number(a.change_pct) > 0 ? '+' : ''}${Number(a.change_pct).toLocaleString('ru-RU')} %)` : ''}
                          </span>
                        )}
                      </>
                    )}
                  </td>
                  <td className="py-1.5 text-right text-xs tabular-nums">{i.lasts_days !== null ? `${i.lasts_days} дн.` : '—'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-faint">
        Принято — по актам приёмки Ozon; продажи — товара в целом (склада в начислениях нет) с дня приёмки, не больше 30 дней,
        против такого же числа дней до неё; дни без начислений не считаем. «Хватит на» — принятое / продажи в день после.
      </p>
    </div>
  )
}

// ── календарь ──
const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
const MONTHS = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']
const EVENT_TONE: Record<CalendarEvent['kind'], string> = {
  plan: 'bg-accent/15 text-accent',
  timeslot: 'bg-warn/15 text-warn',
  accepted: 'bg-ok/15 text-ok',
}
const EVENT_LABEL: Record<CalendarEvent['kind'], string> = { plan: 'план', timeslot: 'таймслот', accepted: 'принята' }
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

export function CalendarTab({ onPlan, onOrder }: { onPlan: (id: string) => void; onOrder: (id: string) => void }) {
  const [month, setMonth] = useState(() => {
    const d = new Date()
    return new Date(d.getFullYear(), d.getMonth(), 1)
  })
  const [events, setEvents] = useState<CalendarEvent[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const grid = useMemo(() => {
    const start = new Date(month)
    start.setDate(1 - ((month.getDay() + 6) % 7)) // с понедельника
    return Array.from({ length: 42 }, (_, i) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + i))
  }, [month])
  useEffect(() => {
    setEvents(null)
    api
      .get<{ events: CalendarEvent[] }>('/supply/calendar', { params: { date_from: iso(grid[0]), date_to: iso(grid[41]) } })
      .then(({ data }) => setEvents(data.events))
      .catch((err) => setError(errorMessage(err)))
  }, [grid])
  const byDay = useMemo(() => {
    const m: Record<string, CalendarEvent[]> = {}
    for (const e of events ?? []) (m[e.date] ??= []).push(e)
    return m
  }, [events])
  const today = iso(new Date())
  const open = (e: CalendarEvent) => (e.order_id ? onOrder(e.order_id) : e.plan_id ? onPlan(e.plan_id) : undefined)
  const inMonth = (events ?? []).filter((e) => e.date.slice(0, 7) === iso(month).slice(0, 7))

  return (
    <section className="space-y-3" data-testid="calendar">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))} className="rounded-lg border border-line px-3 py-1 hover:border-accent" aria-label="Предыдущий месяц">
          ‹
        </button>
        <span className="min-w-[9rem] text-center font-medium capitalize" data-testid="calendar-month">
          {MONTHS[month.getMonth()]} {month.getFullYear()}
        </span>
        <button type="button" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))} className="rounded-lg border border-line px-3 py-1 hover:border-accent" aria-label="Следующий месяц">
          ›
        </button>
        <span className="flex flex-wrap gap-2 text-xs sm:ml-auto">
          {(Object.keys(EVENT_LABEL) as CalendarEvent['kind'][]).map((k) => (
            <span key={k} className={`rounded px-1.5 py-0.5 ${EVENT_TONE[k]}`}>
              {EVENT_LABEL[k]}
            </span>
          ))}
        </span>
      </div>
      <FormError message={error} />
      <div className="hidden overflow-hidden rounded-xl border border-line bg-line sm:grid sm:grid-cols-7 sm:gap-px" data-testid="calendar-grid">
        {WEEKDAYS.map((w) => (
          <div key={w} className="bg-card px-2 py-1 text-xs text-muted">
            {w}
          </div>
        ))}
        {grid.map((d) => {
          const key = iso(d)
          const other = d.getMonth() !== month.getMonth()
          return (
            <div key={key} className={`min-h-[5.5rem] bg-card p-1.5 ${other ? 'opacity-50' : ''}`}>
              <div className={`text-xs ${key === today ? 'font-semibold text-accent' : 'text-muted'}`}>{d.getDate()}</div>
              <div className="mt-1 space-y-1">
                {(byDay[key] ?? []).map((e, i) => (
                  <button key={i} type="button" onClick={() => open(e)} title={`${e.title} · ${e.state_text}`} className={`block w-full truncate rounded px-1.5 py-0.5 text-left text-[11px] ${EVENT_TONE[e.kind]}`} data-testid="calendar-event">
                    {e.time ? `${e.time} ` : ''}
                    {e.title} · {e.units}
                  </button>
                ))}
              </div>
            </div>
          )
        })}
      </div>
      <ul className="space-y-2 sm:hidden" data-testid="calendar-agenda">
        {inMonth.length === 0 && events !== null && <li className="text-sm text-muted">В этом месяце поставок нет.</li>}
        {inMonth.map((e, i) => (
          <li key={i}>
            <button type="button" onClick={() => open(e)} className="flex w-full items-start gap-3 rounded-xl border border-line bg-card px-3 py-2 text-left">
              <span className="w-12 shrink-0 text-sm font-medium">{fmtDay(e.date).slice(0, 5)}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm">{e.title}</span>
                <span className="block text-xs text-muted">
                  {e.time ? `${e.time} · ` : ''}
                  {pieces(e.units)} · {e.state_text}
                </span>
              </span>
              <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs ${EVENT_TONE[e.kind]}`}>{EVENT_LABEL[e.kind]}</span>
            </button>
          </li>
        ))}
      </ul>
      <p className="text-xs text-faint">План — дата, на которую вы запланировали поставку; таймслот — время приёмки по заявке Ozon (по часовому поясу склада); принята — когда Ozon начал приёмку.</p>
    </section>
  )
}

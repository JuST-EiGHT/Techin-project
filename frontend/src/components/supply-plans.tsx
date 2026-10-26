'use client'
// Планы поставок (шаг 10.18): список и план — количество правится вручную (каждая правка сохраняется отдельной версией),
// дата для календаря, заметка, Excel, привязка заявки из кабинета Ozon. Заявка в Ozon по плану — по шагам (правило 6):
// «Проверить в Ozon» (черновик, в кабинете не виден) → склад → таймслот → подтверждение → заявка; запрос — в журнале.
import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { fmtDateTime, fmtDay } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { saveFile } from '@/lib/download'
import { productHref } from '@/lib/stocks'
import {
  type Draft,
  GROUP_TONE,
  PLAN_STATUS,
  type Plan,
  type PlanDetail,
  type PlanStatus,
  type SupplyOrders,
  type Timeslots,
  pieces,
} from '@/lib/supply'

const OZON_CABINET = 'https://seller.ozon.ru/'
const FILTERS: { value: PlanStatus | ''; label: string }[] = [
  { value: 'draft', label: 'В работе' },
  { value: 'ordered', label: 'Заявка в Ozon' },
  { value: 'cancelled', label: 'Отменённые' },
  { value: '', label: 'Все' },
]

export function PlansTab({ openId, onOpen, onOrder }: { openId: string | null; onOpen: (id: string | null) => void; onOrder: (id: string) => void }) {
  if (openId) return <PlanView id={openId} onBack={() => onOpen(null)} onOrder={onOrder} />
  return <PlanList onOpen={onOpen} />
}

function PlanList({ onOpen }: { onOpen: (id: string) => void }) {
  const [status, setStatus] = useState<PlanStatus | ''>('draft')
  const [plans, setPlans] = useState<Plan[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    setPlans(null)
    api
      .get<Plan[]>('/supply/plans', { params: status ? { status } : {} })
      .then(({ data }) => setPlans(data))
      .catch((err) => setError(errorMessage(err)))
  }, [status])
  return (
    <section className="space-y-3" data-testid="plans">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Какие планы">
        {FILTERS.map((f) => (
          <button
            key={f.value || 'all'}
            type="button"
            aria-pressed={status === f.value}
            onClick={() => setStatus(f.value)}
            className={`rounded-full border px-3 py-1 text-sm ${status === f.value ? 'border-accent bg-accent/10 text-accent' : 'border-line hover:border-muted'}`}
          >
            {f.label}
          </button>
        ))}
      </div>
      <FormError message={error} />
      {plans === null ? (
        <p className="text-sm text-muted">Загрузка…</p>
      ) : plans.length === 0 ? (
        <p className="rounded-xl border border-line bg-card px-4 py-3 text-sm text-muted" data-testid="plans-empty">
          {status === 'draft'
            ? 'Планов в работе нет. Отметьте товары во вкладке «Рекомендации», поправьте количество и нажмите «Создать план».'
            : 'Нет планов.'}
        </p>
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {plans.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => onOpen(p.id)}
                className="block w-full rounded-xl border border-line bg-card p-4 text-left hover:border-accent"
                data-testid="plan-card"
              >
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="text-xs text-muted">{p.kind_text}</div>
                    <div className="truncate font-medium">{p.destination_name}</div>
                  </div>
                  <span className={`shrink-0 rounded px-2 py-0.5 text-xs ${PLAN_STATUS[p.status].badge}`}>{PLAN_STATUS[p.status].label}</span>
                </div>
                <div className="mt-2 text-sm">
                  <span className="font-semibold tabular-nums">{pieces(p.units)}</span>
                  <span className="text-muted"> · товаров {p.products}</span>
                  {p.corrected > 0 && <span className="text-faint"> · поправлено вручную: {p.corrected}</span>}
                </div>
                <div className="mt-1 text-xs text-faint">
                  {p.planned_date ? `везти ${fmtDay(p.planned_date)}` : 'дата не назначена'}
                  {p.order && ` · заявка ${p.order.number ?? p.order.ozon_order_id}: ${p.order.state_text}`}
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function PlanView({ id, onBack, onOrder }: { id: string; onBack: () => void; onOrder: (id: string) => void }) {
  const [plan, setPlan] = useState<PlanDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const load = useCallback(async () => {
    try {
      const { data } = await api.get<PlanDetail>(`/supply/plans/${id}`)
      setPlan(data)
      setNote(data.note ?? '')
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [id])
  useEffect(() => {
    load()
  }, [load])

  async function patch(body: Record<string, unknown>) {
    setError(null)
    try {
      const { data } = await api.patch<PlanDetail>(`/supply/plans/${id}`, body)
      setPlan(data)
      setNote(data.note ?? '')
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  async function setQty(productId: string, qty: number) {
    setError(null)
    try {
      setPlan((await api.put<PlanDetail>(`/supply/plans/${id}/items/${productId}`, { qty })).data)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  async function exportXlsx() {
    try {
      await saveFile(api.get(`/supply/plans/${id}/export`, { responseType: 'blob' }), 'supply-plan.xlsx')
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  if (!plan)
    return (
      <div className="space-y-3">
        <button type="button" onClick={onBack} className="text-sm text-accent hover:underline">
          ← Все планы
        </button>
        {error ? <FormError message={error} /> : <p className="text-sm text-muted">Загрузка…</p>}
      </div>
    )
  const live = plan.items.filter((i) => i.qty > 0)
  return (
    <section className="space-y-4" data-testid="plan-view">
      <button type="button" onClick={onBack} className="text-sm text-accent hover:underline">
        ← Все планы
      </button>
      <div className="rounded-xl border border-line bg-card p-4">
        <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
          <div className="min-w-0 flex-1">
            <div className="text-xs text-muted">{plan.kind_text}</div>
            <h2 className="text-lg font-semibold">{plan.destination_name}</h2>
            <div className="text-sm text-muted" data-testid="plan-total">
              {pieces(plan.units)} · товаров {plan.products}
              {plan.recommended > 0 && ` · расчёт советовал ${pieces(plan.recommended)}`}
            </div>
          </div>
          <span className={`rounded px-2 py-0.5 text-xs ${PLAN_STATUS[plan.status].badge}`} data-testid="plan-status">
            {PLAN_STATUS[plan.status].label}
          </span>
        </div>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="text-sm">
            <span className="block text-xs text-muted">Когда везти — для календаря</span>
            <input
              type="date"
              value={plan.planned_date ?? ''}
              onChange={(e) => patch({ planned_date: e.target.value || null })}
              disabled={plan.status === 'cancelled'}
              className="mt-1 rounded-lg border border-line bg-bg px-3 py-1.5"
              data-testid="plan-date"
            />
          </label>
          <label className="min-w-[14rem] flex-1 text-sm">
            <span className="block text-xs text-muted">Заметка — например, машина или договорённость с логистом</span>
            <input
              value={note}
              maxLength={1000}
              onChange={(e) => setNote(e.target.value)}
              onBlur={() => note !== (plan.note ?? '') && patch({ note })}
              className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-1.5"
              data-testid="plan-note"
            />
          </label>
          <button type="button" onClick={exportXlsx} className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent" data-testid="plan-export">
            Excel
          </button>
          {plan.status === 'draft' && (
            <button type="button" onClick={() => patch({ status: 'cancelled' })} className="rounded-lg border border-line px-3 py-1.5 text-sm text-muted hover:border-bad hover:text-bad" data-testid="plan-cancel">
              Отменить план
            </button>
          )}
          {plan.status === 'cancelled' && (
            <button type="button" onClick={() => patch({ status: 'draft' })} className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent">
              Вернуть в работу
            </button>
          )}
        </div>
        {plan.order && (
          <p className="mt-3 text-sm" data-testid="plan-order">
            Заявка Ozon{' '}
            {plan.order.id ? (
              <button type="button" onClick={() => onOrder(plan.order!.id!)} className="font-medium text-accent hover:underline">
                №{plan.order.number ?? plan.order.ozon_order_id}
              </button>
            ) : (
              <span className="font-medium">№{plan.order.ozon_order_id}</span>
            )}{' '}
            <span className={`rounded px-1.5 py-0.5 text-xs ${GROUP_TONE[plan.order.group]}`}>{plan.order.state_text}</span>
            <button type="button" onClick={() => patch({ ozon_order_id: null })} className="ml-2 text-xs text-muted hover:text-accent">
              отвязать
            </button>
          </p>
        )}
      </div>
      <FormError message={error} />

      <div className="overflow-x-auto rounded-xl border border-line bg-card">
        <table className="w-full min-w-[34rem] text-sm" data-testid="plan-items">
          <thead className="text-left text-xs text-muted">
            <tr className="border-b border-line">
              <th className="px-4 py-2 font-normal">Товар</th>
              <th className="px-4 py-2 text-right font-normal" title="Сколько советовал расчёт, когда товар попал в план">
                Советовал
              </th>
              <th className="px-4 py-2 text-right font-normal">В плане</th>
              <th className="px-4 py-2 font-normal">Правки</th>
            </tr>
          </thead>
          <tbody>
            {plan.items.map((i) => (
              <tr key={i.product.id} className={`border-b border-line last:border-b-0 ${i.qty === 0 ? 'text-faint' : ''}`} data-testid="plan-item">
                <td className="px-4 py-2">
                  <Link href={productHref(i.product.id)} className="font-medium hover:text-accent">
                    {i.product.offer_id}
                  </Link>
                  {i.product.name && <div className="max-w-[16rem] truncate text-xs text-muted">{i.product.name}</div>}
                  {!i.has_sku && plan.kind === 'supply' && <div className="text-xs text-warn">нет SKU Ozon — в заявку не попадёт</div>}
                </td>
                <td className="px-4 py-2 text-right tabular-nums text-muted">{i.recommended ?? '—'}</td>
                <td className="px-4 py-2 text-right">
                  {plan.editable ? <QtyInput value={i.qty} onSave={(q) => setQty(i.product.id, q)} testid={`plan-qty-${i.product.offer_id}`} /> : <span className="tabular-nums">{i.qty}</span>}
                </td>
                <td className="px-4 py-2 text-xs text-faint">
                  {i.history.length > 1 ? i.history.map((h) => h.qty).join(' → ') : i.qty === 0 ? 'убран из плана' : '—'}
                  {i.history.length > 1 && <span className="block">последняя {fmtDateTime(i.updated_at)}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {plan.editable && <p className="text-xs text-faint">Каждая правка сохраняется отдельно — видно, сколько советовал расчёт и что вы поменяли. 0 — убрать товар из плана.</p>}

      {plan.kind === 'purchase' ? (
        <p className="rounded-xl border border-line bg-card px-4 py-3 text-sm text-muted">Закупка на свой склад — заявка в Ozon для неё не нужна. Excel — для поставщика.</p>
      ) : plan.status === 'draft' ? (
        <>
          {live.length > 0 && <OzonCreate plan={plan} onDone={load} />}
          <LinkOrder plan={plan} onLink={(orderId) => patch({ ozon_order_id: orderId })} />
        </>
      ) : null}
    </section>
  )
}

function QtyInput({ value, onSave, testid }: { value: number; onSave: (q: number) => void; testid: string }) {
  const [v, setV] = useState(String(value))
  useEffect(() => setV(String(value)), [value])
  const commit = () => {
    const n = Number(v)
    if (v === '' || !Number.isInteger(n) || n < 0 || n > 100000) return setV(String(value))
    if (n !== value) onSave(n)
  }
  return (
    <input
      inputMode="numeric"
      value={v}
      onChange={(e) => setV(e.target.value.replace(/\D/g, ''))}
      onBlur={commit}
      onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
      className="w-24 rounded-lg border border-line bg-bg px-2 py-1 text-right tabular-nums outline-none focus:border-accent"
      aria-label="Количество в плане"
      data-testid={testid}
    />
  )
}

/** Заявку оформили в кабинете Ozon — привязать к плану: увидим, что привезли против плана. */
function LinkOrder({ plan, onLink }: { plan: PlanDetail; onLink: (id: number) => void }) {
  const [orders, setOrders] = useState<SupplyOrders | null>(null)
  const [picked, setPicked] = useState('')
  useEffect(() => {
    api.get<SupplyOrders>('/supply/orders').then(({ data }) => setOrders(data)).catch(() => setOrders(null))
  }, [])
  const free = (orders?.items ?? []).filter((o) => !o.plan && o.group !== 'failed')
  if (free.length === 0) return null
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm" data-testid="link-order">
      <span className="text-muted">Оформили в кабинете Ozon? Привяжите заявку:</span>
      <select value={picked} onChange={(e) => setPicked(e.target.value)} className="max-w-full rounded-lg border border-line bg-bg px-3 py-1.5">
        <option value="">выберите заявку</option>
        {free.map((o) => (
          <option key={o.id} value={o.ozon_order_id}>
            №{o.number ?? o.ozon_order_id} · {o.cluster ?? o.storage_warehouse ?? 'склад'} · {o.state_text}
          </option>
        ))}
      </select>
      <button type="button" disabled={!picked} onClick={() => onLink(Number(picked))} className="rounded-lg border border-line px-3 py-1.5 hover:border-accent hover:text-accent disabled:opacity-60">
        Привязать
      </button>
    </div>
  )
}

function OzonCreate({ plan, onDone }: { plan: PlanDetail; onDone: () => void }) {
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [warehouse, setWarehouse] = useState<number | null>(null)
  const [slots, setSlots] = useState<Timeslots | null>(null)
  const [slot, setSlot] = useState<{ from: string; to: string } | null>(null)
  const [confirm, setConfirm] = useState(false)
  const [result, setResult] = useState<string | null>(null)

  async function check() {
    setBusy(true)
    setError(null)
    setDraft(null)
    setWarehouse(null)
    setSlots(null)
    setSlot(null)
    setConfirm(false)
    try {
      setDraft((await api.post<Draft>(`/supply/plans/${plan.id}/ozon-check`)).data)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  async function refresh() {
    if (!draft) return
    try {
      setDraft((await api.get<Draft>(`/supply/drafts/${draft.id}`)).data)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  async function pickWarehouse(id: number) {
    if (!draft) return
    setWarehouse(id)
    setSlots(null)
    setSlot(null)
    setError(null)
    try {
      setSlots((await api.get<Timeslots>(`/supply/drafts/${draft.id}/timeslots`, { params: { warehouse_id: id } })).data)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  async function create() {
    if (!draft || !warehouse || !slot) return
    setBusy(true)
    setError(null)
    try {
      const { data } = await api.post<{ ozon_order_id: number; message: string }>(`/supply/drafts/${draft.id}/create`, {
        warehouse_id: warehouse,
        timeslot_from: slot.from,
        timeslot_to: slot.to,
        confirm,
      })
      setResult(`${data.message} Номер заявки: ${data.ozon_order_id}.`)
      onDone()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const time = (s: string) => s.slice(11, 16)
  return (
    <section className="space-y-3 rounded-xl border border-line bg-card p-4" data-testid="ozon-create">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-[16rem] flex-1">
          <h3 className="font-semibold">Заявка на поставку в Ozon</h3>
          <p className="text-sm text-muted">
            Сначала проверим план в Ozon: черновик в кабинете не виден и ни к чему не обязывает. Потом выберите склад и таймслот —
            заявка создаётся только после вашего подтверждения.
          </p>
        </div>
        <button type="button" onClick={check} disabled={busy} className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="ozon-check">
          {draft ? 'Проверить заново' : 'Проверить в Ozon'}
        </button>
      </div>
      <FormError message={error} />
      {result && (
        <p className="rounded-lg bg-ok/10 px-3 py-2 text-sm text-ok" data-testid="ozon-created">
          {result}
        </p>
      )}
      {draft && !result && (
        <div className="space-y-3" data-testid="ozon-draft">
          {draft.status === 'pending' && (
            <p className="text-sm text-muted">
              Ozon ещё проверяет план…{' '}
              <button type="button" onClick={refresh} className="text-accent hover:underline">
                Обновить
              </button>
            </p>
          )}
          {draft.status === 'failed' && <p className="text-sm text-bad">Ozon не принял план: {draft.errors.join('; ') || 'причина не указана'}</p>}
          {draft.rejected.length > 0 && (
            <div className="rounded-lg border border-warn/40 bg-warn/5 px-3 py-2 text-sm" data-testid="ozon-rejected">
              <div className="font-medium text-warn">Ozon не примет: {draft.rejected.length}</div>
              <ul className="mt-1 text-xs text-muted">
                {draft.rejected.map((r) => (
                  <li key={r.sku}>
                    {r.offer_id ?? r.sku} — {r.reasons.join(', ') || 'причина не указана'}
                  </li>
                ))}
              </ul>
              <div className="mt-1 text-xs text-faint">Остальные товары войдут в заявку; эти лучше убрать из плана или везти отдельно.</div>
            </div>
          )}
          {draft.status === 'ready' && (
            <>
              <div>
                <div className="text-sm">
                  Кластер в Ozon: <span className="font-medium">{draft.cluster_name ?? plan.destination_name}</span>
                  <span className="text-xs text-faint"> · проверка действует до {fmtDateTime(draft.expires_at)}</span>
                </div>
                <div className="mt-2 grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Склад Ozon">
                  {draft.warehouses.map((w) => (
                    <button
                      key={w.id}
                      type="button"
                      role="radio"
                      aria-checked={warehouse === w.id}
                      disabled={!w.available}
                      onClick={() => pickWarehouse(w.id)}
                      className={`rounded-lg border px-3 py-2 text-left text-sm disabled:opacity-50 ${warehouse === w.id ? 'border-accent bg-accent/10' : 'border-line hover:border-muted'}`}
                      data-testid="ozon-warehouse"
                    >
                      <span className="block font-medium">{w.name}</span>
                      <span className="block text-xs text-faint">{w.available ? w.address ?? 'принимает поставки' : `не принимает${w.reason ? `: ${w.reason}` : ''}`}</span>
                    </button>
                  ))}
                </div>
              </div>
              {warehouse && slots && (
                <div data-testid="ozon-slots">
                  <div className="text-sm text-muted">Таймслот{slots.timezone ? ` (время склада, ${slots.timezone})` : ''}</div>
                  {slots.days.length === 0 ? (
                    <p className="text-sm text-muted">На ближайшие 4 недели свободных таймслотов нет — выберите другой склад.</p>
                  ) : (
                    <div className="mt-1 space-y-1.5">
                      {slots.days.map((d) => (
                        <div key={d.date} className="flex flex-wrap items-center gap-1.5">
                          <span className="w-20 text-xs text-muted">{fmtDay(d.date)}</span>
                          {d.slots.map((s) => (
                            <button
                              key={s.from}
                              type="button"
                              aria-pressed={slot?.from === s.from}
                              onClick={() => setSlot(s)}
                              className={`rounded-full border px-2.5 py-0.5 text-xs ${slot?.from === s.from ? 'border-accent bg-accent/10 text-accent' : 'border-line hover:border-muted'}`}
                              data-testid="ozon-slot"
                            >
                              {time(s.from)}–{time(s.to)}
                            </button>
                          ))}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {warehouse && slot && (
                <div className="space-y-2 rounded-lg border border-line p-3">
                  <label className="flex items-start gap-2 text-sm">
                    <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} className="mt-0.5" data-testid="ozon-confirm" />
                    <span>
                      Понимаю: заявка на {pieces(plan.units)} появится в кабинете Ozon, привезти её нужно {fmtDay(slot.from.slice(0, 10))} с {time(slot.from)}{' '}
                      до {time(slot.to)}; грузоместа и пропуск водителя оформлю в кабинете.
                    </span>
                  </label>
                  <button type="button" onClick={create} disabled={!confirm || busy} className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="ozon-submit">
                    Создать заявку в Ozon
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      )}
      <p className="text-xs text-faint">
        Или оформите вручную: скачайте Excel плана и создайте поставку в{' '}
        <a href={OZON_CABINET} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
          кабинете Ozon ↗
        </a>{' '}
        (раздел «FBO → Поставки»), а потом привяжите заявку к плану.
      </p>
    </section>
  )
}

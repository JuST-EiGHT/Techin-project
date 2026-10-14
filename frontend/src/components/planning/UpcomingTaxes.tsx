'use client'
// «Ближайшие сроки» на странице «Налоги и НДС» (шаг 3.9): 2–3 следующих налоговых платежа — что, до какой даты,
// примерная сумма с расчётом, отметка «оплачено». Профиль не задан — блока нет; «Все платежи →» — только если
// включено «Планирование» (там календарь).
import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { type CalendarLine, calcHref, parseAmount } from '@/components/planning/types'
import { fmtDay, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'

type Upcoming = { has_profile: boolean; planning: boolean; items: CalendarLine[] }

function Item({ ln, onDone }: { ln: CalendarLine; onDone: () => void }) {
  const [amount, setAmount] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const unknown = ln.amount === null

  async function paid() {
    const value = unknown ? parseAmount(amount) : null
    if (unknown && value === null) return setError('Сколько заплатили — числом')
    setBusy(true)
    setError(null)
    try {
      await api.post('/planning/calendar/paid', { key: ln.key, ...(value ? { amount: value } : {}) })
      onDone()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 px-4 py-3 text-sm" data-testid="upcoming-item">
      <div className="min-w-0">
        <div className="font-medium">
          {ln.title}
          {ln.period_label && <span className="font-normal text-muted"> · {ln.period_label}</span>}
        </div>
        <div className="text-xs text-muted">
          до <b className="text-fg">{fmtDay(ln.day)}</b>
          {ln.day !== ln.planned_day && ` (по плану ${fmtDay(ln.planned_day)})`}
          {ln.notice && ` · ${(ln.notice.doc ?? 'уведомление').toLowerCase()} до ${fmtDay(ln.notice.day)}`}
        </div>
        {ln.note && <div className="text-xs text-faint">{ln.note}</div>}
        {ln.calc && (
          <Link href={calcHref(ln.calc)} className="text-xs text-accent hover:underline" data-testid="upcoming-calc">
            Расчёт →
          </Link>
        )}
        <FormError message={error} />
      </div>
      <div className="flex flex-col items-end gap-1.5">
        <span className="whitespace-nowrap font-semibold tabular-nums" data-testid="upcoming-amount">
          {unknown ? <span className="text-xs font-normal text-faint">сумма неизвестна</span> : `${ln.estimate ? '≈ ' : ''}${rub(String(Math.abs(Number(ln.amount))))}`}
        </span>
        {ln.status === 'paid' ? (
          <span className="text-xs text-ok" data-testid="upcoming-paid">
            ✓ оплачено {ln.fact ? fmtDay(ln.fact.day) : ''}
          </span>
        ) : (
          <span className="flex items-center gap-1.5">
            {unknown && (
              <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" placeholder="сумма" aria-label="Сколько заплатили" className="w-24 rounded-md border border-line bg-bg px-2 py-1 text-xs outline-none focus:border-accent" />
            )}
            <button type="button" disabled={busy} onClick={paid} className="rounded-md border border-line px-2.5 py-1 text-xs hover:border-accent hover:text-accent disabled:opacity-50" data-testid="upcoming-mark">
              Оплачено
            </button>
          </span>
        )}
      </div>
    </li>
  )
}

export function UpcomingTaxes({ refresh }: { refresh: number }) {
  const [data, setData] = useState<Upcoming | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(() => {
    api
      .get<Upcoming>('/tax-profile/upcoming')
      .then((r) => {
        setData(r.data)
        setError(null)
      })
      .catch((err) => setError(errorMessage(err)))
  }, [])
  useEffect(() => {
    load()
  }, [load, refresh])

  if (error) return <FormError message={error} />
  if (!data?.has_profile) return null
  return (
    <section className="overflow-hidden rounded-xl border border-line bg-card" data-testid="upcoming-taxes">
      <div className="flex flex-wrap items-baseline gap-x-3 border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold">Ближайшие сроки</h2>
        <span className="text-xs text-faint">по вашему профилю; срок в выходной — на следующий рабочий день, сумма — оценка по продажам</span>
        {data.planning && (
          <Link href="/dashboard/planning?tab=calendar" className="ml-auto text-xs text-accent hover:underline" data-testid="upcoming-all">
            Все платежи →
          </Link>
        )}
      </div>
      {data.items.length === 0 ? (
        <p className="px-4 py-3 text-sm text-muted">Сроков уплаты для этого режима в справочнике нет — добавьте платёж в календаре вручную.</p>
      ) : (
        <ul className="divide-y divide-line">
          {data.items.map((ln) => (
            <Item key={ln.key} ln={ln} onDone={load} />
          ))}
        </ul>
      )}
    </section>
  )
}

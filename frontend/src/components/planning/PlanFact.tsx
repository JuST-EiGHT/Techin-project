'use client'
// «Правила разнесения → План-факт» (шаг 10.20): плановые платежи календаря и операции, которые их закрыли (по сумме и
// дате, отмечено вручную, ЕНП по сроку), отклонения; у неоплаченных — «похоже, это оплата …» с причинами: «Да» отмечает
// платёж оплаченным этой операцией (и ставит статью, если её не было), «Нет» — больше не предлагать.
import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import type { PlanFactOp, PlanFactPage, PlanFactRow } from '@/components/planning/types'
import { Badge, fmtDay, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { addDays, todayMsk } from '@/lib/period'

type Preset = 'around' | 'month' | 'last' | 'quarter'
const PRESETS: { value: Preset; label: string }[] = [
  { value: 'around', label: '30 дней назад и 14 вперёд' },
  { value: 'month', label: 'Этот месяц' },
  { value: 'last', label: 'Прошлый месяц' },
  { value: 'quarter', label: '90 дней' },
]

function range(p: Preset, today: string): { from: string; to: string } {
  const [y, m] = today.split('-').map(Number)
  const first = (yy: number, mm: number) => `${yy}-${String(mm).padStart(2, '0')}-01`
  if (p === 'month') return { from: first(y, m), to: addDays(m === 12 ? first(y + 1, 1) : first(y, m + 1), -1) }
  if (p === 'last') {
    const [py, pm] = m === 1 ? [y - 1, 12] : [y, m - 1]
    return { from: first(py, pm), to: addDays(first(y, m), -1) }
  }
  if (p === 'quarter') return { from: addDays(today, -89), to: today }
  return { from: addDays(today, -30), to: addDays(today, 14) }
}

const HOW: Record<NonNullable<PlanFactRow['how']>, string> = { auto: 'по сумме и дате', marked: 'отмечено вручную', tax: 'ЕНП по сроку' }
const signed = (v: string | null) => (v === null ? 'сумма уточняется' : rub(v))

function OpLine({ op }: { op: PlanFactOp }) {
  return (
    <span>
      {fmtDay(op.day)} · <span className="tabular-nums">{rub(op.amount)}</span>
      {op.counterparty && ` · ${op.counterparty}`}
      {op.account && <span className="text-muted"> · {op.account}</span>}
      {op.category && <span className="text-muted"> · {op.category}</span>}
    </span>
  )
}

function Suggestion({ row, onDone }: { row: PlanFactRow; onDone: (text: string) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const sg = row.suggestion!

  async function act(path: 'accept' | 'dismiss') {
    setBusy(true)
    setError(null)
    try {
      const body = { key: row.key, op_id: sg.op.op_id }
      if (path === 'accept') {
        const { data } = await api.post<{ title: string; category_set: string | null }>('/planning/plan-fact/accept', body)
        onDone(`«${data.title}» отмечен оплаченным.${data.category_set ? ` Операции поставлена статья «${data.category_set}».` : ''}`)
      } else {
        await api.post('/planning/plan-fact/dismiss', body)
        onDone('Хорошо, эту операцию больше не предлагаем для этого платежа.')
      }
    } catch (err) {
      setError(errorMessage(err))
      setBusy(false)
    }
  }

  return (
    <li className="rounded-xl border border-accent/40 bg-accent/5 p-3 text-sm" data-testid="pf-suggestion">
      <p>
        <b>«{row.title}»</b> {fmtDay(row.day)} на <span className="tabular-nums">{signed(row.amount)}</span> — похоже, оплачен операцией:
      </p>
      <p className="mt-1">
        <OpLine op={sg.op} />
      </p>
      <ul className="mt-1 flex flex-wrap gap-1 text-xs">
        {sg.reasons.map((r) => (
          <li key={r}>
            <Badge tone="muted">{r}</Badge>
          </li>
        ))}
      </ul>
      <FormError message={error} />
      <div className="mt-2 flex flex-wrap gap-2">
        <button type="button" disabled={busy} onClick={() => act('accept')} className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="pf-accept">
          Да, это оплата{sg.category_fix ? ` — статья «${sg.category_fix}»` : ''}
        </button>
        <button type="button" disabled={busy} onClick={() => act('dismiss')} className="rounded-lg border border-line px-3 py-1.5 text-xs text-muted hover:text-fg" data-testid="pf-dismiss">
          Нет, это другое
        </button>
      </div>
    </li>
  )
}

export function PlanFact() {
  const today = todayMsk()
  const [preset, setPreset] = useState<Preset>('around')
  const [page, setPage] = useState<PlanFactPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    const r = range(preset, today)
    try {
      const { data } = await api.get<PlanFactPage>('/planning/plan-fact', { params: { date_from: r.from, date_to: r.to } })
      setPage(data)
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [preset, today])

  useEffect(() => {
    load()
  }, [load])

  const suggestions = page?.rows.filter((r) => r.suggestion) ?? []
  return (
    <section className="space-y-4" data-testid="plan-fact">
      <p className="text-sm text-muted">
        Плановые платежи из{' '}
        <Link href="/dashboard/planning/calendar" className="text-accent hover:underline">
          «Календаря»
        </Link>{' '}
        и что их оплатило. Операция закрывает платёж сама, если сумма отличается не больше чем на 10 %, дата — не больше чем на 3 дня, а статья и контрагент
        совпадают. Похожие, но не такие точные — ниже предложениями: подтвердите или отклоните.
      </p>
      <div className="flex flex-wrap gap-1 rounded-lg border border-line bg-card p-1 sm:w-fit" role="group" aria-label="Период">
        {PRESETS.map((p) => (
          <button
            key={p.value}
            type="button"
            aria-pressed={preset === p.value}
            onClick={() => setPreset(p.value)}
            className={`rounded-md px-3 py-1 text-sm ${preset === p.value ? 'bg-accent text-white' : 'text-muted hover:text-fg'}`}
          >
            {p.label}
          </button>
        ))}
      </div>
      <FormError message={error} />
      {notice && <p className="text-sm text-ok" data-testid="pf-notice">{notice}</p>}
      {page && (
        <>
          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line text-sm sm:grid-cols-5" data-testid="pf-summary">
            {[
              ['Оплачено', page.counts.paid, 'text-ok'],
              ['Частично', page.counts.partial, page.counts.partial ? 'text-warn' : ''],
              ['Просрочено', page.counts.overdue, page.counts.overdue ? 'text-bad' : ''],
              ['Ожидается', page.counts.expected + page.counts.planned, ''],
              ['Похоже на оплату', page.counts.suggestions, page.counts.suggestions ? 'text-accent' : ''],
            ].map(([label, n, tone]) => (
              <div key={label as string} className="bg-card px-4 py-2">
                <div className="text-[11px] uppercase tracking-wider text-muted">{label}</div>
                <div className={`text-lg font-semibold tabular-nums ${tone}`}>{n}</div>
              </div>
            ))}
          </div>
          {suggestions.length > 0 && (
            <ul className="space-y-2" data-testid="pf-suggestions">
              {suggestions.map((row) => (
                <Suggestion
                  key={row.key}
                  row={row}
                  onDone={(text) => {
                    setNotice(text)
                    load()
                  }}
                />
              ))}
            </ul>
          )}
          {page.rows.length === 0 ? (
            <p className="rounded-xl border border-line bg-card p-4 text-sm text-muted">
              За период плановых платежей нет. Добавьте их в «Календаре» — аренду, зарплату, оплату поставщику.
            </p>
          ) : (
            <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-card" data-testid="pf-rows">
              {page.rows.map((row) => (
                <li key={row.key} className="grid gap-x-4 gap-y-1 px-4 py-2.5 text-sm sm:grid-cols-[minmax(0,1.1fr)_minmax(0,1.3fr)_auto]" data-testid="pf-row">
                  <span className="min-w-0">
                    <span className="font-medium">{row.title}</span>
                    <span className="block text-xs text-muted">
                      план {fmtDay(row.day)} · <span className="tabular-nums">{signed(row.amount)}</span>
                      {row.estimate && ' (оценка)'}
                      {row.category && ` · ${row.category}`}
                    </span>
                  </span>
                  <span className="min-w-0 text-xs">
                    {row.fact ? (
                      <>
                        <span className="text-muted">факт </span>
                        <OpLine op={row.fact} />
                        {row.diff && Number(row.diff) !== 0 && (
                          <span className="block text-warn" data-testid="pf-diff">
                            отклонение {Number(row.diff) > 0 ? '+' : ''}
                            {rub(row.diff)}
                          </span>
                        )}
                        {row.days_late ? (
                          <span className="block text-muted">
                            {row.days_late > 0 ? 'позже' : 'раньше'} срока на {Math.abs(row.days_late)} дн.
                          </span>
                        ) : null}
                      </>
                    ) : (
                      <span className="text-muted">{row.suggestion ? 'есть похожая операция — выше' : 'оплаты не нашли'}</span>
                    )}
                  </span>
                  <span className="sm:text-right" data-testid="pf-status">
                    {row.status === 'paid' && <Badge tone="ok">оплачен · {HOW[row.how ?? 'auto']}</Badge>}
                    {row.status === 'partial' && <Badge tone="warn">оплачен частично · {HOW[row.how ?? 'auto']}</Badge>}
                    {row.status === 'overdue' && <Badge tone="bad">просрочен</Badge>}
                    {row.status === 'expected' && <Badge tone="warn">ожидается</Badge>}
                    {row.status === 'planned' && <Badge tone="muted">запланирован</Badge>}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  )
}

'use client'
// «Операции → Счета и выписки» (шаг 10.20): сверка расчётного остатка с выпиской (расхождение и с какого дня),
// история поступлений, списаний и переводов по счёту с остатком после каждой операции, нераспознанные операции —
// операции из банка и из файла без статьи: поставить статью или сделать правило.
import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { CategoryOptions } from '@/components/planning/Categories'
import type { AccountHistory, Category, Reconcile, UnrecognizedPage } from '@/components/planning/types'
import { input } from '@/components/planning/ui'
import { Badge, fmtDay, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { addDays, todayMsk } from '@/lib/period'

const SOURCE: Record<string, string> = { manual: 'вручную', import: 'из файла', ozon_payout: 'выплата Ozon', bank: 'банк' }
const signed = (v: string) => `${Number(v) > 0 ? '+' : ''}${rub(v)}`

// ---------- сверка ----------

function ReconcileCard({ r }: { r: Reconcile }) {
  const tone = r.status === 'ok' ? 'ok' : r.status === 'diff' ? 'bad' : 'muted'
  const label = { ok: 'сходится', diff: 'расхождение', no_opening: 'нет остатка', no_overlap: 'не с чем сверить', no_bank: 'нет данных банка' }[r.status]
  const dayLink = r.since ? `/dashboard/operations?from=${r.matched_until ? addDays(r.matched_until, 1) : r.since}&to=${r.since}&account=${r.account.id}` : null
  return (
    <li className="rounded-xl border border-line bg-card p-4 text-sm" data-testid="reconcile-card">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{r.account.name}</span>
        {r.account.number && <span className="text-xs text-faint">···{r.account.number.slice(-4)}</span>}
        <Badge tone={tone}>{label}</Badge>
      </div>
      <p className={`mt-1 ${r.status === 'diff' ? '' : 'text-muted'}`} data-testid="reconcile-hint">
        {r.hint}
      </p>
      {r.bank !== null && r.service !== null && (
        <div className="mt-2 grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-line bg-line text-xs">
          <div className="bg-card px-3 py-1.5">
            <div className="text-muted">банк, {fmtDay(r.day).slice(0, 5)}</div>
            <div className="whitespace-nowrap font-semibold tabular-nums">{rub(r.bank)}</div>
          </div>
          <div className="bg-card px-3 py-1.5">
            <div className="text-muted">по сервису</div>
            <div className="whitespace-nowrap font-semibold tabular-nums">{rub(r.service)}</div>
          </div>
          <div className="bg-card px-3 py-1.5">
            <div className="text-muted">разница</div>
            <div className={`whitespace-nowrap font-semibold tabular-nums ${r.diff && Number(r.diff) !== 0 ? 'text-bad' : 'text-ok'}`}>{r.diff ? signed(r.diff) : '—'}</div>
          </div>
        </div>
      )}
      {r.suspects.length > 0 && (
        <div className="mt-2">
          <p className="text-xs text-muted">Операции сервиса в эти дни, которых нет в выписке, — частая причина: дубль ручной операции или выплата в другой день.</p>
          <ul className="mt-1 divide-y divide-line text-xs" data-testid="reconcile-suspects">
            {r.suspects.map((s) => (
              <li key={s.op_id} className={`flex flex-wrap gap-x-2 py-1 ${s.likely ? 'text-warn' : ''}`}>
                <span className="tabular-nums">{fmtDay(s.day)}</span>
                <span className="font-medium tabular-nums">{signed(s.amount)}</span>
                <span className="text-muted">{SOURCE[s.source] ?? s.source}</span>
                <span className="min-w-0 truncate">{[s.category, s.counterparty, s.comment].filter(Boolean).join(' · ')}</span>
                {s.likely && <span>— сумма совпадает с расхождением</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {dayLink && (
        <Link href={dayLink} className="mt-2 inline-block text-xs text-accent hover:underline" data-testid="reconcile-ops">
          Операции этих дней по счёту →
        </Link>
      )}
      {r.days.length > 1 && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer text-muted hover:text-fg">По дням</summary>
          <table className="mt-1 w-full tabular-nums">
            <thead className="text-muted">
              <tr>
                <th className="py-1 text-left font-normal">день</th>
                <th className="py-1 text-right font-normal">банк</th>
                <th className="py-1 text-right font-normal">сервис</th>
                <th className="py-1 text-right font-normal">разница</th>
              </tr>
            </thead>
            <tbody>
              {r.days.map((d) => (
                <tr key={d.day} className="border-t border-line">
                  <td className="py-1">
                    {fmtDay(d.day).slice(0, 5)}
                    {d.point && <span className="text-faint"> · выписка</span>}
                  </td>
                  <td className="py-1 text-right">{rub(d.bank)}</td>
                  <td className="py-1 text-right">{rub(d.service)}</td>
                  <td className={`py-1 text-right ${Number(d.diff) !== 0 ? 'text-bad' : 'text-faint'}`}>{Number(d.diff) === 0 ? '—' : signed(d.diff)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </li>
  )
}

export function ReconcileSection({ version }: { version: number }) {
  const [rows, setRows] = useState<Reconcile[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api
      .get<Reconcile[]>('/planning/accounts/reconcile')
      .then((r) => setRows(r.data))
      .catch((err) => setError(errorMessage(err)))
  }, [version])
  if (error) return <FormError message={error} />
  if (!rows) return null
  const checked = rows.filter((r) => r.status !== 'no_bank')
  const without = rows.filter((r) => r.status === 'no_bank')
  return (
    <section className="space-y-2" data-testid="reconcile">
      <h2 className="text-sm font-semibold">Сверка с банком</h2>
      <p className="text-xs text-muted">
        Остаток по операциям сервиса против остатка в выписке на тот же день. Разошлось — покажем, с какого дня и какие операции в этот день есть только в
        сервисе.
      </p>
      {checked.length > 0 ? (
        <ul className="grid gap-2 lg:grid-cols-2">
          {checked.map((r) => (
            <ReconcileCard key={r.account.id} r={r} />
          ))}
        </ul>
      ) : (
        <p className="rounded-xl border border-line bg-card p-4 text-sm text-muted">Сверять пока не с чем: загрузите выписку или подключите банк — ниже.</p>
      )}
      {without.length > 0 && checked.length > 0 && (
        <p className="text-xs text-faint">Без данных банка: {without.map((r) => r.account.name).join(', ')}.</p>
      )}
    </section>
  )
}

// ---------- история по счёту ----------

type Period = '30' | 'month' | '90'
const PERIODS: { value: Period; label: string }[] = [
  { value: '30', label: '30 дней' },
  { value: 'month', label: 'Этот месяц' },
  { value: '90', label: '90 дней' },
]
const KINDS = [
  { value: '', label: 'Всё' },
  { value: 'income', label: 'Поступления' },
  { value: 'expense', label: 'Списания' },
  { value: 'transfer', label: 'Переводы' },
]

export function AccountHistoryPanel({ accountId }: { accountId: string }) {
  const today = todayMsk()
  const [period, setPeriod] = useState<Period>('30')
  const [kind, setKind] = useState('')
  const [data, setData] = useState<AccountHistory | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    const from = period === '30' ? addDays(today, -29) : period === '90' ? addDays(today, -89) : `${today.slice(0, 8)}01`
    try {
      const { data: body } = await api.get<AccountHistory>(`/planning/accounts/${accountId}/history`, {
        params: { date_from: from, date_to: today, ...(kind ? { type: kind } : {}) },
      })
      setData(body)
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [accountId, period, kind, today])

  useEffect(() => {
    load()
  }, [load])

  const pill = (active: boolean) => `rounded-md px-2.5 py-1 text-xs ${active ? 'bg-accent text-white' : 'text-muted hover:text-fg'}`
  return (
    <div className="mt-3 space-y-2 border-t border-line pt-3" data-testid="account-history">
      <div className="flex flex-wrap gap-2">
        <div className="flex gap-1 rounded-lg border border-line bg-bg p-1" role="group" aria-label="Период">
          {PERIODS.map((p) => (
            <button key={p.value} type="button" aria-pressed={period === p.value} onClick={() => setPeriod(p.value)} className={pill(period === p.value)}>
              {p.label}
            </button>
          ))}
        </div>
        <div className="flex gap-1 rounded-lg border border-line bg-bg p-1" role="group" aria-label="Что показать">
          {KINDS.map((k) => (
            <button key={k.value} type="button" aria-pressed={kind === k.value} onClick={() => setKind(k.value)} className={pill(kind === k.value)} data-testid={`history-kind-${k.value || 'all'}`}>
              {k.label}
            </button>
          ))}
        </div>
      </div>
      <FormError message={error} />
      {data && (
        <>
          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line text-xs sm:grid-cols-5" data-testid="history-totals">
            {[
              ['На начало', data.opening === null ? '—' : rub(data.opening), ''],
              ['Пришло', rub(data.totals.income), 'text-ok'],
              ['Ушло', rub(data.totals.expense), 'text-bad'],
              ['Переводы', `${rub(data.totals.transfer_in)} / ${rub(data.totals.transfer_out)}`, 'text-muted'],
              ['На конец', data.closing === null ? '—' : rub(data.closing), ''],
            ].map(([label, value, tone]) => (
              <div key={label} className="bg-card px-3 py-1.5">
                <div className="text-muted">{label}</div>
                <div className={`whitespace-nowrap font-semibold tabular-nums ${tone}`}>{value}</div>
              </div>
            ))}
          </div>
          {data.opening === null && data.opening_as_of && (
            <p className="text-xs text-faint">Остаток известен с {fmtDay(data.opening_as_of)} — у операций раньше его нет.</p>
          )}
          {data.items.length === 0 ? (
            <p className="text-sm text-muted">За период движений нет.</p>
          ) : (
            <ul className="divide-y divide-line text-sm">
              {data.items.map((i) => (
                <li key={i.op_id} className={`grid grid-cols-[3rem_minmax(0,1fr)_auto] gap-x-3 py-1.5 ${i.before_opening ? 'opacity-60' : ''}`} data-testid="history-row">
                  <span className="tabular-nums text-muted">{fmtDay(i.day).slice(0, 5)}</span>
                  <span className="min-w-0">
                    <span className="block truncate">
                      {i.type === 'transfer' ? `Перевод ${Number(i.amount) < 0 ? 'на' : 'с'} «${i.transfer_account ?? '—'}»` : (i.category ?? 'Без статьи')}
                    </span>
                    <span className="block truncate text-xs text-muted">{[i.counterparty, i.comment, SOURCE[i.source]].filter(Boolean).join(' · ')}</span>
                  </span>
                  <span className="text-right">
                    <span className={`block whitespace-nowrap font-semibold tabular-nums ${i.type === 'transfer' ? 'text-muted' : Number(i.amount) > 0 ? 'text-ok' : 'text-bad'}`}>
                      {signed(i.amount)}
                    </span>
                    <span className="block whitespace-nowrap text-xs tabular-nums text-faint" data-testid="history-balance">
                      {i.balance === null ? 'до остатка' : `остаток ${rub(i.balance)}`}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          )}
          <Link href={`/dashboard/operations?account=${accountId}`} className="inline-block text-xs text-accent hover:underline">
            Все операции счёта в журнале →
          </Link>
        </>
      )}
    </div>
  )
}

// ---------- нераспознанные операции ----------

export function UnrecognizedSection({ categories, version, onChanged }: { categories: Category[]; version: number; onChanged: () => void }) {
  const [page, setPage] = useState<UnrecognizedPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setPage((await api.get<UnrecognizedPage>('/planning/unrecognized', { params: { limit: 50 } })).data)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    load()
  }, [load, version])

  async function categorize(opId: string, categoryId: string, name: string) {
    if (!categoryId) return
    setError(null)
    try {
      await api.post('/planning/operations/bulk', { op_ids: [opId], category_id: categoryId })
      setNotice(`Статья «${name}» поставлена. Чтобы такие операции разносились сами — «Правило →».`)
      load()
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  if (!page) return error ? <FormError message={error} /> : null
  return (
    <section className="space-y-2" data-testid="unrecognized">
      <div className="flex flex-wrap items-baseline gap-2">
        <h2 className="text-sm font-semibold">Нераспознанные операции</h2>
        <Badge tone={page.total ? 'warn' : 'muted'}>{page.total}</Badge>
        {page.total > 0 && (
          <Link href="/dashboard/operations?category=none" className="ml-auto text-xs text-accent hover:underline">
            Разметить списком в журнале →
          </Link>
        )}
      </div>
      <p className="text-xs text-muted">Операции из банка и из файла, которые правила не разнесли. Поставьте статью — или сделайте правило, и такие будут разноситься сами.</p>
      <FormError message={error} />
      {notice && <p className="text-sm text-ok" data-testid="unrecognized-notice">{notice}</p>}
      {page.total === 0 ? (
        <p className="rounded-xl border border-line bg-card p-4 text-sm text-muted">Все операции из банка разнесены по статьям.</p>
      ) : (
        <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-card">
          {page.items.map((i) => {
            const q = new URLSearchParams({ new: '1', direction: i.rule.direction })
            if (i.rule.counterparty_inn) q.set('inn', i.rule.counterparty_inn)
            if (i.rule.counterparty) q.set('counterparty', i.rule.counterparty)
            return (
              <li key={i.op_id} className="grid gap-x-3 gap-y-1.5 px-4 py-2.5 text-sm sm:grid-cols-[minmax(0,1fr)_12rem]" data-testid="unrecognized-row">
                <span className="min-w-0">
                  <span className="flex flex-wrap items-baseline gap-x-2">
                    <span className="tabular-nums text-muted">{fmtDay(i.day)}</span>
                    <span className={`font-semibold tabular-nums ${i.type === 'income' ? 'text-ok' : 'text-bad'}`}>{signed(i.amount)}</span>
                    <span className="min-w-0 truncate">{i.counterparty ?? '—'}</span>
                    {i.inn && <span className="text-xs text-faint">ИНН {i.inn}</span>}
                  </span>
                  {i.purpose && <span className="block truncate text-xs text-muted">{i.purpose}</span>}
                  <span className="block text-xs text-faint">{i.account.name}</span>
                </span>
                <span className="flex items-center gap-2">
                  <select
                    defaultValue=""
                    onChange={(e) => categorize(i.op_id, e.target.value, e.target.selectedOptions[0]?.textContent?.replace(' · ', '') ?? '')}
                    aria-label="Статья"
                    className={`${input} min-w-0 flex-1 py-1.5 text-xs`}
                    data-testid="unrecognized-category"
                  >
                    <option value="">Статья…</option>
                    <CategoryOptions categories={categories} kind={i.type} />
                  </select>
                  <Link href={`/dashboard/operations/rules?${q.toString()}`} className="whitespace-nowrap text-xs text-accent hover:underline" data-testid="unrecognized-rule">
                    Правило →
                  </Link>
                </span>
              </li>
            )
          })}
        </ul>
      )}
      {page.total > page.items.length && <p className="text-xs text-faint">Показаны последние {page.items.length} из {page.total}.</p>}
    </section>
  )
}

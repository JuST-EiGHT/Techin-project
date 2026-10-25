'use client'
// «Финансы → Обзор» (шаг 10.22, docs/SITEMAP.md п. 6.1): выручка, расходы, прибыль и рентабельность к прошлому периоду —
// по P&L с прочими расходами бизнеса; куда ушла выручка (удержания Ozon, себестоимость, статьи расходов бизнеса, налог);
// динамика по продажам на Ozon; деньги на Ozon и на счетах, ближайшие выплаты и риски (как на главной); финансовые
// проблемы из «Требует внимания» (10.5). Раньше адрес открывал P&L — старые ссылки P&L ведут в P&L (lib/redirects.ts).
import Link from 'next/link'
import { Suspense, useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { AttentionWidget } from '@/components/alerts'
import { AnalyticsFilters, MetricsState, useAnalyticsFilter, useMetrics } from '@/components/analytics'
import { Change, Hint, type OzonDashboard } from '@/components/dashboard'
import { Card, DynamicsChart, MoneyBlock } from '@/components/home'
import { opsHref } from '@/components/planning/types'
import { fmtDay, pctText, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { daysWord } from '@/lib/period'

type Figure = { value: string | null; previous: string | null; change_pct?: string | null; change_pp?: string | null }
type Item = { key: string; title: string; kind: 'ozon' | 'cost' | 'business' | 'tax'; amount: string; share: string | null; category_id: string | null }
type FinanceOverview = {
  date_from: string
  date_to: string
  previous: { date_from: string; date_to: string; has_data: boolean }
  data_source: 'xlsx' | 'api' | 'mixed' | 'none'
  coverage: { days_total: number; missing_days: number }
  has_data: boolean
  profit_kind: 'net' | 'before_tax' | null
  tax_set: boolean
  figures: Record<'revenue' | 'expenses' | 'profit' | 'margin', Figure>
  profit_ozon: string | null
  business: { total: string; operations: number; uncategorized: { count: number; amount: string } } | null
  structure: Item[]
  missing_cost: number
  warnings: string[]
}

const KIND: Record<Item['kind'], { label: string; bar: string }> = {
  ozon: { label: 'Ozon', bar: 'bg-accent' },
  cost: { label: 'себестоимость', bar: 'bg-faint' },
  business: { label: 'расходы бизнеса', bar: 'bg-warn' },
  tax: { label: 'налог', bar: 'bg-bad' },
}
const short = (iso: string) => fmtDay(iso).slice(0, 5)

export default function FinanceOverviewPage() {
  return (
    <Suspense>
      <Overview />
    </Suspense>
  )
}

function useOverview(from: string, to: string) {
  const [state, setState] = useState<{ data: FinanceOverview | null; board: OzonDashboard | null; error: string | null }>({
    data: null,
    board: null,
    error: null,
  })
  useEffect(() => {
    let alive = true
    setState((s) => ({ ...s, error: null }))
    Promise.all([
      api.get<FinanceOverview>('/finance/overview', { params: { date_from: from, date_to: to } }),
      api.get<OzonDashboard>('/dashboard/ozon', { params: { date_from: from, date_to: to } }),
    ])
      .then(([o, b]) => alive && setState({ data: o.data, board: b.data, error: null }))
      .catch((err) => alive && setState((s) => ({ ...s, error: errorMessage(err) })))
    return () => {
      alive = false
    }
  }, [from, to])
  return state
}

function Overview() {
  const [filter, update] = useAnalyticsFilter('month')
  const { from, to } = filter.range
  const { data, board, error } = useOverview(from, to)
  const metrics = useMetrics(filter)
  const pnl = `/dashboard/finance/pnl?period=custom&from=${from}&to=${to}`

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Финансы</h1>
        <p className="mt-1 text-sm text-muted">
          Сколько бизнес заработал за период и куда ушли деньги: начисления Ozon, себестоимость, ваши расходы из «Операций» и налог.
          Сколько денег сейчас на Ozon и на счетах и что впереди.
        </p>
      </div>
      <AnalyticsFilters value={filter} onChange={update} showProduct={false} showCategory={false} kinds={['finance', 'balance']} />
      <FormError message={error} />
      {!data && !error && <p className="text-sm text-muted">Загрузка…</p>}
      {data && !data.has_data && <Empty />}
      {data && data.has_data && (
        <>
          <Figures data={data} pnl={pnl} />
          <Notes data={data} />
          <Structure data={data} pnl={pnl} />
        </>
      )}
      <section className="space-y-2 rounded-xl border border-line bg-card p-4" data-testid="finance-dynamics">
        <div className="flex flex-wrap items-baseline gap-x-3">
          <h2 className="text-sm font-semibold">Динамика</h2>
          <span className="text-xs text-faint">по продажам на Ozon — без расходов бизнеса</span>
        </div>
        <MetricsState error={metrics.error} loading={metrics.loading} empty={!metrics.data} />
        {metrics.data && metrics.data.coverage.data_source !== 'none' && <DynamicsChart data={metrics.data} metrics={['revenue', 'profit']} />}
      </section>
      {board && <MoneyBlock payouts={board.payouts} hasKey={board.setup.has_key} />}
      <AttentionWidget area="finance" />
      <nav className="flex flex-wrap gap-x-5 gap-y-2 text-sm" aria-label="Подробнее" data-testid="finance-links">
        <Link href={pnl} className="text-accent hover:underline">
          P&amp;L за период →
        </Link>
        <Link href={`/dashboard/finance/pnl?period=custom&from=${from}&to=${to}&tab=products`} className="text-accent hover:underline">
          Прибыль по товарам →
        </Link>
        <Link href="/dashboard/finance/payouts" className="text-accent hover:underline">
          Выплаты Ozon →
        </Link>
        <Link href="/dashboard/finance/documents" className="text-accent hover:underline">
          Отчёты и документы →
        </Link>
      </nav>
    </div>
  )
}

function Empty() {
  return (
    <section className="space-y-3 rounded-xl border border-line bg-card p-6" data-testid="finance-empty">
      <h2 className="text-lg font-semibold">За этот период данных Ozon нет</h2>
      <p className="text-sm text-muted">
        Выручка и прибыль считаются по начислениям Ozon: загрузите отчёт по начислениям или подключите ключ Ozon API — начисления
        загрузятся сами. Это пропуск, а не ноль.
      </p>
      <div className="flex flex-wrap gap-3">
        <Link href="/dashboard/finance/documents" className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90">
          Загрузить отчёт
        </Link>
        <Link href="/dashboard/settings/integrations" className="rounded-lg border border-line px-4 py-2 text-sm hover:border-accent hover:text-accent">
          Подключить ключ Ozon API
        </Link>
      </div>
    </section>
  )
}

function Figures({ data, pnl }: { data: FinanceOverview; pnl: string }) {
  const f = data.figures
  const against = data.previous.has_data ? `к ${short(data.previous.date_from)}–${short(data.previous.date_to)}` : ''
  const was = (x: Figure, fmt: (v: string) => string) => (data.previous.has_data && x.previous !== null ? `было ${fmt(x.previous)}` : null)
  const profit = f.profit.value === null ? null : Number(f.profit.value)
  const noPrev = data.previous.has_data ? null : 'в прошлом периоде нет данных'
  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line lg:grid-cols-4" data-testid="finance-figures">
      <Card href={pnl} label="Выручка" value={rub(f.revenue.value)} testid="finance-revenue">
        <Change pct={f.revenue.change_pct ?? null} against={against} />
        <Hint>{was(f.revenue, rub) ?? noPrev ?? ''}</Hint>
      </Card>
      <Card href={pnl} label="Расходы" value={rub(f.expenses.value)} testid="finance-expenses">
        <Change pct={f.expenses.change_pct ?? null} against={against} upIsBad />
        <Hint>{was(f.expenses, rub) ?? 'Ozon, себестоимость, бизнес и налог'}</Hint>
      </Card>
      <Card
        href={pnl}
        label={data.profit_kind === 'net' ? 'Чистая прибыль' : 'Прибыль до налога'}
        value={rub(f.profit.value)}
        tone={profit === null ? undefined : profit < 0 ? 'bad' : 'ok'}
        testid="finance-profit"
      >
        <Change pct={f.profit.change_pct ?? null} against={against} />
        <Hint>{was(f.profit, rub) ?? (data.profit_kind === 'net' ? 'после налога' : 'налог не задан')}</Hint>
        <Hint>
          {data.business === null
            ? 'без расходов бизнеса — «Планирование» выключено'
            : `с расходами бизнеса ${rub(data.business.total)}; от продаж на Ozon ${rub(data.profit_ozon)}`}
        </Hint>
      </Card>
      <Card href={pnl} label="Рентабельность" value={pctText(f.margin.value)} testid="finance-margin">
        <Change pct={f.margin.change_pp ?? null} against={against} unit="п.п." />
        <Hint>{was(f.margin, pctText) ?? 'прибыль / выручка'}</Hint>
      </Card>
    </div>
  )
}

/** Чего не хватает цифрам: дней без данных, себестоимости, разметки расходов, налога. */
function Notes({ data }: { data: FinanceOverview }) {
  const notes: React.ReactNode[] = []
  if (data.coverage.missing_days > 0) notes.push(<>нет начислений за {daysWord(data.coverage.missing_days)} — цифры неполные</>)
  if (data.missing_cost > 0)
    notes.push(
      <>
        себестоимость не найдена у {data.missing_cost} тов. — прибыль завышена{' '}
        <Link href="/dashboard/products/costs" className="text-accent hover:underline">
          заполнить →
        </Link>
      </>,
    )
  if (data.business && data.business.uncategorized.count > 0)
    notes.push(
      <>
        расходов без статьи {data.business.uncategorized.count} на {rub(data.business.uncategorized.amount)} — в расходы не вошли{' '}
        <Link href={`/dashboard/operations?category=none&from=${data.date_from}&to=${data.date_to}`} className="text-accent hover:underline">
          разметить →
        </Link>
      </>,
    )
  if (!data.tax_set)
    notes.push(
      <>
        налог не задан — прибыль до налога{' '}
        <Link href="/dashboard/settings/taxes" className="text-accent hover:underline">
          указать →
        </Link>
      </>,
    )
  if (notes.length === 0) return null
  return (
    <ul className="space-y-0.5 text-sm text-warn" data-testid="finance-notes">
      {notes.map((n, i) => (
        <li key={i}>• {n}</li>
      ))}
    </ul>
  )
}

function Structure({ data, pnl }: { data: FinanceOverview; pnl: string }) {
  const items = data.structure
  const max = Math.max(...items.map((i) => Number(i.amount)), 1)
  const href = (i: Item) =>
    i.kind === 'business' && i.category_id
      ? opsHref(data.date_from, data.date_to, i.category_id)
      : i.kind === 'tax'
        ? pnl
        : `/dashboard/analytics/profit?period=custom&from=${data.date_from}&to=${data.date_to}`
  return (
    <section className="rounded-xl border border-line bg-card" data-testid="finance-structure">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line px-4 py-3">
        <h2 className="font-semibold">Куда ушла выручка</h2>
        <span className="flex flex-wrap gap-x-3 text-xs text-muted">
          {(Object.keys(KIND) as Item['kind'][])
            .filter((k) => items.some((i) => i.kind === k))
            .map((k) => (
              <span key={k} className="flex items-center gap-1">
                <span className={`inline-block h-2.5 w-2.5 rounded-sm ${KIND[k].bar}`} /> {KIND[k].label}
              </span>
            ))}
        </span>
        <Link href={pnl} className="ml-auto text-sm text-accent hover:underline">
          P&amp;L строками →
        </Link>
      </div>
      {items.length === 0 ? (
        <p className="px-4 py-3 text-sm text-muted">Расходов за период нет.</p>
      ) : (
        <ul className="divide-y divide-line">
          {items.map((i) => {
            const v = Number(i.amount)
            return (
              <li key={i.key} data-testid="structure-item" data-kind={i.kind}>
                <Link href={href(i)} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 px-4 py-2 text-sm hover:bg-accent/5 sm:grid-cols-[14rem_minmax(0,1fr)_auto]">
                  <span className="min-w-0 truncate">{i.title}</span>
                  <span className="order-3 col-span-2 h-2 rounded-full bg-line sm:order-none sm:col-span-1">
                    {v > 0 && <span className={`block h-2 rounded-full ${KIND[i.kind].bar}`} style={{ width: `${Math.max((v / max) * 100, 1)}%` }} />}
                  </span>
                  <span className="text-right tabular-nums">
                    <span className={v < 0 ? 'text-ok' : ''}>{v < 0 ? `+${rub(String(-v))}` : rub(i.amount)}</span>
                    <span className="ml-2 text-xs text-muted">{pctText(i.share)}</span>
                  </span>
                </Link>
              </li>
            )
          })}
        </ul>
      )}
      <p className="border-t border-line px-4 py-2 text-xs text-faint">
        % — от выручки. Сумма строк = расходы: выручка − {data.profit_kind === 'net' ? 'чистая прибыль' : 'прибыль до налога'}; в плюс —
        доплаты Ozon (баллы за скидки, компенсации), они уменьшают расходы.
        {data.business === null && ' Расходы бизнеса не учтены — «Планирование» выключено.'}
      </p>
    </section>
  )
}

'use client'
// «Аналитика прибыльности» (шаг 10.9): выручка, расходы, прибыль и маржа к прошлому периоду, график, структура расходов
// по группам статей (статья → товары → исходные строки начислений). Таблица товаров — «Прибыль по товарам» 3.4
// (ProductsView с быстрыми фильтрами), не своя. Данные — GET /analytics/profit/structure, /profit/group, /profit/lines.
import Link from 'next/link'
import { useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { MetricChart } from '@/components/analytics'
import { Change, Hint } from '@/components/dashboard'
import { Card } from '@/components/home'
import { ProductLines } from '@/components/products-pnl'
import { type AnalyticsFilter, type MetricKey, type MetricPoint, type Metrics, formatMetric } from '@/lib/analytics'
import { api, errorMessage } from '@/lib/api'

type Group = {
  key: string
  title: string
  amount: string
  share: string | null
  prev_amount: string | null
  types: { name: string; amount: string }[]
}
export type ProfitStructure = {
  date_from: string
  date_to: string
  scope: 'store' | 'products'
  has_data: boolean
  revenue: string | null
  expenses: string | null
  profit: string | null
  margin: string | null
  missing_cost: number
  net: string | null
  groups: Group[]
  previous: { date_from: string; date_to: string; revenue: string | null; expenses: string | null; profit: string | null; margin: string | null } | null
}
type GroupDetail = {
  key: string
  title: string
  products: { id: string | null; offer_id: string; name: string; amount: string; revenue: string; share: string | null }[]
  unallocated: string | null
}

const range = (from: string, to: string) => `period=custom&from=${from}&to=${to}`
const short = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`
const pct = (v: string | number) => `${Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} %`.replace('-', '−')

export function profitParams(f: AnalyticsFilter) {
  return {
    date_from: f.range.from,
    date_to: f.range.to,
    ...(f.product ? { product_id: f.product } : {}),
    ...(f.category !== null ? { category_id: String(f.category) } : {}),
  }
}

/** Изменение к прошлому периоду, %; прошлого нет или он ноль — null. */
function change(cur: string | null, prev: string | null | undefined): string | null {
  if (cur === null || prev === null || prev === undefined || Number(prev) === 0) return null
  return (((Number(cur) - Number(prev)) / Math.abs(Number(prev))) * 100).toFixed(1)
}

export function useProfitStructure(filter: AnalyticsFilter) {
  const [state, setState] = useState<{ data: ProfitStructure | null; error: string | null }>({ data: null, error: null })
  const key = JSON.stringify(profitParams(filter))
  useEffect(() => {
    let alive = true
    setState((s) => ({ ...s, error: null }))
    api
      .get<ProfitStructure>('/analytics/profit/structure', { params: JSON.parse(key) })
      .then((r) => alive && setState({ data: r.data, error: null }))
      .catch((err) => alive && setState({ data: null, error: errorMessage(err) }))
    return () => {
      alive = false
    }
  }, [key])
  return state
}

/** Выручка, расходы, прибыль до налога, маржинальность — с изменением к прошлому периоду. */
export function ProfitFigures({ s, costLabel }: { s: ProfitStructure; costLabel: string }) {
  const p = s.previous
  const against = p ? `к ${short(p.date_from)}–${short(p.date_to)}` : ''
  const was = (v: string | null | undefined, f: 'rub' | 'pct') => (p ? (v == null ? 'в прошлом периоде нет данных' : `было ${formatMetric(v, f)}`) : null)
  const profit = s.profit === null ? null : Number(s.profit)
  const marginPp = s.margin !== null && p?.margin != null ? (Number(s.margin) - Number(p.margin)).toFixed(1) : null
  const store = s.scope === 'store'
  const pnl = `/dashboard/finance/pnl?${range(s.date_from, s.date_to)}`
  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line lg:grid-cols-4" data-testid="profit-figures">
      <Card href={`/dashboard/analytics/sales?${range(s.date_from, s.date_to)}`} label="Выручка" value={formatMetric(s.revenue, 'rub')} testid="figure-revenue">
        <Change pct={change(s.revenue, p?.revenue)} against={against} />
        <Hint>{s.revenue === null ? 'нет данных за период' : (was(p?.revenue, 'rub') ?? 'строки «Выручка» Ozon')}</Hint>
      </Card>
      <Card href={null} label="Расходы" value={formatMetric(s.expenses, 'rub')} testid="figure-expenses">
        <Change pct={change(s.expenses, p?.expenses)} against={against} upIsBad />
        <Hint>{s.expenses === null ? 'нет данных за период' : (was(p?.expenses, 'rub') ?? `Ozon и ${costLabel.toLowerCase()}`)}</Hint>
        {s.missing_cost > 0 && <Hint tone="warn">без себестоимости {s.missing_cost} тов. — расходы неполные</Hint>}
      </Card>
      <Card
        href={store ? pnl : null}
        label="Прибыль до налога"
        value={formatMetric(s.profit, 'rub')}
        tone={profit === null ? undefined : profit < 0 ? 'bad' : 'ok'}
        testid="figure-profit"
      >
        <Change pct={change(s.profit, p?.profit)} against={against} />
        <Hint>{s.profit === null ? 'нет данных за период' : (was(p?.profit, 'rub') ?? 'выручка − расходы')}</Hint>
        {s.net !== null && <Hint>после налога — {formatMetric(s.net, 'rub')}</Hint>}
        {!store && s.profit !== null && <Hint>по товарам — без начислений, не относящихся к товару</Hint>}
        {store && s.profit !== null && <Hint>от продаж на Ozon, без расходов бизнеса — они в «Финансах»</Hint>}
      </Card>
      <Card href={store ? pnl : null} label="Маржинальность" value={formatMetric(s.margin, 'pct')} testid="figure-margin">
        <Change pct={marginPp} against={against} unit="п.п." />
        <Hint>{s.margin === null ? 'нет данных за период' : (was(p?.margin, 'pct') ?? 'прибыль / выручка')}</Hint>
      </Card>
    </div>
  )
}

const CHART: { key: MetricKey; label: string }[] = [
  { key: 'profit', label: 'Прибыль' },
  { key: 'revenue', label: 'Выручка' },
  { key: 'margin', label: 'Маржинальность' },
]

/** График прибыли, выручки или маржи; точка — эта страница за день, неделю или месяц, «Подробнее →» — P&L периода. */
export function ProfitChart({ data }: { data: Metrics }) {
  const [metric, setMetric] = useState<MetricKey>('profit')
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2" role="group" aria-label="Показатель на графике">
        {CHART.map((c) => (
          <button
            key={c.key}
            type="button"
            onClick={() => setMetric(c.key)}
            aria-pressed={metric === c.key}
            className={`rounded-lg border px-3 py-1 text-sm ${metric === c.key ? 'border-accent bg-accent/10 text-accent' : 'border-line text-muted hover:text-fg'}`}
            data-testid={`profit-metric-${c.key}`}
          >
            {c.label}
          </button>
        ))}
      </div>
      <MetricChart
        data={data}
        metric={metric}
        title={metric === 'profit' ? 'Прибыль до налога' : metric === 'margin' ? 'Маржинальность' : 'Выручка'}
        pointHref={(p: MetricPoint) => `/dashboard/analytics/profit?${range(p.date_from, p.date_to)}`}
        moreHref={data.filter.product_count === null ? `/dashboard/finance/pnl?${range(data.date_from, data.date_to)}` : undefined}
      />
    </div>
  )
}

const NOTE: Record<string, string> = {
  cost: 'Себестоимость проданных товаров — как в P&L: с НДС поставщика или без, по налоговому профилю.',
  commission: 'Вознаграждение Ozon за продажу и его возврат — как колонка «Комиссия» в таблице товаров.',
  logistics: 'Доставка до покупателя, обратная логистика, сборка, упаковка, обработка возвратов — как колонка «Логистика».',
  ads: 'Продвижение, трафареты, оплата за клик и за заказ.',
  storage: 'Размещение и хранение на складах Ozon.',
  returns: 'Возвращённая покупателям выручка.',
  other: 'Эквайринг, подписки, страхование, баллы за скидки и остальные начисления; минус — Ozon доплатил больше, чем удержал.',
  adjustment: 'Ручная сумма «сколько пришло» в отчёте отличается от суммы по его строкам — разница учтена в P&L.',
}

/** Структура расходов: группы статей с долей выручки и изменением; группа раскрывается — статьи и товары, товар —
 * исходные строки начислений. Сумма групп = выручка − прибыль до налога. */
export function ExpenseStructure({ s, filter }: { s: ProfitStructure; filter: AnalyticsFilter }) {
  const [open, setOpen] = useState<string | null>(null)
  if (!s.has_data || s.groups.length === 0) return null
  const max = Math.max(...s.groups.map((g) => Math.abs(Number(g.amount))), 1)
  const against = s.previous ? `к ${short(s.previous.date_from)}–${short(s.previous.date_to)}` : ''
  return (
    <section className="rounded-xl border border-line bg-card" data-testid="expense-structure">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line px-4 py-3">
        <h2 className="font-semibold">Структура расходов</h2>
        <span className="text-xs text-muted">
          {formatMetric(s.expenses, 'rub')}
          {s.revenue !== null && Number(s.revenue) > 0 && ` — ${pct((Number(s.expenses) / Number(s.revenue)) * 100)} выручки`}
        </span>
        {s.scope === 'store' && (
          <Link href={`/dashboard/finance/pnl?${range(s.date_from, s.date_to)}`} className="ml-auto text-xs text-accent hover:underline" data-testid="structure-pnl">
            Все статьи — P&amp;L →
          </Link>
        )}
      </div>
      <ul className="divide-y divide-line">
        {s.groups.map((g) => {
          const v = Number(g.amount)
          const expanded = open === g.key
          return (
            <li key={g.key} data-testid="expense-group" data-key={g.key}>
              <button
                type="button"
                onClick={() => setOpen(expanded ? null : g.key)}
                aria-expanded={expanded}
                className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 px-4 py-2.5 text-left text-sm hover:bg-line/40 sm:grid-cols-[minmax(0,13rem)_minmax(0,1fr)_7rem_4rem_7rem]"
              >
                <span className="truncate font-medium">
                  <span className="mr-1 inline-block w-3 text-faint">{expanded ? '▾' : '▸'}</span>
                  {g.title}
                </span>
                <span className="col-span-2 row-start-2 h-1.5 rounded-full bg-line sm:col-span-1 sm:row-start-auto">
                  <span className={`block h-1.5 rounded-full ${v < 0 ? 'bg-ok/70' : 'bg-bad/70'}`} style={{ width: `${(Math.abs(v) / max) * 100}%` }} />
                </span>
                <span className={`whitespace-nowrap text-right tabular-nums ${v < 0 ? 'text-ok' : ''}`} data-testid="expense-amount">
                  {formatMetric(g.amount, 'rub')}
                </span>
                <span className="hidden text-right text-xs tabular-nums text-faint sm:block" title="Доля выручки">
                  {g.share === null ? '' : pct(g.share)}
                </span>
                <span className="hidden text-right sm:block">
                  <Change pct={change(g.amount, g.prev_amount)} against="" upIsBad />
                </span>
              </button>
              {expanded && <GroupDetailView group={g} filter={filter} against={against} />}
            </li>
          )
        })}
      </ul>
      <div className="space-y-1 border-t border-line px-4 py-3 text-sm">
        <div className="flex justify-between">
          <span className="text-muted">Выручка</span>
          <span className="tabular-nums">{formatMetric(s.revenue, 'rub')}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted">− Расходы</span>
          <span className="tabular-nums">{formatMetric(s.expenses, 'rub')}</span>
        </div>
        <div className="flex justify-between font-semibold" data-testid="structure-profit">
          <span>= Прибыль до налога</span>
          <span className={`tabular-nums ${Number(s.profit) < 0 ? 'text-bad' : 'text-ok'}`}>{formatMetric(s.profit, 'rub')}</span>
        </div>
        <p className="pt-1 text-xs text-faint">
          {s.scope === 'store'
            ? 'Как в P&L периода: все начисления Ozon, себестоимость и поправки отчётов. Нажмите на группу — статьи и товары.'
            : 'По выбранным товарам — их строки «Прибыли по товарам»; хранение, подписки и другие начисления без товара сюда не входят.'}
        </p>
      </div>
    </section>
  )
}

/** Раскрытая группа: пояснение, статьи, товары (больше расход — выше), начисления без товара. */
function GroupDetailView({ group, filter, against }: { group: Group; filter: AnalyticsFilter; against: string }) {
  const [detail, setDetail] = useState<GroupDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [product, setProduct] = useState<string | null>(null)
  const [all, setAll] = useState(false)
  const key = JSON.stringify(profitParams(filter))
  useEffect(() => {
    if (group.key === 'adjustment') return
    let alive = true
    api
      .get<GroupDetail>('/analytics/profit/group', { params: { ...JSON.parse(key), key: group.key } })
      .then((r) => alive && setDetail(r.data))
      .catch((err) => alive && setError(errorMessage(err)))
    return () => {
      alive = false
    }
  }, [group.key, key])
  const shown = detail ? (all ? detail.products : detail.products.slice(0, 10)) : []
  return (
    <div className="space-y-3 bg-bg/40 px-4 pb-4 pt-1 text-sm" data-testid="group-detail">
      <p className="text-xs text-muted">
        {NOTE[group.key]}
        {group.prev_amount !== null && against && <span className="text-faint"> Было {formatMetric(group.prev_amount, 'rub')} {against}.</span>}
      </p>
      {group.key === 'adjustment' && (
        <Link href="/dashboard/analytics/reports" className="text-xs text-accent hover:underline">
          Отчёты и их суммы →
        </Link>
      )}
      {group.types.length > 1 && (
        <div>
          <h3 className="text-xs uppercase tracking-wider text-muted">Статьи</h3>
          <ul className="mt-1 divide-y divide-line">
            {group.types.map((t) => (
              <li key={t.name} className="flex justify-between gap-3 py-1" data-testid="group-type">
                <span className="min-w-0 truncate" title={t.name}>
                  {t.name}
                </span>
                <span className={`whitespace-nowrap tabular-nums ${Number(t.amount) < 0 ? 'text-ok' : ''}`}>{formatMetric(t.amount, 'rub')}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <FormError message={error} />
      {group.key !== 'adjustment' && !detail && !error && <p className="text-xs text-muted">Загрузка товаров…</p>}
      {detail && (
        <div>
          <h3 className="text-xs uppercase tracking-wider text-muted">
            {detail.products.length ? `Товары — ${detail.products.length}` : 'Товаров нет'}
          </h3>
          <ul className="mt-1 divide-y divide-line">
            {shown.map((p) => {
              const expanded = product === p.offer_id
              return (
                <li key={p.offer_id} data-testid="group-product">
                  <button
                    type="button"
                    onClick={() => setProduct(expanded ? null : p.offer_id)}
                    aria-expanded={expanded}
                    className="flex w-full items-baseline gap-3 py-1.5 text-left hover:text-accent"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium">{p.offer_id}</span>
                      {p.name && <span className="block truncate text-xs text-faint">{p.name}</span>}
                    </span>
                    <span className="text-right">
                      <span className={`block whitespace-nowrap tabular-nums ${Number(p.amount) < 0 ? 'text-ok' : ''}`}>{formatMetric(p.amount, 'rub')}</span>
                      {p.share !== null && <span className="block text-xs tabular-nums text-faint">{pct(p.share)} его выручки</span>}
                    </span>
                  </button>
                  {expanded && (
                    <div className="mb-2 rounded-lg border border-line bg-card px-3 py-2">
                      {group.key === 'cost' ? (
                        <p className="text-xs text-muted">
                          Себестоимость — из справочника на дату продажи, а не из начислений Ozon.{' '}
                          <Link href={`/dashboard/products/costs?search=${encodeURIComponent(p.offer_id)}`} className="text-accent hover:underline" data-testid="group-cost-edit">
                            Изменить себестоимость →
                          </Link>
                        </p>
                      ) : (
                        <ProductLines offerId={p.offer_id} from={filter.range.from} to={filter.range.to} group={group.key} groupTitle={group.title} />
                      )}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
          {detail.products.length > 10 && (
            <button type="button" onClick={() => setAll(!all)} className="mt-1 text-xs text-accent hover:underline">
              {all ? 'Свернуть' : `Показать все ${detail.products.length}`}
            </button>
          )}
          {detail.unallocated !== null && (
            <p className="mt-2 flex justify-between gap-3 border-t border-line pt-2 text-muted" data-testid="group-unallocated">
              <span>Без товара — начисления на весь магазин</span>
              <span className="whitespace-nowrap tabular-nums">{formatMetric(detail.unallocated, 'rub')}</span>
            </p>
          )}
        </div>
      )}
    </div>
  )
}

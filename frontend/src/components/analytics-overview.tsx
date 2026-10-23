'use client'
// «Обзор бизнеса» (шаг 10.7): лучшие и худшие товары по прибыли и по изменению продаж к прошлому периоду (только
// заметные изменения — не случайные колебания, как в аномалиях), переходы к подробным отчётам. Показатели, график и
// фильтры — общие: components/analytics.tsx и components/home.tsx.
import Link from 'next/link'
import { useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { type AnalyticsFilter, formatMetric } from '@/lib/analytics'
import { api, errorMessage } from '@/lib/api'

type Ref = { id: string | null; offer_id: string; name: string | null }
type ProfitLeader = { product: Ref; revenue: string; profit: string; margin: string | null; qty: number }
type SalesLeader = { product: Ref; qty: number; prev_qty: number; expected: string; change_pct: string | null }
export type Leaders = {
  date_from: string
  date_to: string
  previous: { date_from: string; date_to: string }
  profit: { best: ProfitLeader[]; worst: ProfitLeader[]; missing_cost: number; data_source: string }
  sales: { compared: boolean; note: string | null; growth: SalesLeader[]; decline: SalesLeader[]; days: number; prev_days: number }
}

const short = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`
const pnl = (from: string, to: string) => `/dashboard/finance/pnl?period=custom&from=${from}&to=${to}`

function useLeaders(filter: AnalyticsFilter) {
  const [data, setData] = useState<Leaders | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { from, to } = filter.range
  useEffect(() => {
    let alive = true
    setError(null)
    api
      .get<Leaders>('/analytics/leaders', {
        params: {
          date_from: from,
          date_to: to,
          ...(filter.product ? { product_id: filter.product } : {}),
          ...(filter.category !== null ? { category_id: filter.category } : {}),
        },
      })
      .then(({ data }) => alive && setData(data))
      .catch((err) => alive && setError(errorMessage(err)))
    return () => {
      alive = false
    }
  }, [from, to, filter.product, filter.category])
  return { data, error }
}

/** Товар — ссылкой на его карточку (статистика товара — шаг 10.14); не из каталога — просто артикул. */
function ProductCell({ p }: { p: Ref }) {
  const body = (
    <>
      <span className="block truncate text-sm font-medium">{p.offer_id}</span>
      {p.name && <span className="block truncate text-xs text-faint">{p.name}</span>}
    </>
  )
  return p.id ? (
    <Link href={`/dashboard/products/${p.id}`} className="min-w-0 flex-1 hover:text-accent" data-testid="leader-product">
      {body}
    </Link>
  ) : (
    <div className="min-w-0 flex-1">{body}</div>
  )
}

function List<T extends { product: Ref }>({ title, items, empty, testid, value }: {
  title: string
  items: T[]
  empty: string
  testid: string
  value: (x: T) => React.ReactNode
}) {
  return (
    <div className="min-w-0" data-testid={testid}>
      <h3 className="text-xs font-medium uppercase tracking-wide text-muted">{title}</h3>
      {items.length === 0 ? (
        <p className="mt-2 text-sm text-faint">{empty}</p>
      ) : (
        <ul className="mt-1 divide-y divide-line">
          {items.map((x) => (
            <li key={x.product.offer_id} className="flex items-center gap-3 py-2" data-testid="leader-item">
              <ProductCell p={x.product} />
              <div className="shrink-0 text-right text-sm">{value(x)}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function profitValue(x: ProfitLeader) {
  const n = Number(x.profit)
  return (
    <>
      <span className={n < 0 ? 'text-bad' : 'text-ok'}>{formatMetric(x.profit, 'rub')}</span>
      {x.margin !== null && <span className="block text-xs text-faint">маржа {formatMetric(x.margin, 'pct')}</span>}
    </>
  )
}

function salesValue(x: SalesLeader, up: boolean) {
  const pct = x.change_pct === null ? 'с нуля' : `${up ? '▲' : '▼'} ${Math.abs(Number(x.change_pct)).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} %`
  return (
    <>
      <span className={up ? 'text-ok' : 'text-bad'}>{pct}</span>
      <span className="block text-xs text-faint">
        было {x.prev_qty} → {x.qty} шт.
      </span>
    </>
  )
}

export function LeadersBlock({ filter }: { filter: AnalyticsFilter }) {
  const { data, error } = useLeaders(filter)
  if (error) return <FormError message={error} />
  if (!data) return null
  const p = data.profit
  const s = data.sales
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <section className="rounded-xl border border-line bg-card p-4" data-testid="leaders-profit">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="font-semibold">Прибыль по товарам</h2>
          <span className="text-xs text-muted">
            {short(data.date_from)}–{short(data.date_to)}
          </span>
          <Link
            href={`/dashboard/analytics/profit?period=custom&from=${data.date_from}&to=${data.date_to}`}
            className="ml-auto text-sm text-accent hover:underline"
            data-testid="leaders-profit-more"
          >
            Подробнее →
          </Link>
        </div>
        {p.data_source === 'none' ? (
          <p className="mt-3 text-sm text-muted">За период нет начислений Ozon — прибыль по товарам считать не из чего.</p>
        ) : (
          <div className="mt-3 grid gap-4 sm:grid-cols-2">
            <List title="Лучшие" items={p.best} empty="Товаров в плюсе нет" testid="leaders-best" value={profitValue} />
            <List title="Худшие" items={p.worst} empty="Остальные — в списке лучших" testid="leaders-worst" value={profitValue} />
          </div>
        )}
        {p.missing_cost > 0 && (
          <p className="mt-3 text-xs text-warn" data-testid="leaders-missing-cost">
            Без себестоимости {p.missing_cost} тов. — их прибыль не посчитать, в списках их нет.{' '}
            <Link href="/dashboard/products/costs?missing=1" className="underline">
              Заполнить →
            </Link>
          </p>
        )}
      </section>

      <section className="rounded-xl border border-line bg-card p-4" data-testid="leaders-sales">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="font-semibold">Продажи к прошлому периоду</h2>
          <span className="text-xs text-muted">
            к {short(data.previous.date_from)}–{short(data.previous.date_to)}
          </span>
        </div>
        <p className="mt-1 text-xs text-faint">
          Только заметные изменения: случайные колебания малых продаж сюда не попадают.
          {s.compared && s.days !== s.prev_days && (
            <span data-testid="leaders-days-note">
              {' '}
              Дней с данными сейчас {s.days}, тогда {s.prev_days} — % посчитан с поправкой на это.
            </span>
          )}
        </p>
        {!s.compared ? (
          <p className="mt-3 text-sm text-muted" data-testid="leaders-sales-note">
            {s.note}
          </p>
        ) : (
          <div className="mt-3 grid gap-4 sm:grid-cols-2">
            <List title="Растут" items={s.growth} empty="Заметного роста нет" testid="leaders-growth" value={(x) => salesValue(x, true)} />
            <List title="Падают" items={s.decline} empty="Заметного падения нет" testid="leaders-decline" value={(x) => salesValue(x, false)} />
          </div>
        )}
      </section>
    </div>
  )
}

/** Подробные отчёты за выбранный период. */
export function ReportLinks({ from, to }: { from: string; to: string }) {
  const links = [
    { href: pnl(from, to), label: 'P&L — прибыль за период' },
    { href: `${pnl(from, to)}&tab=products`, label: 'Прибыль по товарам' },
    { href: '/dashboard/finance/payouts', label: 'Выплаты Ozon' },
    { href: '/dashboard/analytics/reports', label: 'Отчёты Ozon и загрузка' },
  ]
  return (
    <section className="rounded-xl border border-line bg-card p-4" data-testid="report-links">
      <h2 className="font-semibold">Подробные отчёты</h2>
      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-2 text-sm">
        {links.map((l) => (
          <Link key={l.label} href={l.href} className="text-accent hover:underline">
            {l.label} →
          </Link>
        ))}
      </div>
    </section>
  )
}

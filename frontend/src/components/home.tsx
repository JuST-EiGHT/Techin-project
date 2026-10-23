'use client'
// Главная (шаг 10.4, docs/SITEMAP.md п. 1): ключевые показатели с прошлым периодом, один график динамики и «Деньги и
// ближайшие события». Показатели — основа аналитики (10.3), выплаты Ozon — 3.6, прогноз денег — «Планирование» (3.9–3.10).
import Link from 'next/link'
import { useEffect, useState } from 'react'
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { MetricChart } from '@/components/analytics'
import { type Drill, MoreLink, TipBox, useDrill } from '@/components/charts'
import { Change, FigureLink, Hint, type OzonDashboard } from '@/components/dashboard'
import { compact, useTokenColors } from '@/components/finance'
import { weekdayShort } from '@/components/payouts'
import { dayTitle, signed } from '@/components/planning/Calendar'
import { type Overview, type PlanningState, opsHref } from '@/components/planning/types'
import { fmtDay, rub } from '@/components/reports'
import { METRICS, type MetricKey, type MetricPoint, type Metrics, formatMetric } from '@/lib/analytics'
import { api } from '@/lib/api'
import { useSyncStatus } from '@/lib/sync'

const range = (from: string, to: string) => `period=custom&from=${from}&to=${to}`

/** Куда ведут показатели: выручка, заказы и продажи — «Аналитика продаж» (10.8), прибыль и маржа — «Аналитика
 * прибыльности» (10.9). */
export const METRIC_LINKS: Partial<Record<MetricKey, (from: string, to: string) => string>> = {
  revenue: (from, to) => `/dashboard/analytics/sales?${range(from, to)}`,
  orders: (from, to) => `/dashboard/analytics/sales?${range(from, to)}`,
  sold_qty: (from, to) => `/dashboard/analytics/sales?${range(from, to)}`,
  profit: (from, to) => `/dashboard/analytics/profit?${range(from, to)}`,
  margin: (from, to) => `/dashboard/analytics/profit?${range(from, to)}`,
}

const short = (iso: string) => fmtDay(iso).slice(0, 5)

/** Карточка показателя: ссылка на подробности или просто цифра, если подробной страницы пока нет. */
export function Card({ href, label, value, testid, tone, children }: {
  href: string | null
  label: string
  value: string
  testid: string
  tone?: 'ok' | 'bad'
  children?: React.ReactNode
}) {
  if (href) return <FigureLink href={href} label={label} value={value} tone={tone} testid={testid}>{children}</FigureLink>
  return (
    <div className="bg-card px-4 py-3" data-testid={testid}>
      <div className="text-[11px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`mt-1 text-lg font-bold tabular-nums ${tone === 'ok' ? 'text-ok' : tone === 'bad' ? 'text-bad' : ''}`}>{value}</div>
      <div className="mt-0.5 space-y-0.5">{children}</div>
    </div>
  )
}

/** Выручка, прибыль, маржинальность, заказы: значение, ▲ / ▼ к прошлому периоду, «было …», переход в подробности. */
/** Четыре главные цифры: выручка, прибыль, маржинальность и заказы (главная) или продажи в штуках («Обзор бизнеса»). */
export function KeyFigures({ data, taxSet, costLabel, fourth = 'orders' }: {
  data: Metrics
  taxSet: boolean
  costLabel: string
  fourth?: 'orders' | 'sold_qty'
}) {
  const t = data.totals
  const prev = data.previous?.totals ?? null
  const ch = data.changes
  const against = data.previous ? `к ${short(data.previous.date_from)}–${short(data.previous.date_to)}` : ''
  const link = (k: MetricKey) => METRIC_LINKS[k]?.(data.date_from, data.date_to) ?? null
  const was = (k: MetricKey) =>
    prev === null ? null : prev[k] === null ? 'в прошлом периоде нет данных' : `было ${formatMetric(prev[k], METRICS[k].format)}`
  const profit = t.profit === null ? null : Number(t.profit)
  const noData = (k: MetricKey) => (t[k] === null ? 'нет данных за период' : null)
  const byProducts = data.filter.product_count !== null // товар или категория — прибыль до налога, как «Прибыль по товарам»
  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line lg:grid-cols-4" data-testid="key-figures">
      <Card href={link('revenue')} label="Выручка" value={formatMetric(t.revenue, 'rub')} testid="figure-revenue">
        {ch && <Change pct={ch.revenue} against={against} />}
        <Hint>{noData('revenue') ?? was('revenue') ?? `продано ${formatMetric(t.sold_qty, 'qty')}`}</Hint>
      </Card>
      <Card
        href={link('profit')}
        label={data.profit_kind === 'net' ? 'Прибыль после налога' : 'Прибыль до налога'}
        value={formatMetric(t.profit, 'rub')}
        tone={profit === null ? undefined : profit < 0 ? 'bad' : 'ok'}
        testid="figure-profit"
      >
        {ch && <Change pct={ch.profit} against={against} />}
        <Hint>{noData('profit') ?? was('profit') ?? ''}</Hint>
        {t.profit !== null && t.missing_cost > 0 && (
          <Hint tone="warn">
            {costLabel}: не найдена у {t.missing_cost} тов. — прибыль завышена
          </Hint>
        )}
        {t.profit !== null && data.profit_kind === 'before_tax' && (
          <Hint>
            {byProducts
              ? 'по товарам — до налога, как в «Прибыли по товарам»'
              : taxSet
                ? 'до налога — в прошлом периоде налог не посчитан'
                : 'налог не задан — без налога'}
          </Hint>
        )}
      </Card>
      <Card href={link('margin')} label="Маржинальность" value={formatMetric(t.margin, 'pct')} testid="figure-margin">
        {ch && <Change pct={ch.margin_pp} against={against} unit="п.п." />}
        <Hint>{noData('margin') ?? was('margin') ?? 'прибыль / выручка'}</Hint>
      </Card>
      {fourth === 'sold_qty' ? (
        <Card href={link('sold_qty')} label="Продажи, шт." value={formatMetric(t.sold_qty, 'qty')} testid="figure-sold_qty">
          {ch && <Change pct={ch.sold_qty} against={against} />}
          <Hint>{noData('sold_qty') ?? was('sold_qty') ?? `возвратов ${formatMetric(t.returned_qty, 'qty')}`}</Hint>
        </Card>
      ) : (
        <Card href={link('orders')} label="Заказы" value={formatMetric(t.orders, 'count')} testid="figure-orders">
          {ch && <Change pct={ch.orders} against={against} />}
          <Hint>
            {t.orders === null
              ? 'отправления не загружены — нужен ключ Ozon'
              : (was('orders') ?? `${formatMetric(t.ordered_qty, 'qty')} в заказах`)}
          </Hint>
        </Card>
      )}
    </div>
  )
}

const CHART_METRICS: { key: MetricKey; label: string }[] = [
  { key: 'revenue', label: 'Выручка' },
  { key: 'profit', label: 'Прибыль' },
  { key: 'orders', label: 'Заказы' },
  { key: 'sold_qty', label: 'Продажи, шт.' },
]

/** Один график с переключением показателя; точка и «Подробнее →» ведут туда же, куда карточка показателя. */
export function DynamicsChart({ data }: { data: Metrics }) {
  const [metric, setMetric] = useState<MetricKey>('revenue')
  const link = METRIC_LINKS[metric]
  return (
    <div className="space-y-2" data-testid="dynamics">
      <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Показатель на графике">
        {CHART_METRICS.map((m) => (
          <button
            key={m.key}
            type="button"
            aria-pressed={metric === m.key}
            onClick={() => setMetric(m.key)}
            className={`rounded-lg border px-2.5 py-1 text-sm ${metric === m.key ? 'border-accent bg-accent/10 text-accent' : 'border-line text-muted hover:text-fg'}`}
            data-testid={`chart-metric-${m.key}`}
          >
            {m.label}
          </button>
        ))}
      </div>
      <MetricChart
        data={data}
        metric={metric}
        title={`${METRICS[metric].label}${metric === 'orders' ? ' — оформлены покупателями' : metric === 'sold_qty' ? ' — начислены Ozon' : ''}`}
        pointHref={link ? (p: MetricPoint) => link(p.date_from, p.date_to) : undefined}
        moreHref={link ? link(data.date_from, data.date_to) : undefined}
      />
    </div>
  )
}

// ── деньги и ближайшие события ──
type BalanceRow = { day: string; label: string; fact: number | null; plan: number | null; kind: 'fact' | 'plan'; balance: string | null }

function BalanceTip({ active, payload, drill, today }: { active?: boolean; payload?: { payload: BalanceRow }[]; drill: Drill<BalanceRow>; today: string }) {
  if (!active || !payload?.length) return null
  const r = payload[0].payload
  return (
    <TipBox href={drill.hrefFor(r)} touch={drill.touch}>
      <div className="font-medium">
        {dayTitle(r.day)} · {r.day === today ? 'сегодня' : r.kind === 'fact' ? 'факт' : 'прогноз'}
      </div>
      <div className="tabular-nums">Остаток: {r.balance === null ? 'нет данных' : rub(r.balance)}</div>
    </TipBox>
  )
}

/** Маленький график остатка: факт сплошной, прогноз пунктиром, порог и ноль — линиями. */
function BalanceChart({ data }: { data: Overview }) {
  const colors = useTokenColors()
  const drill = useDrill<BalanceRow>((r) => (r.kind === 'fact' ? opsHref(r.day, r.day) : `/dashboard/planning/calendar?day=${r.day}`))
  if (!colors) return null
  const rows: BalanceRow[] = data.series.map((s) => {
    const b = s.balance === null ? null : Number(s.balance)
    return { day: s.day, label: short(s.day), kind: s.kind, balance: s.balance, fact: s.day <= data.today ? b : null, plan: s.day >= data.today ? b : null }
  })
  return (
    <div className={`h-32 ${drill.boxProps.className}`} onPointerDown={drill.boxProps.onPointerDown} data-testid="money-chart">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 8, right: 4, bottom: 0, left: 0 }} onClick={drill.onClick}>
          <CartesianGrid vertical={false} stroke={colors.line} />
          <XAxis dataKey="label" tick={{ fill: colors.muted, fontSize: 10 }} tickLine={false} axisLine={{ stroke: colors.line }} minTickGap={16} />
          <YAxis tick={{ fill: colors.muted, fontSize: 10 }} tickFormatter={compact} tickLine={false} axisLine={false} width={64} />
          <ReferenceLine y={0} stroke={colors.bad} strokeDasharray="4 3" />
          {data.threshold !== null && <ReferenceLine y={Number(data.threshold)} stroke={colors.warn} strokeDasharray="4 3" />}
          <Tooltip content={<BalanceTip drill={drill} today={data.today} />} cursor={{ stroke: colors.faint, strokeDasharray: '3 3' }} {...drill.tooltipProps} />
          <Line type="monotone" dataKey="fact" stroke={colors.fg} strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false} />
          <Line type="monotone" dataKey="plan" stroke={colors.accent} strokeWidth={2} strokeDasharray="5 3" dot={false} connectNulls={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

/** Риск кассового разрыва одной строкой: разрыв — красным, ниже порога — жёлтым, иначе — до какого минимума опустится. */
function Risk({ data }: { data: Overview }) {
  if (data.first_below_zero) {
    const gap = data.gaps.find((g) => g.below_zero)
    return (
      <p className="text-sm font-medium text-bad" data-testid="money-risk" data-risk="gap">
        Кассовый разрыв {dayTitle(data.first_below_zero)}
        {gap && <span className="font-normal"> — минимум {rub(gap.min_balance)} {short(gap.min_day)}</span>}
      </p>
    )
  }
  if (data.first_below_threshold) {
    return (
      <p className="text-sm font-medium text-warn" data-testid="money-risk" data-risk="threshold">
        Остаток ниже вашего порога {rub(data.threshold)} с {dayTitle(data.first_below_threshold)}
      </p>
    )
  }
  if (data.min_balance === null) {
    return (
      <p className="text-sm text-muted" data-testid="money-risk" data-risk="unknown">
        Прогноз не посчитать: укажите остатки на счетах.
      </p>
    )
  }
  return (
    <p className="text-sm text-ok" data-testid="money-risk" data-risk="ok">
      Денег хватит на {data.horizon_days} дн. вперёд — минимум {rub(data.min_balance)}
    </p>
  )
}

function OzonMoney({ payouts, hasKey }: { payouts: OzonDashboard['payouts']; hasKey: boolean }) {
  const upcoming = payouts.upcoming.length ? payouts.upcoming : payouts.next_payout ? [payouts.next_payout] : []
  return (
    <div className="space-y-2 p-4" data-testid="money-ozon">
      <div className="flex items-baseline gap-2">
        <h3 className="text-sm font-semibold">Ozon</h3>
        <Link href="/dashboard/finance/payouts" className="ml-auto text-xs text-accent hover:underline">
          Все выплаты →
        </Link>
      </div>
      {!payouts.has_data ? (
        <p className="text-sm text-muted" data-testid="payouts-empty">
          {hasKey
            ? 'Баланс у Ozon ещё не загружен — он загрузится сам в течение нескольких часов.'
            : 'Баланс и даты выплат приходят только из Ozon API — в отчётах XLSX их нет.'}{' '}
          {!hasKey && (
            <Link href="/dashboard/settings/integrations" className="text-accent hover:underline">
              Подключить ключ →
            </Link>
          )}
        </p>
      ) : (
        <>
          <div>
            <div className="text-xs text-muted">На балансе у Ozon</div>
            <div className="text-lg font-bold tabular-nums" data-testid="money-ozon-balance">
              {rub(payouts.balance)}
            </div>
            {payouts.last_day && <div className="text-xs text-faint">на конец {fmtDay(payouts.last_day)}</div>}
          </div>
          <div>
            <div className="text-xs text-muted">Ближайшие выплаты</div>
            {upcoming.length === 0 ? (
              <p className="text-sm text-faint">выплаты нерегулярные — дату не прогнозируем</p>
            ) : (
              <ul className="text-sm" data-testid="money-payouts">
                {upcoming.map((p) => (
                  <li key={p.date} className="flex gap-2 tabular-nums">
                    <span className="text-muted">
                      {fmtDay(p.date).slice(0, 5)}, {weekdayShort(p.date)}
                    </span>
                    <span className="ml-auto font-medium">{p.amount === null ? 'сумма неизвестна' : rub(p.amount)}</span>
                    {p.kind === 'estimate' && <span className="text-xs text-faint">оценка</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function OwnMoney({ data, hasBank }: { data: Overview; hasBank: boolean }) {
  const received = data.series
    .filter((s) => s.kind === 'fact')
    .reduce((sum, s) => sum + Number(s.ozon) + Number(s.income), 0)
  const lines = data.upcoming.filter((l) => l.status !== 'paid')
  const payments = lines.filter((l) => l.amount !== null && Number(l.amount) < 0)
  const total = payments.reduce((sum, l) => sum + Number(l.amount), 0)
  return (
    <div className="space-y-2 p-4" data-testid="money-own">
      <div className="flex items-baseline gap-2">
        <h3 className="text-sm font-semibold">Ваши деньги</h3>
        <Link href="/dashboard/planning" className="ml-auto text-xs text-accent hover:underline">
          Планирование →
        </Link>
      </div>
      <div className="flex flex-wrap items-end gap-x-6 gap-y-1">
        <div>
          <div className="text-xs text-muted">Сейчас на счетах</div>
          <div className={`text-lg font-bold tabular-nums ${data.balance !== null && Number(data.balance) < 0 ? 'text-bad' : ''}`} data-testid="money-balance">
            {data.balance === null ? '—' : rub(data.balance)}
          </div>
        </div>
        {hasBank && (
          <div data-testid="money-received">
            <div className="text-xs text-muted">Пришло за 14 дней</div>
            <div className="font-semibold tabular-nums text-ok">{signed(String(received))}</div>
          </div>
        )}
      </div>
      <Risk data={data} />
      <BalanceChart data={data} />
      <div>
        <div className="flex items-baseline gap-2 text-xs text-muted">
          <span>
            Платежи на 14 дней: {payments.length ? `${payments.length} на ${rub(String(Math.abs(total)))}` : 'нет'}
          </span>
          <MoreLink href="/dashboard/planning/calendar" testid="money-calendar" />
        </div>
        {lines.length > 0 && (
          <ul className="mt-1 space-y-0.5 text-sm" data-testid="money-upcoming">
            {lines.slice(0, 4).map((l) => (
              <li key={l.key} className="flex gap-2">
                <span className={`tabular-nums ${l.status === 'overdue' ? 'text-warn' : 'text-muted'}`}>{fmtDay(l.day).slice(0, 5)}</span>
                <span className="min-w-0 flex-1 truncate">{l.title}</span>
                <span className={`tabular-nums ${l.amount !== null && Number(l.amount) > 0 ? 'text-ok' : ''}`}>
                  {l.amount === null ? '—' : signed(l.amount)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

/** Деньги и ближайшие события: выплаты Ozon всегда; свои деньги, платежи и прогноз — если включено «Планирование». */
export function MoneyBlock({ payouts, hasKey }: { payouts: OzonDashboard['payouts']; hasKey: boolean }) {
  const sync = useSyncStatus()
  const [state, setState] = useState<PlanningState | null>(null)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let alive = true
    api
      .get<PlanningState>('/planning/settings')
      .then(async ({ data }) => {
        if (!alive) return
        setState(data)
        if (data.enabled) {
          const { data: ov } = await api.get<Overview>('/planning/overview')
          if (alive) setOverview(ov)
        }
      })
      .catch(() => alive && setFailed(true))
    return () => {
      alive = false
    }
  }, [])
  return (
    <section className="rounded-xl border border-line bg-card" data-testid="money">
      <h2 className="border-b border-line px-4 py-3 font-semibold">Деньги и ближайшие события</h2>
      <div className="grid divide-y divide-line md:grid-cols-2 md:divide-x md:divide-y-0">
        <OzonMoney payouts={payouts} hasKey={hasKey} />
        {state && !state.enabled ? (
          <div className="p-4 text-sm text-muted" data-testid="money-planning-off">
            Включите «Планирование», чтобы видеть прогноз денег: сколько на счетах, какие платежи впереди и хватит ли на них.{' '}
            <Link href="/dashboard/planning" className="text-accent hover:underline">
              Включить →
            </Link>
          </div>
        ) : overview ? (
          <OwnMoney data={overview} hasBank={sync?.has_bank ?? false} />
        ) : failed ? (
          <p className="p-4 text-sm text-muted">
            Прогноз денег сейчас не загрузился —{' '}
            <Link href="/dashboard/planning" className="text-accent hover:underline">
              откройте «Планирование» →
            </Link>
          </p>
        ) : (
          <p className="p-4 text-sm text-muted">Загружаем…</p>
        )}
      </div>
    </section>
  )
}

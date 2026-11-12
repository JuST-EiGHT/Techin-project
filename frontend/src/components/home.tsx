'use client'
// Главная (шаг 10.4, docs/SITEMAP.md п. 1): ключевые показатели с прошлым периодом, один график динамики и «Деньги и
// ближайшие события». Показатели — основа аналитики (10.3), выплаты Ozon — 3.6, прогноз денег — «Планирование» (3.9–3.10).
import { Landmark, Wallet } from 'lucide-react'
import Link from 'next/link'
import { useEffect, useState } from 'react'
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { GranularitySwitch, MetricChart } from '@/components/analytics'
import { FilterButton, FilterField } from '@/components/filters'
import { type Drill, MoreLink, TipBox, useDrill } from '@/components/charts'
import { Change, FigureLink, Hint, type OzonDashboard, STAT_BOX, StatValue } from '@/components/dashboard'
import { compact, useTokenColors } from '@/components/finance'
import { weekdayShort } from '@/components/payouts'
import { dayTitle, signed } from '@/components/planning/Calendar'
import { type Overview, type PlanningState, opsHref } from '@/components/planning/types'
import { fmtDay, rub } from '@/components/reports'
import { Badge, IconBox, Segmented, Skeleton } from '@/components/ui'
import { type Granularity, METRICS, type MetricKey, type MetricPoint, type Metrics, formatMetric } from '@/lib/analytics'
import { api } from '@/lib/api'
import { useSyncStatus } from '@/lib/sync'

const range = (from: string, to: string) => `period=custom&from=${from}&to=${to}`

/** Куда ведут показатели: выручка, заказы и продажи — «Аналитика продаж» (10.8), прибыль и маржа — «Аналитика
 * прибыльности» (10.9). */
export const METRIC_LINKS: Partial<Record<MetricKey, (from: string, to: string) => string>> = {
  revenue: (from, to) => `/dashboard/analytics/sales?${range(from, to)}`,
  orders: (from, to) => `/dashboard/analytics/sales?${range(from, to)}`,
  ordered_amount: (from, to) => `/dashboard/analytics/sales?${range(from, to)}`,
  sold_qty: (from, to) => `/dashboard/analytics/sales?${range(from, to)}`,
  profit: (from, to) => `/dashboard/analytics/profit?${range(from, to)}`,
  margin: (from, to) => `/dashboard/analytics/profit?${range(from, to)}`,
}

const short = (iso: string) => fmtDay(iso).slice(0, 5)
/** Ссылка показателя с доп. фильтром («Статистика товара» 10.14 — product=…). */
const withQuery = (href: string | null, query?: string) => (href && query ? `${href}&${query}` : href)

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
    <div className={STAT_BOX} data-testid={testid}>
      <div className="text-sm text-muted">{label}</div>
      <StatValue value={value} tone={tone} />
      <div className="mt-1.5 space-y-1">{children}</div>
    </div>
  )
}

/** Выручка, прибыль, маржинальность, заказы: значение, ▲ / ▼ к прошлому периоду, «было …», переход в подробности. */
/** Четыре главные цифры: выручка, прибыль, маржинальность и заказы (главная) или продажи в штуках («Обзор бизнеса»). */
export function KeyFigures({ data, taxSet, costLabel, fourth = 'orders', query }: {
  data: Metrics
  taxSet: boolean
  costLabel: string
  fourth?: 'orders' | 'sold_qty'
  /** Добавить к ссылкам показателей (например, product=… — подробности по одному товару). */
  query?: string
}) {
  const t = data.totals
  const prev = data.previous?.totals ?? null
  const ch = data.changes
  const against = data.previous ? `к ${short(data.previous.date_from)}–${short(data.previous.date_to)}` : ''
  const link = (k: MetricKey) => withQuery(METRIC_LINKS[k]?.(data.date_from, data.date_to) ?? null, query)
  const was = (k: MetricKey) =>
    prev === null ? null : prev[k] === null ? 'в прошлом периоде нет данных' : `было ${formatMetric(prev[k], METRICS[k].format)}`
  const profit = t.profit === null ? null : Number(t.profit)
  const noData = (k: MetricKey) => (t[k] === null ? 'нет данных за период' : null)
  const byProducts = data.filter.product_count !== null // товар или категория — прибыль до налога, как «Прибыль по товарам»
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="key-figures">
      <Card href={link('revenue')} label="Выручка" value={formatMetric(t.revenue, 'rub')} testid="figure-revenue">
        {ch && <Change pct={ch.revenue} against={against} />}
        <Hint>{noData('revenue') ?? was('revenue') ?? `продано ${formatMetric(t.sold_qty, 'qty')}`}</Hint>
        {t.ordered_amount !== null && (
          <Hint>
            <span title="Сумма заказов за период по дню заказа (без отмен). Выручка выше — по доставленным: Ozon начисляет её, когда покупатель получил товар">
              по заказам — <span data-testid="figure-ordered-amount">{formatMetric(t.ordered_amount, 'rub')}</span>
            </span>
          </Hint>
        )}
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
        {/* решение 10.22: главная и аналитика — прибыль от продаж на Ozon; с расходами бизнеса — «Финансы» */}
        {t.profit !== null && !byProducts && <Hint>от продаж на Ozon, без расходов бизнеса</Hint>}
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
              : (was('orders') ??
                `${formatMetric(t.ordered_qty, 'qty')}${t.ordered_amount !== null ? ` на ${formatMetric(t.ordered_amount, 'rub')}` : ' в заказах'}`)}
          </Hint>
        </Card>
      )}
    </div>
  )
}

const CHART_METRICS: { key: MetricKey; label: string }[] = [
  { key: 'revenue', label: 'Выручка' },
  { key: 'ordered_amount', label: 'Заказы, ₽' },
  { key: 'profit', label: 'Прибыль' },
  { key: 'orders', label: 'Заказы' },
  { key: 'sold_qty', label: 'Продажи, шт.' },
]

/** Один график с переключением показателя; точка и «Подробнее →» ведут туда же, куда карточка показателя.
 * metrics — какие показатели на выбор («Финансы» — только выручка и прибыль); granularity — детализация над графиком. */
export function DynamicsChart({
  data,
  query,
  metrics,
  granularity,
}: {
  data: Metrics
  query?: string
  metrics?: MetricKey[]
  granularity?: { value: Granularity; chosen?: boolean; onChange: (g: Granularity) => void }
}) {
  const [metric, setMetric] = useState<MetricKey>('revenue')
  const choices = metrics ? CHART_METRICS.filter((m) => metrics.includes(m.key)) : CHART_METRICS
  const base = METRIC_LINKS[metric]
  const link = base ? (from: string, to: string) => withQuery(base(from, to), query) as string : undefined
  return (
    <div data-testid="dynamics">
      <MetricChart
        data={data}
        metric={metric}
        title={`${METRICS[metric].label}${
          metric === 'orders' || metric === 'ordered_amount'
            ? ' — оформлены покупателями'
            : metric === 'sold_qty' || metric === 'revenue'
              ? ' — по доставленным (начисления Ozon)'
              : ''
        }`}
        pointHref={link ? (p: MetricPoint) => link(p.date_from, p.date_to) : undefined}
        moreHref={link ? link(data.date_from, data.date_to) : undefined}
        toolbar={
          choices.length > 1 && (
            <Segmented
              label="Показатель на графике"
              value={metric}
              onChange={setMetric}
              options={choices.map((m) => ({ value: m.key, label: m.label }))}
              optionTestid={(k) => `chart-metric-${k}`}
            />
          )
        }
        action={
          granularity && (
            // правка 11.11: фильтры графика — за кнопкой «Фильтры» в его углу
            <FilterButton count={granularity.chosen ? 1 : 0} testid="chart-filters">
              <FilterField label="Детализация">
                <GranularitySwitch value={granularity.value} onChange={granularity.onChange} />
              </FilterField>
            </FilterButton>
          )
        }
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
    <div className={`h-36 ${drill.boxProps.className}`} onPointerDown={drill.boxProps.onPointerDown} data-testid="money-chart">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 8, right: 4, bottom: 0, left: 0 }} onClick={drill.onClick}>
          <CartesianGrid vertical={false} stroke={colors.line} />
          <XAxis dataKey="label" tick={{ fill: colors.faint, fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={16} />
          <YAxis tick={{ fill: colors.faint, fontSize: 10 }} tickFormatter={compact} tickLine={false} axisLine={false} width={78} />
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
  const box = 'rounded-lg px-3 py-2 text-sm'
  if (data.first_below_zero) {
    const gap = data.gaps.find((g) => g.below_zero)
    return (
      <p className={`${box} bg-bad/10 font-medium text-bad`} data-testid="money-risk" data-risk="gap">
        Кассовый разрыв {dayTitle(data.first_below_zero)}
        {gap && <span className="font-normal"> — минимум {rub(gap.min_balance)} {short(gap.min_day)}</span>}
      </p>
    )
  }
  if (data.first_below_threshold) {
    return (
      <p className={`${box} bg-warn/10 font-medium text-warn`} data-testid="money-risk" data-risk="threshold">
        Остаток ниже вашего порога {rub(data.threshold)} с {dayTitle(data.first_below_threshold)}
      </p>
    )
  }
  if (data.min_balance === null) {
    return (
      <p className={`${box} bg-soft text-muted`} data-testid="money-risk" data-risk="unknown">
        Прогноз не посчитать: укажите остатки на счетах.
      </p>
    )
  }
  return (
    <p className={`${box} bg-ok/10 text-ok`} data-testid="money-risk" data-risk="ok">
      Денег хватит на {data.horizon_days} дн. вперёд — минимум {rub(data.min_balance)}
    </p>
  )
}

/** Заголовок половины блока: значок, название, ссылка «… →». */
function MoneyHead({ icon, title, href, link }: { icon: React.ComponentType<{ className?: string }>; title: string; href: string; link: string }) {
  return (
    <div className="flex items-center gap-2.5">
      <IconBox icon={icon} />
      <h3 className="font-semibold">{title}</h3>
      <Link href={href} className="ml-auto text-sm text-accent hover:underline">
        {link}
      </Link>
    </div>
  )
}

function OzonMoney({ payouts, hasKey }: { payouts: OzonDashboard['payouts']; hasKey: boolean }) {
  const upcoming = payouts.upcoming.length ? payouts.upcoming : payouts.next_payout ? [payouts.next_payout] : []
  return (
    <div className="space-y-4 p-5" data-testid="money-ozon">
      <MoneyHead icon={Wallet} title="Ozon" href="/dashboard/finance/payouts" link="Все выплаты →" />
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
            <div className="text-sm text-muted">На балансе у Ozon</div>
            <div className="mt-1 text-2xl font-semibold tracking-tight tabular-nums" data-testid="money-ozon-balance">
              {rub(payouts.balance)}
            </div>
            {payouts.last_day && <div className="mt-0.5 text-xs text-faint">на конец {fmtDay(payouts.last_day)}</div>}
          </div>
          <div>
            <div className="mb-1.5 text-sm text-muted">Ближайшие выплаты</div>
            {upcoming.length === 0 ? (
              <p className="text-sm text-faint">выплаты нерегулярные — дату не прогнозируем</p>
            ) : (
              <ul className="divide-y divide-line rounded-lg border border-line text-sm" data-testid="money-payouts">
                {upcoming.map((p) => (
                  <li key={p.date} className="flex items-center gap-2 px-3 py-2 tabular-nums">
                    <span className="text-muted">
                      {fmtDay(p.date).slice(0, 5)}, {weekdayShort(p.date)}
                    </span>
                    {p.kind === 'estimate' && <Badge tone="muted">оценка</Badge>}
                    <span className="ml-auto font-medium">{p.amount === null ? 'сумма неизвестна' : rub(p.amount)}</span>
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
    <div className="space-y-4 p-5" data-testid="money-own">
      <MoneyHead icon={Landmark} title="Ваши деньги" href="/dashboard/planning" link="Планирование →" />
      <div className="flex flex-wrap items-end gap-x-8 gap-y-2">
        <div>
          <div className="text-sm text-muted">Сейчас на счетах</div>
          <div className={`mt-1 text-2xl font-semibold tracking-tight tabular-nums ${data.balance !== null && Number(data.balance) < 0 ? 'text-bad' : ''}`} data-testid="money-balance">
            {data.balance === null ? '—' : rub(data.balance)}
          </div>
        </div>
        {hasBank && (
          <div data-testid="money-received">
            <div className="text-sm text-muted">Пришло за 14 дней</div>
            <div className="mt-1 font-semibold tabular-nums text-ok">{signed(String(received))}</div>
          </div>
        )}
      </div>
      <Risk data={data} />
      <BalanceChart data={data} />
      <div>
        <div className="mb-1.5 flex items-baseline gap-2 text-sm text-muted">
          <span>
            Платежи на 14 дней: {payments.length ? `${payments.length} на ${rub(String(Math.abs(total)))}` : 'нет'}
          </span>
          <MoreLink href="/dashboard/planning/calendar" testid="money-calendar" />
        </div>
        {lines.length > 0 && (
          <ul className="divide-y divide-line rounded-lg border border-line text-sm" data-testid="money-upcoming">
            {lines.slice(0, 4).map((l) => (
              <li key={l.key} className="flex items-center gap-2 px-3 py-2">
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
    <section className="rounded-xl border border-line bg-card shadow-card" data-testid="money">
      <div className="px-5 pt-4">
        <h2 className="text-base font-semibold">Деньги и ближайшие события</h2>
        <p className="mt-0.5 text-sm text-muted">Баланс и выплаты Ozon, а с «Планированием» — счета, платежи и прогноз на 14 дней</p>
      </div>
      <div className="mt-2 grid divide-y divide-line md:grid-cols-2 md:divide-x md:divide-y-0">
        <OzonMoney payouts={payouts} hasKey={hasKey} />
        {state && !state.enabled ? (
          <div className="p-5" data-testid="money-planning-off">
            <MoneyHead icon={Landmark} title="Ваши деньги" href="/dashboard/planning" link="Включить →" />
            <p className="mt-3 text-sm text-muted">
              Включите «Планирование», чтобы видеть прогноз денег: сколько на счетах, какие платежи впереди и хватит ли на них.
            </p>
          </div>
        ) : overview ? (
          <OwnMoney data={overview} hasBank={sync?.has_bank ?? false} />
        ) : failed ? (
          <p className="p-5 text-sm text-muted">
            Прогноз денег сейчас не загрузился —{' '}
            <Link href="/dashboard/planning" className="text-accent hover:underline">
              откройте «Планирование» →
            </Link>
          </p>
        ) : (
          <div className="space-y-3 p-5">
            <Skeleton className="h-8 w-1/2" />
            <Skeleton className="h-24" />
          </div>
        )}
      </div>
    </section>
  )
}

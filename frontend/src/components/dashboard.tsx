'use client'
// Главная — дашборд Ozon (шаг 3.7): цифры со сравнением и график выручки рядом с прошлым периодом.
import Link from 'next/link'
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { type Coverage, type DataSource, compact, useTokenColors } from '@/components/finance'
import type { ForecastItem, WeekBar } from '@/components/payouts'
import { fmtDay, rub } from '@/components/reports'
import { daysWord } from '@/lib/period'

export type Figure = { value: string | null; previous: string | null; change_pct: string | null }

export type OzonDashboard = {
  date_from: string
  date_to: string
  prev_from: string
  prev_to: string
  setup: { has_key: boolean; has_reports: boolean; has_balance: boolean; tax_set: boolean }
  data_source: DataSource
  coverage: Coverage
  prev_data_source: DataSource
  missing_cost_count: number
  figures: {
    revenue: Figure
    received: Figure
    profit: Figure & { kind: 'net' | 'before_tax' }
    margin: { value: string | null; previous: string | null; change_pp: string | null }
  }
  compare_note: string | null
  series: {
    granularity: 'day' | 'week'
    points: { date: string; date_to: string; prev_date: string | null; revenue: string | null; prev_revenue: string | null; partial: boolean }[]
  }
  payouts: { has_data: boolean; balance: string | null; last_day: string | null; next_payout: ForecastItem | null; schedule_title: string; weeks: WeekBar[] }
}

const num = (v: string) => Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 1 })
const short = (iso: string) => fmtDay(iso).slice(0, 5)

/** «▲ 12,5 %» — рост выручки и прибыли хорош (зелёный), падение — красный. */
export function Change({ pct, against, unit = '%' }: { pct: string | null; against: string; unit?: '%' | 'п.п.' }) {
  if (pct === null) return null
  const v = Number(pct)
  const tone = v > 0 ? 'text-ok' : v < 0 ? 'text-bad' : 'text-muted'
  return (
    <span className={`text-xs font-medium ${tone}`} data-testid="change">
      {v > 0 ? '▲' : v < 0 ? '▼' : '='} {num(String(Math.abs(v)))} {unit} <span className="font-normal text-faint">{against}</span>
    </span>
  )
}

/** Цифра-ссылка: ведёт на свою страницу. */
export function FigureLink({
  href,
  label,
  value,
  tone,
  children,
  testid,
  highlight,
}: {
  href: string
  label: string
  value: string
  tone?: 'ok' | 'bad'
  children?: React.ReactNode
  testid: string
  highlight?: string
}) {
  return (
    <Link href={href} className="group block bg-card hover:bg-accent/5" data-testid={testid}>
      <div className={`h-full px-4 py-3 ${highlight ?? ''}`}>
        <div className="flex items-center justify-between text-[11px] uppercase tracking-wider text-muted">
          <span>{label}</span>
          <span className="text-faint opacity-0 transition group-hover:opacity-100" aria-hidden>
            →
          </span>
        </div>
        <div className={`mt-1 text-lg font-bold tabular-nums ${tone === 'ok' ? 'text-ok' : tone === 'bad' ? 'text-bad' : ''}`}>{value}</div>
        <div className="mt-0.5 space-y-0.5">{children}</div>
      </div>
    </Link>
  )
}

export const Hint = ({ children, tone }: { children: React.ReactNode; tone?: 'warn' }) => (
  <div className={`text-xs ${tone === 'warn' ? 'text-warn' : 'text-faint'}`}>{children}</div>
)

// ---------- выручка рядом с прошлым периодом ----------

type Row = { label: string; revenue: number | null; prev: number | null; point: OzonDashboard['series']['points'][number] }

function RevenueTooltip({ active, payload, week }: { active?: boolean; payload?: { payload: Row }[]; week: boolean }) {
  if (!active || !payload?.length) return null
  const { point } = payload[0].payload
  const span = (from: string, to: string) => (week ? `${short(from)}–${short(to)}` : fmtDay(from))
  return (
    <div className="rounded-lg border border-line bg-card px-3 py-2 text-xs shadow-lg">
      <div className="font-semibold">{span(point.date, point.date_to)}</div>
      <div className="tabular-nums text-accent">Выручка: {point.revenue === null ? 'нет данных' : rub(point.revenue)}</div>
      {point.prev_date && (
        <div className="mt-1 tabular-nums text-muted">
          {week ? `с ${short(point.prev_date)}` : fmtDay(point.prev_date)}: {point.prev_revenue === null ? 'нет данных' : rub(point.prev_revenue)}
        </div>
      )}
      {point.partial && <div className="mt-1 text-faint">есть дни без данных</div>}
    </div>
  )
}

export function RevenueChart({ series, against }: { series: OzonDashboard['series']; against: string }) {
  const colors = useTokenColors()
  if (!colors) return null
  const week = series.granularity === 'week'
  const rows: Row[] = series.points.map((p) => ({
    label: short(p.date),
    revenue: p.revenue === null ? null : Number(p.revenue),
    prev: p.prev_revenue === null ? null : Number(p.prev_revenue),
    point: p,
  }))
  const missing = series.points.filter((p) => p.revenue === null).length
  return (
    <section className="rounded-xl border border-line bg-card p-4" data-testid="revenue-chart">
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1">
        <h2 className="text-sm font-semibold">Выручка {week ? 'по неделям' : 'по дням'}</h2>
        <span className="flex items-center gap-1.5 text-xs text-muted">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-accent" /> этот период
        </span>
        <span className="flex items-center gap-1.5 text-xs text-muted">
          <span className="inline-block h-0 w-3 border-t-2 border-dashed border-muted" /> {against.replace(/^к /, '')}
        </span>
        {missing > 0 && (
          <span className="text-xs text-faint">
            без данных — {week ? `${missing} нед.` : daysWord(missing)} (пропуски, а не ноль)
          </span>
        )}
      </div>
      <div className="h-60">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} stroke={colors.line} />
            <XAxis dataKey="label" tick={{ fill: colors.muted, fontSize: 11 }} tickLine={false} axisLine={{ stroke: colors.line }} minTickGap={8} />
            <YAxis tick={{ fill: colors.muted, fontSize: 11 }} tickFormatter={compact} tickLine={false} axisLine={false} width={64} />
            <Tooltip content={<RevenueTooltip week={week} />} cursor={{ fill: colors.line, opacity: 0.4 }} />
            <Bar dataKey="revenue" fill={colors.accent} radius={[3, 3, 0, 0]} isAnimationActive={false} />
            <Line
              dataKey="prev"
              stroke={colors.muted}
              strokeDasharray="4 3"
              strokeWidth={2}
              dot={false}
              connectNulls={false}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}

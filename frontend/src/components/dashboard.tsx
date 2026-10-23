'use client'
// Главная (шаги 3.7, 10.4): ответ /dashboard/ozon и общие части карточек показателей. График — MetricChart (10.3).
import Link from 'next/link'

import { type Coverage, type DataSource } from '@/components/finance'
import type { ForecastItem, WeekBar } from '@/components/payouts'

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
  payouts: { has_data: boolean; balance: string | null; last_day: string | null; next_payout: ForecastItem | null; upcoming: ForecastItem[]; schedule_title: string; weeks: WeekBar[] }
}

const num = (v: string) => Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 1 })

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

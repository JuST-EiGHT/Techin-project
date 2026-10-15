'use client'
// «Планирование → ДДС» (шаг 3.10): движение денег по статьям и периодам — остаток на начало, поступления, платежи,
// остаток на конец. ДДС — по деньгам; P&L — по начислениям (раздел «Финансы Ozon»): не смешиваем. Переводы между своими
// счетами — не приход и не расход.
import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { FormError } from '@/components/AuthForm'
import { type Drill, MoreLink, TipBox, useDrill } from '@/components/charts'
import { compact, useTokenColors } from '@/components/finance'
import { input } from '@/components/planning/Operations'
import { type CashFlow, type CashFlowPeriod, opsHref } from '@/components/planning/types'
import { fmtDay, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { addDays, todayMsk } from '@/lib/period'

type Preset = 'month' | 'quarter' | 'half' | 'year' | 'custom'
const PRESETS: { value: Preset; label: string }[] = [
  { value: 'month', label: 'Этот месяц' },
  { value: 'quarter', label: '3 месяца' },
  { value: 'half', label: 'Полгода' },
  { value: 'year', label: 'Год' },
  { value: 'custom', label: 'Свой период' },
]
const MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']

/** Первое число месяца на n месяцев раньше. */
function monthsBack(today: string, n: number): string {
  const [y, m] = today.split('-').map(Number)
  const index = y * 12 + (m - 1) - n
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}-01`
}

function presetRange(p: Exclude<Preset, 'custom'>, today: string) {
  const back = { month: 0, quarter: 2, half: 5, year: 11 }[p]
  return { from: monthsBack(today, back), to: today }
}

const num = (v: string | null) => (v === null ? '—' : Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 2 }))

function periodTitle(p: CashFlowPeriod, granularity: CashFlow['granularity']): string {
  const [y, m, d] = p.date_from.split('-').map(Number)
  if (granularity === 'month') {
    const whole = d === 1 && addDays(p.date_to, 1).endsWith('-01')
    return whole ? `${MONTHS[m - 1]} ${y}` : `${fmtDay(p.date_from).slice(0, 5)}–${fmtDay(p.date_to).slice(0, 5)}`
  }
  return `${fmtDay(p.date_from).slice(0, 5)}–${fmtDay(p.date_to).slice(0, 5)}`
}

function Figure({ label, value, tone, hint }: { label: string; value: string; tone?: string; hint?: string }) {
  return (
    <div className="min-w-[9rem] flex-1 bg-card px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`mt-1 text-lg font-bold tabular-nums ${tone ?? ''}`}>{value}</div>
      {hint && <div className="text-xs text-faint">{hint}</div>}
    </div>
  )
}

type ChartRow = { label: string; income: number; expense: number; period: CashFlowPeriod }

function Tip({ active, payload, drill }: { active?: boolean; payload?: { payload: ChartRow }[]; drill: Drill<ChartRow> }) {
  if (!active || !payload?.length) return null
  const r = payload[0].payload
  return (
    <TipBox href={drill.hrefFor(r)} touch={drill.touch}>
      <div className="font-medium">
        {fmtDay(r.period.date_from)}–{fmtDay(r.period.date_to)}
      </div>
      <div className="tabular-nums text-ok">Пришло: {rub(r.period.income)}</div>
      <div className="tabular-nums text-bad">Ушло: {rub(r.period.expense)}</div>
      <div className="tabular-nums">Итого: {rub(r.period.net)}</div>
      {r.period.closing !== null && <div className="tabular-nums text-muted">Остаток на конец: {rub(r.period.closing)}</div>}
    </TipBox>
  )
}

function FlowChart({ data }: { data: CashFlow }) {
  const colors = useTokenColors()
  const drill = useDrill<ChartRow>((r) => opsHref(r.period.date_from, r.period.date_to))
  if (!colors) return null
  const rows: ChartRow[] = data.periods.map((p) => ({ label: periodTitle(p, data.granularity), income: Number(p.income), expense: -Number(p.expense), period: p }))
  return (
    <section className="rounded-xl border border-line bg-card p-4" data-testid="cf-chart">
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
        <h2 className="text-sm font-semibold text-fg">Пришло и ушло {data.granularity === 'week' ? 'по неделям' : 'по месяцам'}</h2>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-ok" /> пришло
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-bad" /> ушло
        </span>
        <MoreLink href={opsHref(data.date_from, data.date_to)} />
      </div>
      <div className={`h-56 ${drill.boxProps.className}`} onPointerDown={drill.boxProps.onPointerDown}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: 0 }} onClick={drill.onClick}>
            <CartesianGrid vertical={false} stroke={colors.line} />
            <XAxis dataKey="label" tick={{ fill: colors.muted, fontSize: 11 }} tickLine={false} axisLine={{ stroke: colors.line }} minTickGap={8} />
            <YAxis tick={{ fill: colors.muted, fontSize: 11 }} tickFormatter={compact} tickLine={false} axisLine={false} width={64} />
            <Tooltip content={<Tip drill={drill} />} cursor={{ fill: colors.line, fillOpacity: 0.4 }} {...drill.tooltipProps} />
            <Bar dataKey="income" fill={colors.ok} radius={[3, 3, 0, 0]} maxBarSize={36} isAnimationActive={false} />
            <Bar dataKey="expense" fill={colors.bad} radius={[3, 3, 0, 0]} maxBarSize={36} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}

function Table({ data }: { data: CashFlow }) {
  const income = data.rows.filter((r) => r.section === 'income')
  const expense = data.rows.filter((r) => r.section === 'expense')
  const cell = 'whitespace-nowrap px-3 py-1.5 text-right tabular-nums'
  const first = 'sticky left-0 z-10 bg-card px-3 py-1.5 text-left'
  const total = (label: string, values: (string | null)[], all: string | null, tone = '', testid?: string) => (
    <tr className="border-t border-line font-semibold" data-testid={testid}>
      <td className={first}>{label}</td>
      {values.map((v, i) => (
        <td key={i} className={`${cell} ${tone}`}>
          {num(v)}
        </td>
      ))}
      <td className={`${cell} ${tone}`}>{num(all)}</td>
    </tr>
  )
  const section = (title: string, rows: CashFlow['rows']) => (
    <>
      <tr className="border-t border-line bg-bg">
        <td className={`${first} !bg-bg text-xs uppercase tracking-wider text-muted`}>
          {title}
        </td>
        <td colSpan={data.periods.length + 1} />
      </tr>
      {rows.map((r) => (
        <tr key={`${r.section}-${r.category_id ?? 'none'}`} className="border-t border-line" data-testid="cf-row">
          <td className={first}>
            <Link href={opsHref(data.date_from, data.date_to, r.category_id)} className="hover:text-accent hover:underline">
              {r.name}
            </Link>
          </td>
          {r.values.map((v, i) => (
            <td key={i} className={`${cell} ${Number(v) === 0 ? 'text-faint' : ''}`}>
              {Number(v) === 0 ? '—' : num(v)}
            </td>
          ))}
          <td className={`${cell} font-medium`}>{num(r.total)}</td>
        </tr>
      ))}
    </>
  )
  return (
    <section className="overflow-x-auto rounded-xl border border-line bg-card" data-testid="cf-table">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="text-xs text-muted">
            <th className={`${first} font-normal`}>Статья, ₽</th>
            {data.periods.map((p) => (
              <th key={p.date_from} className={`${cell} font-normal`}>
                {periodTitle(p, data.granularity)}
              </th>
            ))}
            <th className={`${cell} font-normal`}>Итого</th>
          </tr>
        </thead>
        <tbody>
          {total('Остаток на начало', data.periods.map((p) => p.opening), data.opening, 'text-muted')}
          {section('Поступления', income)}
          {total('Итого поступления', data.periods.map((p) => p.income), data.income, 'text-ok', 'cf-income')}
          {section('Платежи', expense)}
          {total('Итого платежи', data.periods.map((p) => p.expense), data.expense, 'text-bad', 'cf-expense')}
          {total('Чистый поток', data.periods.map((p) => p.net), data.net)}
          {total('Остаток на конец', data.periods.map((p) => p.closing), data.closing, '', 'cf-closing')}
        </tbody>
      </table>
    </section>
  )
}

export function CashFlowTab() {
  const today = todayMsk()
  const [preset, setPreset] = useState<Preset>('quarter')
  const [range, setRange] = useState(() => presetRange('quarter', today))
  const [granularity, setGranularity] = useState<CashFlow['granularity']>('month')
  const [data, setData] = useState<CashFlow | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const { data: body } = await api.get<CashFlow>('/planning/cash-flow', { params: { date_from: range.from, date_to: range.to, granularity } })
      setData(body)
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [range, granularity])

  useEffect(() => {
    load()
  }, [load])

  function choose(p: Preset) {
    setPreset(p)
    if (p !== 'custom') setRange(presetRange(p, today))
  }

  const toggle = (active: boolean) => `rounded-md px-3 py-1 text-sm ${active ? 'bg-accent text-white' : 'text-muted hover:text-fg'}`
  return (
    <div className="space-y-4" data-testid="cashflow-tab">
      <p className="text-sm text-muted">
        ДДС — движение денег: что пришло на ваши счета и что ушло, по статьям. Это не P&amp;L: прибыль по начислениям Ozon — в{' '}
        <Link href="/dashboard/finance" className="text-accent hover:underline">
          «Финансах»
        </Link>
        . Выплата Ozon здесь — в день, когда пришли деньги, а в P&amp;L — в неделю продаж. Переводы между своими счетами — не приход и не расход.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1 rounded-lg border border-line bg-card p-1" role="group" aria-label="Период">
          {PRESETS.map((p) => (
            <button key={p.value} type="button" aria-pressed={preset === p.value} onClick={() => choose(p.value)} className={toggle(preset === p.value)} data-testid={`cf-preset-${p.value}`}>
              {p.label}
            </button>
          ))}
        </div>
        <div className="flex gap-1 rounded-lg border border-line bg-card p-1" role="group" aria-label="По неделям или месяцам">
          <button type="button" aria-pressed={granularity === 'week'} onClick={() => setGranularity('week')} className={toggle(granularity === 'week')} data-testid="cf-week">
            По неделям
          </button>
          <button type="button" aria-pressed={granularity === 'month'} onClick={() => setGranularity('month')} className={toggle(granularity === 'month')} data-testid="cf-month">
            По месяцам
          </button>
        </div>
      </div>
      {preset === 'custom' && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <input type="date" value={range.from} max={range.to} onChange={(e) => e.target.value && setRange({ ...range, from: e.target.value })} className={`${input} w-40`} aria-label="С" data-testid="cf-from" />
          <span className="text-muted">—</span>
          <input type="date" value={range.to} min={range.from} onChange={(e) => e.target.value && setRange({ ...range, to: e.target.value })} className={`${input} w-40`} aria-label="По" data-testid="cf-to" />
        </div>
      )}
      <FormError message={error} />
      {!data && !error && <p className="text-sm text-muted">Загрузка…</p>}
      {data && (
        <>
          <div className="flex flex-wrap gap-px overflow-hidden rounded-xl border border-line bg-line" data-testid="cf-summary">
            <Figure label={`На ${fmtDay(data.date_from)}`} value={data.opening === null ? '—' : rub(data.opening)} hint={data.opening === null ? 'остаток неизвестен' : 'остаток на начало'} />
            <Figure label="Пришло" value={rub(data.income)} tone="text-ok" />
            <Figure label="Ушло" value={rub(data.expense)} tone="text-bad" />
            <Figure label={`На ${fmtDay(data.date_to)}`} value={data.closing === null ? '—' : rub(data.closing)} hint={data.closing === null ? 'остаток неизвестен' : 'остаток на конец'} />
          </div>
          {data.warnings.length > 0 && (
            <ul className="space-y-1 text-sm text-warn" data-testid="cf-warnings">
              {data.warnings.map((w) => (
                <li key={w}>• {w}</li>
              ))}
            </ul>
          )}
          {data.has_data ? (
            <>
              <FlowChart data={data} />
              <Table data={data} />
            </>
          ) : (
            <p className="rounded-xl border border-line bg-card p-4 text-sm text-muted" data-testid="cf-empty">
              За этот период операций нет. Добавьте их во вкладке{' '}
              <Link href="/dashboard/planning?tab=operations" className="text-accent hover:underline">
                «Операции»
              </Link>{' '}
              — вручную или списком из Excel; выплаты Ozon появятся сами, если подключён ключ Ozon API.
            </p>
          )}
        </>
      )}
    </div>
  )
}

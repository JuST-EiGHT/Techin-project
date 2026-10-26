'use client'
// «Планирование → ДДС» (шаг 3.10): движение денег по статьям и периодам — остаток на начало, поступления, платежи,
// остаток на конец. ДДС — по деньгам; P&L — по начислениям (раздел «Финансы Ozon»): не смешиваем. Переводы между своими
// счетами — не приход и не расход.
// Шаг 10.24: план календаря — оставшиеся платежи с сегодня («ждём») дают прогнозный остаток на конец периодов, план
// периода — план-факт по статьям; один счёт (переводы — его приход и расход), только поступления или платежи, график
// остатка, кассовый разрыв с днём, нехваткой и причинами; сумма в ячейке ведёт к операциям.
import { ChevronRight } from 'lucide-react'
import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { Bar, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { FormError } from '@/components/AuthForm'
import { type Drill, MoreLink, TipBox, useDrill } from '@/components/charts'
import { compact, useTokenColors } from '@/components/finance'
import { GapCausesList } from '@/components/planning/Calendar'
import { input } from '@/components/planning/Operations'
import { type Account, type CashFlow, type CashFlowPeriod, type CashFlowRow, opsHref } from '@/components/planning/types'
import { fmtDay, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { addDays, todayMsk } from '@/lib/period'

type Preset = 'month' | 'quarter' | 'half' | 'year' | 'ahead' | 'custom'
const PRESETS: { value: Preset; label: string }[] = [
  { value: 'month', label: 'Этот месяц' },
  { value: 'quarter', label: '3 месяца' },
  { value: 'half', label: 'Полгода' },
  { value: 'year', label: 'Год' },
  { value: 'ahead', label: '3 месяца вперёд' },
  { value: 'custom', label: 'Свой период' },
]
type Side = 'all' | 'income' | 'expense'
type Mode = 'forecast' | 'planfact'
const MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']

/** Первое число месяца со сдвигом на shift месяцев (−2 — два месяца назад). */
function monthStart(today: string, shift: number): string {
  const [y, m] = today.split('-').map(Number)
  const index = y * 12 + (m - 1) + shift
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}-01`
}
const monthEnd = (today: string, shift: number) => addDays(monthStart(today, shift + 1), -1)

/** Периоды — до конца текущего месяца: оставшиеся платежи дают прогноз остатка на конец; «вперёд» — и два следующих. */
function presetRange(p: Exclude<Preset, 'custom'>, today: string) {
  if (p === 'ahead') return { from: monthStart(today, 0), to: monthEnd(today, 2) }
  const back = { month: 0, quarter: 2, half: 5, year: 11 }[p]
  return { from: monthStart(today, -back), to: monthEnd(today, 0) }
}

const num = (v: string | number | null) => (v === null ? '—' : Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 2 }))
const sum = (...v: string[]) => String(v.reduce((a, x) => a + Number(x), 0))

function periodTitle(p: CashFlowPeriod, granularity: CashFlow['granularity']): string {
  const [y, m, d] = p.date_from.split('-').map(Number)
  if (granularity === 'month') {
    const whole = d === 1 && addDays(p.date_to, 1).endsWith('-01')
    return whole ? `${MONTHS[m - 1]} ${y}` : `${fmtDay(p.date_from).slice(0, 5)}–${fmtDay(p.date_to).slice(0, 5)}`
  }
  return `${fmtDay(p.date_from).slice(0, 5)}–${fmtDay(p.date_to).slice(0, 5)}`
}

function Figure({ label, value, tone, hint, testid }: { label: string; value: string; tone?: string; hint?: string; testid?: string }) {
  return (
    <div className="min-w-[9rem] flex-1 bg-card px-4 py-3" data-testid={testid}>
      <div className="text-[11px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`mt-1 text-lg font-bold tabular-nums ${tone ?? ''}`}>{value}</div>
      {hint && <div className="text-xs text-faint">{hint}</div>}
    </div>
  )
}

// ---------- график: пришло и ушло, остаток на конец ----------

type ChartRow = { label: string; income: number; incomeAhead: number; expense: number; expenseAhead: number; closing: number | null; period: CashFlowPeriod }

function Tip({ active, payload, drill }: { active?: boolean; payload?: { payload: ChartRow }[]; drill: Drill<ChartRow> }) {
  if (!active || !payload?.length) return null
  const r = payload[0].payload
  const p = r.period
  return (
    <TipBox href={drill.hrefFor(r)} touch={drill.touch}>
      <div className="font-medium">
        {fmtDay(p.date_from)}–{fmtDay(p.date_to)}
        {p.kind === 'future' ? ' · прогноз' : p.kind === 'current' ? ' · сейчас' : ''}
      </div>
      <div className="tabular-nums text-ok">
        Пришло: {rub(p.income)}
        {Number(p.forecast_income) !== 0 && <span className="text-muted"> · ждём {rub(p.forecast_income)}</span>}
      </div>
      <div className="tabular-nums text-bad">
        Ушло: {rub(p.expense)}
        {Number(p.forecast_expense) !== 0 && <span className="text-muted"> · ждём {rub(p.forecast_expense)}</span>}
      </div>
      {p.closing !== null && (
        <div className={`tabular-nums ${Number(p.closing) < 0 ? 'text-bad' : ''}`}>
          Остаток на конец: {rub(p.closing)}
          {(Number(p.forecast_income) !== 0 || Number(p.forecast_expense) !== 0) && ' (прогноз)'}
        </div>
      )}
    </TipBox>
  )
}

function FlowChart({ data, side }: { data: CashFlow; side: Side }) {
  const colors = useTokenColors()
  const drill = useDrill<ChartRow>((r) => opsHref(r.period.date_from, r.period.date_to, null, data.account_id))
  if (!colors) return null
  const rows: ChartRow[] = data.periods.map((p) => ({
    label: periodTitle(p, data.granularity),
    income: Number(p.income),
    incomeAhead: Number(p.forecast_income),
    expense: -Number(p.expense),
    expenseAhead: -Number(p.forecast_expense),
    closing: p.closing === null ? null : Number(p.closing),
    period: p,
  }))
  const ahead = data.has_plan && (Number(data.forecast_income) !== 0 || Number(data.forecast_expense) !== 0)
  return (
    <section className="rounded-xl border border-line bg-card p-4" data-testid="cf-chart">
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
        <h2 className="text-sm font-semibold text-fg">Пришло, ушло и остаток {data.granularity === 'week' ? 'по неделям' : 'по месяцам'}</h2>
        {side !== 'expense' && (
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-sm bg-ok" /> пришло
          </span>
        )}
        {side !== 'income' && (
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-sm bg-bad" /> ушло
          </span>
        )}
        {ahead && (
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-sm bg-faint opacity-60" /> ждём по плану
          </span>
        )}
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0 w-3 border-t-2 border-fg" /> остаток на конец
        </span>
        <MoreLink href={opsHref(data.date_from, data.date_to, null, data.account_id)} />
      </div>
      <div className={`h-60 ${drill.boxProps.className}`} onPointerDown={drill.boxProps.onPointerDown}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: 0 }} onClick={drill.onClick}>
            <CartesianGrid vertical={false} stroke={colors.line} />
            <XAxis dataKey="label" tick={{ fill: colors.muted, fontSize: 11 }} tickLine={false} axisLine={{ stroke: colors.line }} minTickGap={8} />
            <YAxis tick={{ fill: colors.muted, fontSize: 11 }} tickFormatter={compact} tickLine={false} axisLine={false} width={64} />
            <ReferenceLine y={0} stroke={colors.bad} strokeDasharray="4 3" />
            <Tooltip content={<Tip drill={drill} />} cursor={{ fill: colors.line, fillOpacity: 0.4 }} {...drill.tooltipProps} />
            {side !== 'expense' && <Bar dataKey="income" stackId="in" fill={colors.ok} maxBarSize={36} isAnimationActive={false} />}
            {side !== 'expense' && <Bar dataKey="incomeAhead" stackId="in" fill={colors.ok} fillOpacity={0.35} radius={[3, 3, 0, 0]} maxBarSize={36} isAnimationActive={false} />}
            {side !== 'income' && <Bar dataKey="expense" stackId="out" fill={colors.bad} maxBarSize={36} isAnimationActive={false} />}
            {side !== 'income' && <Bar dataKey="expenseAhead" stackId="out" fill={colors.bad} fillOpacity={0.35} radius={[3, 3, 0, 0]} maxBarSize={36} isAnimationActive={false} />}
            <Line type="monotone" dataKey="closing" stroke={colors.fg} strokeWidth={2} dot={{ r: 3, fill: colors.fg }} connectNulls={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}

// ---------- таблица по статьям ----------

/** Сумма в ячейке: факт (ссылкой на операции), под ним — «ждём» (прогноз) или «план» (план-факт). */
function Amount({ fact, ahead, plan, mode, section, href }: { fact: string; ahead: string; plan: string; mode: Mode; section: 'income' | 'expense' | null; href: string | null }) {
  const main = Number(fact) === 0 ? <span className="text-faint">—</span> : href ? (
    <Link href={href} className="hover:text-accent hover:underline" data-testid="cf-cell-link">
      {num(fact)}
    </Link>
  ) : (
    num(fact)
  )
  let sub: React.ReactNode = null
  if (mode === 'forecast' && Number(ahead) !== 0) sub = <span className="text-faint">ждём {num(ahead)}</span>
  if (mode === 'planfact' && Number(plan) !== 0) {
    // хуже плана: поступлений с ожидаемыми меньше плана, платежей — больше
    const projected = Number(fact) + Number(ahead)
    const worse = section !== null && projected < Number(plan) - 0.5
    sub = <span className={worse ? 'text-bad' : 'text-faint'}>план {num(plan)}</span>
  }
  return (
    <>
      <span className="block">{main}</span>
      {sub && (
        <span className="block text-[11px]" data-testid="cf-sub">
          {sub}
        </span>
      )}
    </>
  )
}

function Table({ data, side, mode }: { data: CashFlow; side: Side; mode: Mode }) {
  const [open, setOpen] = useState<Set<string>>(new Set()) // раскрытые статьи с подстатьями
  // в прогнозе — строки с фактом или ожидаемым; в план-факте — и строки только с планом
  const shown = (r: CashFlowRow) => Number(r.total) !== 0 || Number(r.forecast_total) !== 0 || (mode === 'planfact' && Number(r.plan_total) !== 0)
  const income = data.rows.filter((r) => r.section === 'income' && shown(r))
  const expense = data.rows.filter((r) => r.section === 'expense' && shown(r))
  const cell = 'whitespace-nowrap px-3 py-1.5 text-right align-top tabular-nums'
  const first = 'sticky left-0 z-10 bg-card px-3 py-1.5 text-left align-top'
  const periodCls = (p: CashFlowPeriod) => (p.kind === 'future' ? 'bg-bg/40' : '')
  const href = (r: CashFlowRow, from: string, to: string) => (r.transfer ? opsHref(from, to, null, data.account_id) : opsHref(from, to, r.category_id, data.account_id))
  const total = (label: string, values: { fact: string | null; ahead?: string; plan?: string }[], all: { fact: string | null; ahead?: string; plan?: string }, tone = '', testid?: string, section: 'income' | 'expense' | null = null) => (
    <tr className="border-t border-line font-semibold" data-testid={testid}>
      <td className={first}>{label}</td>
      {[...values, all].map((v, i) => (
        <td key={i} className={`${cell} ${tone} ${i < data.periods.length ? periodCls(data.periods[i]) : ''}`}>
          {v.fact === null ? '—' : v.ahead === undefined ? num(v.fact) : <Amount fact={v.fact} ahead={v.ahead} plan={v.plan ?? '0'} mode={mode} section={section} href={null} />}
        </td>
      ))}
    </tr>
  )
  const section = (title: string, rows: CashFlowRow[]) => (
    <>
      <tr className="border-t border-line bg-bg">
        <td className={`${first} !bg-bg text-xs uppercase tracking-wider text-muted`}>{title}</td>
        <td colSpan={data.periods.length + 1} />
      </tr>
      {rows.map((r) => {
        const key = `${r.section}-${r.transfer ? 'transfer' : r.category_id ?? 'none'}`
        const kids = (r.children ?? []).filter(shown)
        const expanded = open.has(key)
        const line = (row: CashFlowRow, child: boolean) => (
          <>
            {row.values.map((v, i) => (
              <td key={i} className={`${cell} ${periodCls(data.periods[i])}`}>
                <Amount fact={v} ahead={row.forecast[i] ?? '0'} plan={row.plan[i] ?? '0'} mode={mode} section={row.section} href={href(row, data.periods[i].date_from, data.periods[i].date_to)} />
              </td>
            ))}
            <td className={`${cell} ${child ? '' : 'font-medium'}`}>
              <Amount fact={row.total} ahead={row.forecast_total} plan={row.plan_total} mode={mode} section={row.section} href={href(row, data.date_from, data.date_to)} />
            </td>
          </>
        )
        return [
          <tr key={key} className="border-t border-line" data-testid="cf-row" data-name={r.name}>
            <td className={first}>
              <span className="inline-flex items-center gap-1">
                {kids.length > 0 && (
                  <button
                    type="button"
                    onClick={() =>
                      setOpen((prev) => {
                        const next = new Set(prev)
                        if (next.has(key)) next.delete(key)
                        else next.add(key)
                        return next
                      })
                    }
                    aria-expanded={expanded}
                    aria-label={expanded ? 'Скрыть подстатьи' : 'Показать подстатьи'}
                    title={`Подстатей: ${kids.length} — их суммы уже в статье`}
                    className="rounded text-muted hover:text-fg"
                    data-testid="cf-toggle"
                  >
                    <ChevronRight className={`h-4 w-4 transition-transform ${expanded ? 'rotate-90' : ''}`} />
                  </button>
                )}
                <Link href={href(r, data.date_from, data.date_to)} className={`hover:text-accent hover:underline ${r.transfer ? 'text-muted' : ''}`}>
                  {r.name}
                </Link>
              </span>
            </td>
            {line(r, false)}
          </tr>,
          ...(expanded
            ? kids.map((c) => (
                <tr key={`${key}-${c.category_id}`} className="border-t border-line/60 text-muted" data-testid="cf-child">
                  <td className={`${first} pl-9`}>
                    <Link href={href(c, data.date_from, data.date_to)} className="hover:text-accent hover:underline">
                      · {c.name}
                    </Link>
                  </td>
                  {line(c, true)}
                </tr>
              ))
            : []),
        ]
      })}
    </>
  )
  const per = (f: (p: CashFlowPeriod) => { fact: string | null; ahead?: string; plan?: string }) => data.periods.map(f)
  return (
    <section className="overflow-x-auto rounded-xl border border-line bg-card" data-testid="cf-table">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="text-xs text-muted">
            <th className={`${first} font-normal`}>Статья, ₽</th>
            {data.periods.map((p) => (
              <th key={p.date_from} className={`${cell} font-normal ${periodCls(p)}`} data-kind={p.kind}>
                {periodTitle(p, data.granularity)}
                {p.kind !== 'past' && <span className="block text-[10px] text-faint">{p.kind === 'current' ? 'идёт' : 'прогноз'}</span>}
              </th>
            ))}
            <th className={`${cell} font-normal`}>Итого</th>
          </tr>
        </thead>
        <tbody>
          {total('Остаток на начало', per((p) => ({ fact: p.opening })), { fact: data.opening }, 'text-muted')}
          {side !== 'expense' && section('Поступления', income)}
          {side !== 'expense' &&
            total('Итого поступления', per((p) => ({ fact: p.income, ahead: p.forecast_income, plan: p.plan_income })), { fact: data.income, ahead: data.forecast_income, plan: data.plan_income }, 'text-ok', 'cf-income', 'income')}
          {side !== 'income' && section('Платежи', expense)}
          {side !== 'income' &&
            total('Итого платежи', per((p) => ({ fact: p.expense, ahead: p.forecast_expense, plan: p.plan_expense })), { fact: data.expense, ahead: data.forecast_expense, plan: data.plan_expense }, 'text-bad', 'cf-expense', 'expense')}
          {total(
            'Чистый поток',
            per((p) => ({ fact: p.net, ahead: sum(p.forecast_income, p.forecast_expense), plan: sum(p.plan_income, p.plan_expense) })),
            { fact: data.net, ahead: sum(data.forecast_income, data.forecast_expense), plan: sum(data.plan_income, data.plan_expense) },
          )}
          {total(data.has_plan ? 'Остаток на конец (прогноз)' : 'Остаток на конец', per((p) => ({ fact: p.closing })), { fact: data.closing }, '', 'cf-closing')}
        </tbody>
      </table>
    </section>
  )
}

// ---------- вкладка ----------

export function CashFlowTab({ accounts }: { accounts: Account[] }) {
  const today = todayMsk()
  const [preset, setPreset] = useState<Preset>('quarter')
  const [range, setRange] = useState(() => presetRange('quarter', today))
  const [granularity, setGranularity] = useState<CashFlow['granularity']>('month')
  const [account, setAccount] = useState('')
  const [side, setSide] = useState<Side>('all')
  const [mode, setMode] = useState<Mode>('forecast')
  const [data, setData] = useState<CashFlow | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const { data: body } = await api.get<CashFlow>('/planning/cash-flow', {
        params: { date_from: range.from, date_to: range.to, granularity, ...(account ? { account_id: account } : {}) },
      })
      setData(body)
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [range, granularity, account])

  useEffect(() => {
    load()
  }, [load])

  function choose(p: Preset) {
    setPreset(p)
    if (p !== 'custom') setRange(presetRange(p, today))
  }

  const toggle = (active: boolean) => `rounded-md px-3 py-1 text-sm ${active ? 'bg-accent text-white' : 'text-muted hover:text-fg'}`
  const group = 'flex flex-wrap gap-1 rounded-lg border border-line bg-card p-1'
  const ahead = data ? sum(data.forecast_income, data.forecast_expense) : '0'
  return (
    <div className="space-y-4" data-testid="cashflow-tab">
      <p className="text-sm text-muted">
        ДДС — движение денег: что пришло на ваши счета и что ушло, по статьям; дальше сегодняшнего дня — что ждём по плану из календаря. Это не P&amp;L: прибыль
        по начислениям Ozon — в{' '}
        <Link href="/dashboard/finance/pnl" className="text-accent hover:underline">
          «Финансах»
        </Link>
        . Выплата Ozon здесь — в день, когда пришли деньги, а в P&amp;L — в неделю продаж. Переводы между своими счетами — не приход и не расход.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <div className={group} role="group" aria-label="Период">
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
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <select value={account} onChange={(e) => setAccount(e.target.value)} className={`${input} max-w-[16rem]`} aria-label="Счёт" data-testid="cf-account">
          <option value="">Все счета</option>
          {accounts
            .filter((a) => !a.archived || a.id === account)
            .map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
        </select>
        <div className={group} role="group" aria-label="Поступления или платежи">
          {(
            [
              ['all', 'Всё'],
              ['income', 'Поступления'],
              ['expense', 'Платежи'],
            ] as const
          ).map(([v, label]) => (
            <button key={v} type="button" aria-pressed={side === v} onClick={() => setSide(v)} className={toggle(side === v)} data-testid={`cf-side-${v}`}>
              {label}
            </button>
          ))}
        </div>
        <div className={group} role="group" aria-label="Что показать под фактом">
          <button type="button" aria-pressed={mode === 'forecast'} onClick={() => setMode('forecast')} className={toggle(mode === 'forecast')} data-testid="cf-mode-forecast" title="Под фактом — что ещё ждём по плану с сегодня">
            Факт и прогноз
          </button>
          <button type="button" aria-pressed={mode === 'planfact'} onClick={() => setMode('planfact')} className={toggle(mode === 'planfact')} data-testid="cf-mode-planfact" title="Под фактом — план периода по календарю; красным — хуже плана">
            План-факт
          </button>
        </div>
      </div>
      <FormError message={error} />
      {!data && !error && <p className="text-sm text-muted">Загрузка…</p>}
      {data && (
        <>
          <div className="flex flex-wrap gap-px overflow-hidden rounded-xl border border-line bg-line" data-testid="cf-summary">
            <Figure label={`На ${fmtDay(data.date_from)}`} value={data.opening === null ? '—' : rub(data.opening)} hint={data.opening === null ? 'остаток неизвестен' : 'остаток на начало'} />
            <Figure label="Пришло" value={rub(data.income)} tone="text-ok" hint={Number(data.forecast_income) ? `ждём ещё ${rub(data.forecast_income)}` : undefined} testid="cf-fig-income" />
            <Figure label="Ушло" value={rub(data.expense)} tone="text-bad" hint={Number(data.forecast_expense) ? `ждём ещё ${rub(data.forecast_expense)}` : undefined} testid="cf-fig-expense" />
            <Figure
              label="Чистый поток"
              value={rub(sum(data.net, ahead))}
              tone={Number(sum(data.net, ahead)) < 0 ? 'text-bad' : 'text-ok'}
              hint={Number(ahead) ? `факт ${rub(data.net)} · по плану ${rub(ahead)}` : undefined}
            />
            <Figure
              label={`На ${fmtDay(data.date_to)}`}
              value={data.closing === null ? '—' : rub(data.closing)}
              tone={data.closing !== null && Number(data.closing) < 0 ? 'text-bad' : ''}
              hint={data.closing === null ? 'остаток неизвестен' : data.has_plan && data.date_to >= data.today ? 'прогноз с планом' : 'остаток на конец'}
              testid="cf-fig-closing"
            />
          </div>
          {data.causes && (
            <div className={`rounded-lg border px-4 py-3 text-sm ${data.causes.below_zero ? 'border-bad/40 bg-bad/10' : 'border-warn/40 bg-warn/10'}`} role="alert" data-testid="cf-gap">
              <b className={data.causes.below_zero ? 'text-bad' : 'text-warn'}>
                {data.causes.below_zero ? 'Кассовый разрыв' : 'Остаток ниже порога'} с {fmtDay(data.causes.day)}
              </b>
              : остаток опустится до {rub(data.causes.min_balance)} ({fmtDay(data.causes.min_day)}).{' '}
              <Link href={`/dashboard/planning/calendar?day=${data.causes.day}`} className="text-accent hover:underline">
                В календарь →
              </Link>
              <GapCausesList causes={data.causes} />
            </div>
          )}
          {(data.warnings.length > 0 || data.plan_note) && (
            <ul className="space-y-1 text-sm text-warn" data-testid="cf-warnings">
              {data.warnings.map((w) => (
                <li key={w}>• {w}</li>
              ))}
              {data.plan_note && (
                <li className="text-muted" data-testid="cf-plan-note">
                  • {data.plan_note}
                </li>
              )}
            </ul>
          )}
          {data.has_data || data.has_plan ? (
            <>
              <FlowChart data={data} side={side} />
              <Table data={data} side={side} mode={mode} />
            </>
          ) : (
            <p className="rounded-xl border border-line bg-card p-4 text-sm text-muted" data-testid="cf-empty">
              За этот период операций нет. Добавьте их в разделе{' '}
              <Link href="/dashboard/operations" className="text-accent hover:underline">
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

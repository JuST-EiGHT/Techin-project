'use client'
// «Операции → Бюджеты и сценарии» (шаг 10.21): план по статьям на месяц, квартал, год; план-факт в таблице и на
// графике, отклонения в ₽ и %; правка с историей версий; заполнение «как в прошлом году / как прошлый период»; шаблон
// Excel; сценарии (базовый, оптимистичный, пессимистичный) и прогноз денег с риском кассового разрыва.
import { ChevronLeft, ChevronRight } from 'lucide-react'
import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { FormError } from '@/components/AuthForm'
import { type Drill, MoreLink, TipBox, useDrill } from '@/components/charts'
import { compact, useTokenColors } from '@/components/finance'
import { ImportPanel } from '@/components/planning/Operations'
import {
  type BudgetCell,
  type BudgetForecast,
  type BudgetGranularity,
  type BudgetPage,
  type BudgetPeriod,
  type BudgetRow,
  type BudgetSection,
  type BudgetVersion,
  opsHref,
  parseAmount,
} from '@/components/planning/types'
import { Modal, input } from '@/components/planning/ui'
import { Badge, fmtDateTime, fmtDay, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { todayMsk } from '@/lib/period'

const GRANULARITY: { value: BudgetGranularity; label: string }[] = [
  { value: 'month', label: 'Месяцы' },
  { value: 'quarter', label: 'Кварталы' },
  { value: 'year', label: 'Год' },
]
const SOURCE: Record<BudgetVersion['source'], string> = { manual: 'вручную', import: 'из файла', last_year: 'как в прошлом году', prev_period: 'как прошлый период' }
const num = (v: string | null) => (v === null ? '—' : Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 0 }))
const pctText = (c: BudgetCell) => (c.pct === null ? '' : ` · ${Number(c.pct) > 0 ? '+' : ''}${Number(c.pct).toLocaleString('ru-RU')} %`)
const factEnd = (p: BudgetPeriod, today: string) => (p.date_to > today ? today : p.date_to)

// ---------- ячейка ----------

function CellView({ c, kind, onPlan, href, testid }: { c: BudgetCell; kind: 'income' | 'expense' | 'net'; onPlan?: () => void; href?: string | null; testid?: string }) {
  const tone = c.worse === null ? 'text-muted' : c.worse ? 'text-bad' : c.diff !== null && Number(c.diff) !== 0 ? 'text-ok' : 'text-muted'
  const plan = onPlan ? (
    <button type="button" onClick={onPlan} className={`block w-full text-right tabular-nums hover:text-accent ${c.plan === null ? 'text-faint' : ''}`} data-testid="budget-plan">
      {c.plan === null ? '+ план' : num(c.plan)}
    </button>
  ) : (
    <span className={`block text-right tabular-nums ${c.plan === null ? 'text-faint' : ''}`}>{num(c.plan)}</span>
  )
  const fact =
    c.fact === null ? (
      <span className="block text-right text-xs text-faint">ещё не было</span>
    ) : (
      <span className={`block text-right text-xs tabular-nums ${tone}`}>
        {num(c.fact)}
        {c.diff !== null && Number(c.diff) !== 0 && pctText(c)}
      </span>
    )
  return (
    <div className="min-w-[6.5rem]" data-testid={testid} title={kind === 'net' ? undefined : c.diff !== null ? `Отклонение: ${Number(c.diff) > 0 ? '+' : ''}${rub(c.diff)}` : undefined}>
      {plan}
      {href && c.fact !== null ? (
        <Link href={href} className="block hover:underline" data-testid="budget-fact">
          {fact}
        </Link>
      ) : (
        fact
      )}
    </div>
  )
}

// ---------- правка плана ----------

type Editing = { row: BudgetRow; period: BudgetPeriod }

function PlanEditor({ editing, granularity, onClose, onSaved }: { editing: Editing; granularity: BudgetGranularity; onClose: () => void; onSaved: (text: string) => void }) {
  const { row, period } = editing
  const months = row.own.filter((m) => m.month >= period.date_from && m.month <= period.date_to)
  const own = months.reduce<number | null>((sum, m) => (m.plan === null ? sum : (sum ?? 0) + Number(m.plan)), null)
  const [amount, setAmount] = useState(own === null ? '' : String(own).replace('.', ','))
  const [history, setHistory] = useState<BudgetVersion[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (granularity !== 'month') return
    api
      .get<BudgetVersion[]>('/planning/budgets/history', { params: { category_id: row.category.id, month: period.date_from } })
      .then((r) => setHistory(r.data))
      .catch(() => setHistory([]))
  }, [granularity, row.category.id, period.date_from])

  async function save(value: string | null) {
    setBusy(true)
    setError(null)
    try {
      const { data } = await api.put<{ changed: number }>('/planning/budgets/plan', {
        category_id: row.category.id,
        granularity,
        start: period.date_from,
        amount: value,
      })
      onSaved(data.changed ? `План «${row.category.name}» на ${period.label} сохранён.` : 'План не изменился.')
    } catch (err) {
      setError(errorMessage(err))
      setBusy(false)
    }
  }

  function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!amount.trim()) return save(null)
    const value = parseAmount(amount)
    if (value === null || Number(value) < 0) return setError('План — число не меньше нуля, например 30 000')
    save(value)
  }

  return (
    <Modal title={`${row.category.name} — ${period.label}`} onClose={onClose}>
      <form onSubmit={submit} className="space-y-3 text-sm" data-testid="budget-editor">
        <label className="block">
          <span className="text-muted">{row.category.kind === 'income' ? 'Сколько планируете получить, ₽' : 'Сколько планируете потратить, ₽'}</span>
          <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" autoFocus className={`${input} mt-1`} data-testid="budget-amount" />
          <span className="mt-1 block text-xs text-faint">
            {granularity === 'month' ? 'Пусто — плана нет; 0 — план «ноль».' : `Сумма разойдётся по месяцам поровну (${months.length} мес.) — потом любой месяц можно поправить.`}
            {row.children.length > 0 && ' Это план самой статьи: планы подстатей добавятся к нему.'}
          </span>
        </label>
        {granularity !== 'month' && months.some((m) => m.plan !== null) && (
          <p className="text-xs text-muted">
            Сейчас по месяцам: {months.map((m) => `${fmtDay(m.month).slice(3)} — ${num(m.plan)}`).join(', ')}
          </p>
        )}
        <FormError message={error} />
        <div className="flex flex-wrap gap-2">
          <button type="submit" disabled={busy} className="rounded-lg bg-accent px-4 py-2 font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="budget-save">
            Сохранить
          </button>
          {own !== null && (
            <button type="button" disabled={busy} onClick={() => save(null)} className="rounded-lg border border-line px-4 py-2 text-muted hover:border-bad hover:text-bad">
              Убрать план
            </button>
          )}
          <button type="button" onClick={onClose} className="rounded-lg border border-line px-4 py-2 text-muted hover:text-fg">
            Отмена
          </button>
        </div>
        <Link href={opsHref(period.date_from, period.date_to, row.category.id)} className="inline-block text-xs text-accent hover:underline">
          Операции статьи за {period.label} →
        </Link>
        {history && history.length > 0 && (
          <div data-testid="budget-history">
            <p className="text-xs text-muted">История плана</p>
            <ul className="mt-1 divide-y divide-line text-xs">
              {history.map((h, i) => (
                <li key={i} className={`flex flex-wrap gap-x-3 py-1 ${h.superseded_at ? 'text-muted' : ''}`}>
                  <span className="tabular-nums">{h.amount === null ? 'план убран' : rub(h.amount)}</span>
                  <span>{SOURCE[h.source]}</span>
                  <span className="text-faint">{fmtDateTime(h.created_at)}</span>
                  {!h.superseded_at && <Badge tone="accent">действует</Badge>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </form>
    </Modal>
  )
}

// ---------- заполнение по факту ----------

type FillResult = { changed: number; kept: number; examples: { category: string; month: string; before: string | null; after: string }[]; from: string; to: string }

function FillDialog({ year, onClose, onDone }: { year: number; onClose: () => void; onDone: (text: string) => void }) {
  const today = todayMsk()
  const [source, setSource] = useState<'last_year' | 'prev_period'>('last_year')
  const [scope, setScope] = useState<BudgetGranularity>('year')
  const [start, setStart] = useState(`${year}-${today.slice(5, 7)}-01`)
  const [overwrite, setOverwrite] = useState(false)
  const [preview, setPreview] = useState<FillResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const body = useCallback(
    (dry: boolean) => ({ source, granularity: scope, start: scope === 'year' ? `${year}-01-01` : start, overwrite, dry_run: dry }),
    [source, scope, start, overwrite, year],
  )

  useEffect(() => {
    let alive = true
    api
      .post<FillResult>('/planning/budgets/fill', body(true))
      .then((r) => alive && (setPreview(r.data), setError(null)))
      .catch((err) => alive && setError(errorMessage(err)))
    return () => {
      alive = false
    }
  }, [body])

  async function apply() {
    setBusy(true)
    try {
      const { data } = await api.post<FillResult>('/planning/budgets/fill', body(false))
      onDone(`План заполнен по факту: изменено месяцев — ${data.changed}.`)
    } catch (err) {
      setError(errorMessage(err))
      setBusy(false)
    }
  }

  const pill = (active: boolean) => `rounded-md px-3 py-1 text-sm ${active ? 'bg-accent text-white' : 'text-muted hover:text-fg'}`
  return (
    <Modal title="Заполнить план по факту" onClose={onClose}>
      <div className="space-y-3 text-sm" data-testid="budget-fill">
        <p className="text-muted">План каждой статьи возьмём из её фактических поступлений и расходов. Где факта не было, план не ставим.</p>
        <div className="flex flex-wrap gap-1 rounded-lg border border-line bg-bg p-1" role="group" aria-label="Откуда взять">
          <button type="button" aria-pressed={source === 'last_year'} onClick={() => setSource('last_year')} className={pill(source === 'last_year')} data-testid="fill-last-year">
            Как в прошлом году
          </button>
          <button type="button" aria-pressed={source === 'prev_period'} onClick={() => setSource('prev_period')} className={pill(source === 'prev_period')} data-testid="fill-prev">
            Как прошлый период
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex gap-1 rounded-lg border border-line bg-bg p-1" role="group" aria-label="Какой период заполнить">
            {GRANULARITY.map((g) => (
              <button key={g.value} type="button" aria-pressed={scope === g.value} onClick={() => setScope(g.value)} className={pill(scope === g.value)}>
                {g.value === 'month' ? 'Месяц' : g.value === 'quarter' ? 'Квартал' : `${year} год`}
              </button>
            ))}
          </div>
          {scope !== 'year' && (
            <input type="month" value={start.slice(0, 7)} onChange={(e) => e.target.value && setStart(`${e.target.value}-01`)} aria-label="Месяц" className={`${input} w-auto`} />
          )}
        </div>
        <label className="flex items-start gap-2">
          <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} className="mt-0.5 h-4 w-4 accent-accent" />
          <span>
            Заменить уже заданный план
            <span className="block text-xs text-faint">Без галочки трогаем только месяцы без плана. Прежний план останется в истории.</span>
          </span>
        </label>
        <FormError message={error} />
        {preview && (
          <div className="rounded-lg border border-line p-3" data-testid="fill-preview">
            <p>
              По факту {fmtDay(preview.from)} — {fmtDay(preview.to)}: изменится месяцев — <b>{preview.changed}</b>
              {preview.kept > 0 && <span className="text-muted">; уже есть свой план — {preview.kept}</span>}.
            </p>
            {preview.examples.length > 0 && (
              <ul className="mt-1 space-y-0.5 text-xs text-muted">
                {preview.examples.map((x, i) => (
                  <li key={i}>
                    {x.category}, {fmtDay(x.month).slice(3)}: {x.before === null ? 'нет плана' : rub(x.before)} → <b className="text-fg">{rub(x.after)}</b>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        <div className="flex gap-2">
          <button type="button" disabled={busy || !preview || preview.changed === 0} onClick={apply} className="rounded-lg bg-accent px-4 py-2 font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="fill-apply">
            {preview && preview.changed === 0 ? 'Нечего заполнять' : `Заполнить (${preview?.changed ?? 0})`}
          </button>
          <button type="button" onClick={onClose} className="rounded-lg border border-line px-4 py-2 text-muted hover:text-fg">
            Отмена
          </button>
        </div>
      </div>
    </Modal>
  )
}

// ---------- график план-факт ----------

type ChartRow = { label: string; period: BudgetPeriod; planIncome: number | null; factIncome: number | null; planExpense: number | null; factExpense: number | null }

function PlanFactTip({ active, payload, drill }: { active?: boolean; payload?: { payload: ChartRow }[]; drill: Drill<ChartRow> }) {
  if (!active || !payload?.length) return null
  const r = payload[0].payload
  const line = (label: string, plan: number | null, fact: number | null, tone: string) => (
    <div className={`tabular-nums ${tone}`}>
      {label}: план {plan === null ? '—' : rub(String(plan))}, факт {fact === null ? '—' : rub(String(fact))}
    </div>
  )
  return (
    <TipBox href={drill.hrefFor(r)} touch={drill.touch}>
      <div className="font-medium">{r.period.label}{r.period.partial ? ' (идёт)' : ''}</div>
      {line('Поступления', r.planIncome, r.factIncome, 'text-ok')}
      {line('Расходы', r.planExpense, r.factExpense, 'text-bad')}
    </TipBox>
  )
}

function PlanFactChart({ data }: { data: BudgetPage }) {
  const colors = useTokenColors()
  const drill = useDrill<ChartRow>((r) => (r.period.started ? opsHref(r.period.date_from, factEnd(r.period, data.today)) : null))
  if (!colors) return null
  const n = (v: string | null) => (v === null ? null : Number(v))
  const rows: ChartRow[] = data.periods.map((p, i) => ({
    label: p.label.replace(` ${data.year}`, ''),
    period: p,
    planIncome: n(data.income.cells[i].plan),
    factIncome: n(data.income.cells[i].fact),
    planExpense: n(data.expense.cells[i].plan),
    factExpense: n(data.expense.cells[i].fact),
  }))
  const lastStarted = [...data.periods].reverse().find((p) => p.started)
  return (
    <section className="rounded-xl border border-line bg-card p-4" data-testid="budget-chart">
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
        <h2 className="text-sm font-semibold text-fg">План и факт</h2>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-ok/40" /> план поступлений
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-ok" /> факт
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-bad/40" /> план расходов
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-bad" /> факт
        </span>
        {lastStarted && <MoreLink href={opsHref(data.periods[0].date_from, factEnd(lastStarted, data.today))} />}
      </div>
      <div className={`h-56 ${drill.boxProps.className}`} onPointerDown={drill.boxProps.onPointerDown}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: 0 }} onClick={drill.onClick}>
            <CartesianGrid vertical={false} stroke={colors.line} />
            <XAxis dataKey="label" tick={{ fill: colors.muted, fontSize: 11 }} tickLine={false} axisLine={{ stroke: colors.line }} minTickGap={4} />
            <YAxis tick={{ fill: colors.muted, fontSize: 11 }} tickFormatter={compact} tickLine={false} axisLine={false} width={64} />
            <Tooltip content={<PlanFactTip drill={drill} />} cursor={{ fill: colors.line, fillOpacity: 0.4 }} {...drill.tooltipProps} />
            <Bar dataKey="planIncome" fill={colors.ok} fillOpacity={0.35} radius={[3, 3, 0, 0]} maxBarSize={18} isAnimationActive={false} />
            <Bar dataKey="factIncome" fill={colors.ok} radius={[3, 3, 0, 0]} maxBarSize={18} isAnimationActive={false} />
            <Bar dataKey="planExpense" fill={colors.bad} fillOpacity={0.35} radius={[3, 3, 0, 0]} maxBarSize={18} isAnimationActive={false} />
            <Bar dataKey="factExpense" fill={colors.bad} radius={[3, 3, 0, 0]} maxBarSize={18} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}

// ---------- таблица ----------

function Table({ data, onEdit }: { data: BudgetPage; onEdit: (e: Editing) => void }) {
  const box = useRef<HTMLElement>(null)
  // 12 месяцев шире экрана — сразу показываем текущий период рядом с колонкой статей
  useEffect(() => {
    const el = box.current
    const current = el?.querySelector<HTMLElement>('[data-current]')
    const head = el?.querySelector<HTMLElement>('th')
    if (el && current && head && current.offsetLeft + current.offsetWidth > el.clientWidth) {
      el.scrollLeft = current.offsetLeft - head.offsetWidth - Math.max(0, (el.clientWidth - head.offsetWidth - current.offsetWidth) / 2)
    }
  }, [data])
  const withTotal = data.granularity !== 'year'
  const first = 'sticky left-0 z-10 bg-card px-3 py-1.5 text-left align-top'
  const td = 'px-2 py-1.5 align-top'
  const link = (p: BudgetPeriod, categoryId?: string) => (p.started ? opsHref(p.date_from, factEnd(p, data.today), categoryId) : null)
  const yearPeriod: BudgetPeriod = {
    key: String(data.year),
    label: `${data.year} год`,
    date_from: `${data.year}-01-01`,
    date_to: `${data.year}-12-31`,
    started: data.periods[0].started,
    partial: false,
  }

  const rowView = (r: BudgetRow, child: boolean): React.ReactNode[] => [
    <tr key={r.category.id} className={`border-t border-line ${child ? 'text-muted' : ''}`} data-testid={child ? 'budget-child' : 'budget-row'}>
      <td className={`${first} ${child ? 'pl-7' : 'font-medium'}`}>
        {child && '· '}
        {r.category.name}
        {r.category.archived && <span className="text-xs text-faint"> (архив)</span>}
      </td>
      {r.cells.map((c, i) => (
        <td key={i} className={td}>
          <CellView c={c} kind={r.category.kind} onPlan={() => onEdit({ row: r, period: data.periods[i] })} href={link(data.periods[i], r.category.id)} testid="budget-cell" />
        </td>
      ))}
      {withTotal && (
        <td className={`${td} border-l border-line`}>
          <CellView c={r.total} kind={r.category.kind} href={link(yearPeriod, r.category.id)} />
        </td>
      )}
    </tr>,
    ...r.children.flatMap((ch) => rowView(ch, true)),
  ]

  const section = (title: string, s: BudgetSection, kind: 'income' | 'expense') => (
    <>
      <tr className="border-t border-line bg-bg">
        <td className={`${first} !bg-bg text-xs uppercase tracking-wider text-muted`}>{title}</td>
        <td colSpan={data.periods.length + (withTotal ? 1 : 0)} />
      </tr>
      {s.rows.length === 0 && (
        <tr className="border-t border-line">
          <td className={`${first} text-xs text-faint`} colSpan={1}>
            Нет статей с отметкой «Бюджеты» — включите её в «Статьях»
          </td>
          <td colSpan={data.periods.length + (withTotal ? 1 : 0)} />
        </tr>
      )}
      {s.rows.flatMap((r) => rowView(r, false))}
      <tr className="border-t border-line text-muted">
        <td className={`${first} text-xs`}>Вне бюджета и без статьи</td>
        {s.other.cells.map((c, i) => (
          <td key={i} className={td}>
            <CellView c={c} kind={kind} />
          </td>
        ))}
        {withTotal && (
          <td className={`${td} border-l border-line`}>
            <CellView c={s.other.total} kind={kind} />
          </td>
        )}
      </tr>
      <tr className="border-t border-line font-semibold" data-testid={`budget-total-${kind}`}>
        <td className={first}>Итого {title.toLowerCase()}</td>
        {s.cells.map((c, i) => (
          <td key={i} className={td}>
            <CellView c={c} kind={kind} href={link(data.periods[i])} />
          </td>
        ))}
        {withTotal && (
          <td className={`${td} border-l border-line`}>
            <CellView c={s.total} kind={kind} />
          </td>
        )}
      </tr>
    </>
  )

  return (
    <section ref={box} className="overflow-x-auto rounded-xl border border-line bg-card" data-testid="budget-table">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="text-xs text-muted">
            <th className={`${first} font-normal`}>
              Статья, ₽<span className="block text-[11px] text-faint">план / факт · отклонение</span>
            </th>
            {data.periods.map((p) => (
              <th key={p.key} className="whitespace-nowrap px-2 py-1.5 text-right font-normal" data-current={p.partial || undefined}>
                {p.label.replace(` ${data.year}`, '')}
                {p.partial && <span className="block text-[11px] text-faint">идёт</span>}
              </th>
            ))}
            {withTotal && <th className="whitespace-nowrap border-l border-line px-2 py-1.5 text-right font-normal">Итого за год</th>}
          </tr>
        </thead>
        <tbody>
          {section('Поступления', data.income, 'income')}
          {section('Расходы', data.expense, 'expense')}
          <tr className="border-t border-line font-semibold" data-testid="budget-net">
            <td className={first}>Поступления − расходы</td>
            {data.net.cells.map((c, i) => (
              <td key={i} className={td}>
                <CellView c={c} kind="net" />
              </td>
            ))}
            {withTotal && (
              <td className={`${td} border-l border-line`}>
                <CellView c={data.net.total} kind="net" />
              </td>
            )}
          </tr>
        </tbody>
      </table>
    </section>
  )
}

// ---------- сценарии и прогноз ----------

const SCENARIO_FIELDS = [
  { key: 'scen_opt_income', label: 'Оптимистичный: поступления', hint: 'например, +10 % — продажи лучше плана' },
  { key: 'scen_opt_expense', label: 'Оптимистичный: расходы', hint: 'например, −5 % — удалось сэкономить' },
  { key: 'scen_pes_income', label: 'Пессимистичный: поступления', hint: 'например, −10 % — продажи хуже' },
  { key: 'scen_pes_expense', label: 'Пессимистичный: расходы', hint: 'например, +10 % — траты выше' },
] as const

type ScenarioRow = { day: string; base: number | null; optimistic: number | null; pessimistic: number | null }

function ForecastTip({ active, payload }: { active?: boolean; payload?: { payload: ScenarioRow }[] }) {
  if (!active || !payload?.length) return null
  const r = payload[0].payload
  const v = (x: number | null) => (x === null ? '—' : rub(String(x)))
  return (
    <TipBox href={null} touch={false}>
      <div className="font-medium">{fmtDay(r.day)}</div>
      <div className="tabular-nums text-ok">Оптимистичный: {v(r.optimistic)}</div>
      <div className="tabular-nums">Базовый: {v(r.base)}</div>
      <div className="tabular-nums text-bad">Пессимистичный: {v(r.pessimistic)}</div>
    </TipBox>
  )
}

function Scenarios({ version }: { version: number }) {
  const colors = useTokenColors()
  const [months, setMonths] = useState(3)
  const [data, setData] = useState<BudgetForecast | null>(null)
  const [form, setForm] = useState<Record<string, string> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const { data: body } = await api.get<BudgetForecast>('/planning/budgets/forecast', { params: { months } })
      setData(body)
      const opt = body.scenarios.find((s) => s.key === 'optimistic')!
      const pes = body.scenarios.find((s) => s.key === 'pessimistic')!
      setForm({ scen_opt_income: opt.income_pct, scen_opt_expense: opt.expense_pct, scen_pes_income: pes.income_pct, scen_pes_expense: pes.expense_pct })
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [months])

  useEffect(() => {
    load()
  }, [load, version])

  async function save(e: React.FormEvent) {
    e.preventDefault()
    if (!form) return
    setError(null)
    try {
      await api.put('/planning/budgets/scenarios', Object.fromEntries(Object.entries(form).map(([k, v]) => [k, v.replace(',', '.').trim() || '0'])))
      setSaved('Сценарии сохранены.')
      load()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const rows: ScenarioRow[] = data
    ? data.scenarios[0].days.map((d, i) => {
        const at = (k: number) => (data.scenarios[k].days[i].balance === null ? null : Number(data.scenarios[k].days[i].balance))
        return { day: d.day, base: at(0), optimistic: at(1), pessimistic: at(2) }
      })
    : []
  return (
    <section className="space-y-3" data-testid="budget-scenarios">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">Сценарии и прогноз денег</h2>
        <div className="ml-auto flex gap-1 rounded-lg border border-line bg-card p-1" role="group" aria-label="Горизонт">
          {[3, 6, 12].map((m) => (
            <button key={m} type="button" aria-pressed={months === m} onClick={() => setMonths(m)} className={`rounded-md px-2.5 py-1 text-xs ${months === m ? 'bg-accent text-white' : 'text-muted hover:text-fg'}`}>
              {m} мес.
            </button>
          ))}
        </div>
      </div>
      <p className="text-xs text-muted">
        Остаток денег сейчас + платежи и поступления из «Календаря» + та часть бюджета, которой ещё нет ни в факте, ни в календаре (расход — в начале месяца,
        поступление — в конце: так риск разрыва виден раньше). Оптимистичный и пессимистичный сценарии меняют все плановые суммы на заданный процент.
      </p>
      <FormError message={error} />
      {data && data.start === null && (
        <p className="rounded-lg border border-warn/40 bg-warn/5 p-3 text-sm text-warn">Укажите остатки счетов в «Счетах и выписках» — без них прогноз остатка не посчитать.</p>
      )}
      {data && (
        <>
          <div className="grid gap-2 sm:grid-cols-3" data-testid="scenario-cards">
            {data.scenarios.map((s) => {
              const gap = s.first_below_zero
              return (
                <div key={s.key} className={`rounded-xl border bg-card p-3 text-sm ${gap ? 'border-bad/50' : 'border-line'}`} data-testid={`scenario-${s.key}`}>
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="font-medium">{s.label}</span>
                    {s.key !== 'base' && (
                      <span className="text-xs text-faint">
                        поступления {Number(s.income_pct) > 0 ? '+' : ''}
                        {Number(s.income_pct)} %, расходы {Number(s.expense_pct) > 0 ? '+' : ''}
                        {Number(s.expense_pct)} %
                      </span>
                    )}
                  </div>
                  <div className="mt-1 text-xs text-muted">на {fmtDay(data.date_to)}</div>
                  <div className={`text-lg font-bold tabular-nums ${s.end_balance !== null && Number(s.end_balance) < 0 ? 'text-bad' : ''}`}>{s.end_balance === null ? '—' : rub(s.end_balance)}</div>
                  {s.min_balance !== null && (
                    <div className="text-xs text-muted">
                      минимум {rub(s.min_balance)} — {fmtDay(s.min_day)}
                    </div>
                  )}
                  <div className={`mt-1 text-xs ${gap ? 'text-bad' : 'text-ok'}`} data-testid="scenario-gap">
                    {gap ? `Кассовый разрыв с ${fmtDay(gap)}` : s.end_balance === null ? '' : 'Денег хватает на весь срок'}
                    {!gap && s.first_below_threshold && <span className="block text-warn">ниже порога с {fmtDay(s.first_below_threshold)}</span>}
                  </div>
                </div>
              )
            })}
          </div>
          {colors && data.start !== null && (
            <section className="rounded-xl border border-line bg-card p-4" data-testid="forecast-chart">
              <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
                <h3 className="text-sm font-semibold text-fg">Остаток по сценариям</h3>
                <span className="flex items-center gap-1.5">
                  <span className="inline-block h-0.5 w-4 bg-ok" /> оптимистичный
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="inline-block h-0.5 w-4 bg-accent" /> базовый
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="inline-block h-0.5 w-4 bg-bad" /> пессимистичный
                </span>
                <MoreLink href="/dashboard/planning/calendar" />
              </div>
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                    <CartesianGrid vertical={false} stroke={colors.line} />
                    <XAxis dataKey="day" tickFormatter={(d: string) => fmtDay(d).slice(0, 5)} tick={{ fill: colors.muted, fontSize: 11 }} tickLine={false} axisLine={{ stroke: colors.line }} minTickGap={24} />
                    <YAxis tick={{ fill: colors.muted, fontSize: 11 }} tickFormatter={compact} tickLine={false} axisLine={false} width={64} />
                    <Tooltip content={<ForecastTip />} />
                    <ReferenceLine y={0} stroke={colors.bad} strokeDasharray="4 4" />
                    {data.threshold !== null && <ReferenceLine y={Number(data.threshold)} stroke={colors.warn} strokeDasharray="4 4" />}
                    <Line type="monotone" dataKey="optimistic" stroke={colors.ok} dot={false} strokeWidth={1.5} isAnimationActive={false} />
                    <Line type="monotone" dataKey="base" stroke={colors.accent} dot={false} strokeWidth={2} isAnimationActive={false} />
                    <Line type="monotone" dataKey="pessimistic" stroke={colors.bad} dot={false} strokeWidth={1.5} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </section>
          )}
          {data.budget_added.length > 0 && (
            <details className="text-xs text-muted" data-testid="budget-added">
              <summary className="cursor-pointer hover:text-fg">Из бюджета добавлено в прогноз ({data.budget_added.length})</summary>
              <ul className="mt-1 space-y-0.5">
                {data.budget_added.map((a, i) => (
                  <li key={i} className="tabular-nums">
                    {fmtDay(a.day)} · {a.category} · {Number(a.amount) > 0 ? '+' : ''}
                    {rub(a.amount)}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {form && (
            <form onSubmit={save} className="rounded-xl border border-line bg-card p-4 text-sm" data-testid="scenario-form">
              <p className="mb-2 text-xs text-muted">На сколько процентов плановые суммы сценария больше (+) или меньше (−) базового. Влияет только на прогноз — план не меняется.</p>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {SCENARIO_FIELDS.map((f) => (
                  <label key={f.key} className="block">
                    <span className="text-muted">{f.label}, %</span>
                    <input
                      value={form[f.key]}
                      onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                      inputMode="decimal"
                      className={`${input} mt-1`}
                      data-testid={`scenario-${f.key}`}
                    />
                    <span className="mt-1 block text-xs text-faint">{f.hint}</span>
                  </label>
                ))}
              </div>
              <div className="mt-3 flex items-center gap-3">
                <button type="submit" className="rounded-lg bg-accent px-4 py-2 font-medium text-white hover:opacity-90" data-testid="scenario-save">
                  Сохранить сценарии
                </button>
                {saved && <span className="text-ok">{saved}</span>}
              </div>
            </form>
          )}
        </>
      )}
    </section>
  )
}

// ---------- страница ----------

export function BudgetsPage({ onChanged }: { onChanged: () => void }) {
  const today = todayMsk()
  const [year, setYear] = useState(Number(today.slice(0, 4)))
  const [granularity, setGranularity] = useState<BudgetGranularity>('month')
  const [data, setData] = useState<BudgetPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [editing, setEditing] = useState<Editing | null>(null)
  const [filling, setFilling] = useState(false)
  const [version, setVersion] = useState(0)

  const load = useCallback(async () => {
    try {
      const { data: body } = await api.get<BudgetPage>('/planning/budgets', { params: { year, granularity } })
      setData(body)
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [year, granularity])

  useEffect(() => {
    load()
  }, [load])

  const changed = (text: string) => {
    setNotice(text)
    setVersion((v) => v + 1)
    load()
    onChanged()
  }

  const totals = data && [
    { label: 'Поступления', c: data.income.total, tone: 'text-ok' },
    { label: 'Расходы', c: data.expense.total, tone: 'text-bad' },
    { label: 'Итог', c: data.net.total, tone: '' },
  ]
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        План по статьям на месяц, квартал или год и что вышло на деле: отклонения в рублях и процентах. Статья с подстатьями — вместе с ними. Нажмите на план в
        ячейке, чтобы поменять его, на факт — чтобы открыть операции. В бюджете — статьи с отметкой «Бюджеты» (
        <Link href="/dashboard/operations/categories" className="text-accent hover:underline">
          «Статьи»
        </Link>
        ).
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1 rounded-lg border border-line bg-card p-1">
          <button type="button" onClick={() => setYear(year - 1)} className="rounded p-1 text-muted hover:text-fg" aria-label="Предыдущий год">
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="px-1 text-sm font-medium tabular-nums" data-testid="budget-year">
            {year}
          </span>
          <button type="button" onClick={() => setYear(year + 1)} className="rounded p-1 text-muted hover:text-fg" aria-label="Следующий год">
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
        <div className="flex gap-1 rounded-lg border border-line bg-card p-1" role="group" aria-label="Детализация">
          {GRANULARITY.map((g) => (
            <button
              key={g.value}
              type="button"
              aria-pressed={granularity === g.value}
              onClick={() => setGranularity(g.value)}
              className={`rounded-md px-3 py-1 text-sm ${granularity === g.value ? 'bg-accent text-white' : 'text-muted hover:text-fg'}`}
              data-testid={`budget-gran-${g.value}`}
            >
              {g.label}
            </button>
          ))}
        </div>
        <button type="button" onClick={() => setFilling(true)} className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent sm:ml-auto" data-testid="budget-fill-open">
          Заполнить по факту…
        </button>
      </div>
      <FormError message={error} />
      {notice && (
        <p className="text-sm text-ok" data-testid="budget-notice">
          {notice}
        </p>
      )}
      {data && (
        <>
          {!data.has_plan && (
            <p className="rounded-xl border border-accent/40 bg-accent/5 p-3 text-sm" data-testid="budget-empty">
              Плана пока нет. Нажмите «+ план» в ячейке, заполните план по факту прошлого года — или скачайте шаблон Excel ниже.
            </p>
          )}
          <div className="grid gap-px overflow-hidden rounded-xl border border-line bg-line text-sm sm:grid-cols-3" data-testid="budget-summary">
            {totals!.map((t) => (
              <div key={t.label} className="min-w-0 bg-card px-3 py-2 sm:px-4">
                <div className="text-[11px] uppercase tracking-wider text-muted">
                  {t.label}, {data.year}
                </div>
                <div className={`whitespace-nowrap font-semibold tabular-nums sm:text-base ${t.tone}`}>факт {t.c.fact === null ? '—' : rub(t.c.fact)}</div>
                <div className="whitespace-nowrap text-xs tabular-nums text-muted">план {t.c.plan === null ? '—' : rub(t.c.plan)}</div>
              </div>
            ))}
          </div>
          <PlanFactChart data={data} />
          <Table data={data} onEdit={setEditing} />
        </>
      )}
      <ImportPanel
        base="/planning/budgets"
        params={{ year: String(year) }}
        fileName={`budget-${year}`}
        allLabel={`Шаблон с планом ${year} (Excel)`}
        intro="Скачайте шаблон — в нём статьи бюджета и план по месяцам года. Поправьте суммы и загрузите обратно: сначала покажем, что изменится. Пустая ячейка — плана нет."
        onApplied={() => changed('План загружен из файла.')}
      />
      <Scenarios version={version} />
      {editing && (
        <PlanEditor
          editing={editing}
          granularity={granularity}
          onClose={() => setEditing(null)}
          onSaved={(text) => {
            setEditing(null)
            changed(text)
          }}
        />
      )}
      {filling && (
        <FillDialog
          year={year}
          onClose={() => setFilling(false)}
          onDone={(text) => {
            setFilling(false)
            changed(text)
          }}
        />
      )}
    </div>
  )
}

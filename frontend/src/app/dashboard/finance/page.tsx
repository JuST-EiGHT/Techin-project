'use client'

import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { ProductsView } from '@/components/products-pnl'
import {
  type CalcParams,
  type Coverage,
  type DataSource,
  type DayPnL,
  DayChart,
  ManualTotal,
  PROFILE_PARAMS,
  ParamsPanel,
  SourceNote,
} from '@/components/finance'
import {
  type FiguresData,
  MissingCostTable,
  type PnL,
  PnLFigures,
  PnLTable,
  type ReportDetail,
  type ReportSummary,
  type ReportType,
  Warnings,
  fmtDay,
  periodLabel,
  reportFigures,
  rub,
} from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { PRESETS, type Preset, type Range, daysCount, daysWord, monthTitle, presetRange, todayMsk } from '@/lib/period'

type PeriodPnL = {
  date_from: string
  date_to: string
  data_source: DataSource
  coverage: Coverage
  reports: { id: string; period: string; period_start: string; period_end: string; manual_total_applied: boolean }[]
  auto_total: string | null
  revenue_base: string | null
  cost_sales: string | null
  by_type: Record<string, string>
  missing_cost: ReportSummary['missing_cost']
  pnl: PnL | null
  warnings: string[]
  calculated_at: string
  from_snapshot: boolean
}

type PeriodsReport = { id: string; report_type: ReportType; period: string; period_start: string; period_end: string }
type PnLPeriods = {
  reports: PeriodsReport[]
  months: { month: string; date_from: string; date_to: string; xlsx_days: number; api_days: number; missing_days: number }[]
}

const TYPE_SHORT: Record<ReportType, string> = { accrual: 'Начисления', realization: 'Реализация' }
const DAY = /^\d{4}-\d{2}-\d{2}$/

/** День графика → «По товарам» за этот день. */
const dayProductsHref = (day: string) => `/dashboard/finance?period=custom&from=${day}&to=${day}&tab=products`

/** «Налог не задан» уже видно в цифрах — в списке предупреждений не повторяем. */
const shown = (warnings: string[]) => warnings.filter((w) => !w.startsWith('Налог не задан'))

/** Параметры расчёта → query для /pnl. */
function calcQuery(c: CalcParams): Record<string, string> {
  const q: Record<string, string> = {}
  if (c.vat_multiplier !== null) q.vat_multiplier = c.vat_multiplier
  if (c.tax_rate !== null) {
    q.tax_rate = c.tax_rate
    q.tax_base = c.tax_base ?? 'profit'
  }
  return q
}

export default function FinancePage() {
  return (
    <Suspense>
      <Finance />
    </Suspense>
  )
}

function Finance() {
  const params = useSearchParams()
  const router = useRouter()
  const [today] = useState(todayMsk)
  const [periods, setPeriods] = useState<PnLPeriods | null>(null)

  const reportId = params.get('report')
  const raw = params.get('period')
  const preset: Preset = PRESETS.some((p) => p.value === raw) ? (raw as Preset) : 'month'
  let range: Range = presetRange(preset === 'custom' ? 'month' : preset, today)
  if (preset === 'custom') {
    const from = params.get('from') ?? ''
    const to = params.get('to') ?? ''
    if (DAY.test(from) && DAY.test(to)) range = { from, to }
  }

  useEffect(() => {
    api
      .get<PnLPeriods>('/pnl/periods')
      .then((r) => setPeriods(r.data))
      .catch(() => undefined)
  }, [])

  const tab: 'pnl' | 'products' = params.get('tab') === 'products' ? 'products' : 'pnl'
  const open = (query: string, t = tab) => {
    const q = [query, t === 'products' ? 'tab=products' : ''].filter(Boolean).join('&')
    router.replace(`/dashboard/finance${q ? `?${q}` : ''}`, { scroll: false })
  }
  const go = (query: string) => open(query)
  const goRange = (r: Range) => go(`period=custom&from=${r.from}&to=${r.to}`)
  const current = reportId ? `report=${reportId}` : preset === 'custom' ? `period=custom&from=${range.from}&to=${range.to}` : `period=${preset}`
  const report = periods?.reports.find((r) => r.id === reportId)
  const productsHref = `/dashboard/finance?${current}&tab=products` // «Подробнее →» у графика по дням

  return (
    <div className={`mx-auto space-y-5 ${tab === 'products' ? 'max-w-7xl' : 'max-w-5xl'}`}>
      <div>
        <h1 className="text-2xl font-semibold">P&amp;L — прибыль и убытки</h1>
        <p className="mt-1 text-sm text-muted">
          По начислениям Ozon — за что и когда заработано, а не когда деньги пришли на счёт. Деньги на счетах (ДДС) —
          отдельный раздел{' '}
          <Link href="/dashboard/planning?tab=cashflow" className="text-accent hover:underline">
            «Планирование»
          </Link>
          .
        </p>
      </div>

      <div className="flex gap-1 border-b border-line" role="tablist" aria-label="Что показать">
        {(
          [
            ['pnl', 'P&L за период'],
            ['products', 'По товарам'],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={tab === value}
            onClick={() => open(current, value)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm ${tab === value ? 'border-accent font-medium text-fg' : 'border-transparent text-muted hover:text-fg'}`}
            data-testid={`tab-${value}`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1 rounded-lg border border-line bg-card p-1" role="group" aria-label="Период">
          {PRESETS.map((p) => {
            const active = !reportId && preset === p.value
            return (
              <button
                key={p.value}
                type="button"
                aria-pressed={active}
                onClick={() => (p.value === 'custom' ? goRange(range) : go(`period=${p.value}`))}
                className={`rounded-md px-3 py-1.5 text-sm ${active ? 'bg-accent text-white' : 'text-muted hover:text-fg'}`}
                data-testid={`preset-${p.value}`}
              >
                {p.label}
              </button>
            )
          })}
        </div>
        <select
          value={reportId ?? ''}
          onChange={(e) => go(e.target.value ? `report=${e.target.value}` : `period=${preset}`)}
          className="min-w-0 max-w-full rounded-lg border border-line bg-card px-3 py-2 text-sm outline-none focus:border-accent"
          aria-label="Открыть загруженный отчёт"
          data-testid="report-select"
        >
          <option value="">Открыть загруженный отчёт…</option>
          {periods?.reports.map((r) => (
            <option key={r.id} value={r.id}>
              {TYPE_SHORT[r.report_type]} · {periodLabel(r)}
            </option>
          ))}
        </select>
      </div>

      {!reportId && preset === 'custom' && <CustomRange range={range} onApply={goRange} />}

      {tab === 'products' ? (
        reportId ? (
          <>
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span className="font-semibold" data-testid="range-label">
                {report ? `${report.report_type === 'accrual' ? 'Отчёт по начислениям' : 'Отчёт о реализации'} · ${periodLabel(report)}` : 'Отчёт'}
              </span>
              <button type="button" onClick={() => go(`period=${preset}`)} className="ml-auto text-muted hover:text-fg" data-testid="close-report">
                ✕ Закрыть отчёт
              </button>
            </div>
            {report?.report_type === 'realization' ? (
              <section className="rounded-xl border border-line bg-card p-6 text-sm text-muted" data-testid="realization-note">
                В отчёте о реализации нет удержаний Ozon по товарам (комиссии, логистики) — прибыль по товарам считается по отчётам
                по начислениям и данным Ozon API. Продажи и себестоимость по товарам этого отчёта —{' '}
                <Link href={`/dashboard/reports/${reportId}`} className="text-accent hover:underline">
                  на странице отчёта
                </Link>
                .
              </section>
            ) : (
              <ProductsView key={reportId} query={{ report_id: reportId }} showSource={false} pnlHref={`/dashboard/finance?report=${reportId}`} />
            )}
          </>
        ) : (
          <>
            <div className="text-sm">
              <span className="font-semibold" data-testid="range-label">
                {fmtDay(range.from)} – {fmtDay(range.to)}
              </span>
              <span className="text-muted"> · {daysWord(daysCount(range))}</span>
            </div>
            <ProductsView
              key={`${range.from}:${range.to}`}
              query={{ date_from: range.from, date_to: range.to }}
              showSource
              pnlHref={`/dashboard/finance?${current}`}
            />
          </>
        )
      ) : reportId ? (
        <ReportView key={reportId} id={reportId} onClose={() => go(`period=${preset}`)} productsHref={productsHref} />
      ) : (
        <PeriodView
          key={`${range.from}:${range.to}`}
          range={range}
          periods={periods}
          onRange={goRange}
          onReport={(id) => go(`report=${id}`)}
          productsHref={productsHref}
        />
      )}
    </div>
  )
}

function CustomRange({ range, onApply }: { range: Range; onApply: (r: Range) => void }) {
  const [from, setFrom] = useState(range.from)
  const [to, setTo] = useState(range.to)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    // период сменили не здесь (месяц из подсказки) — поля показывают его
    setFrom(range.from)
    setTo(range.to)
  }, [range.from, range.to])
  function apply(next: Range) {
    if (!DAY.test(next.from) || !DAY.test(next.to)) return
    if (next.from > next.to) return setError('Начало периода позже конца')
    if (daysCount(next) > 366) return setError('Период — не длиннее 366 дней')
    setError(null)
    onApply(next)
  }
  const input = 'rounded-lg border border-line bg-card px-3 py-1.5 text-sm outline-none focus:border-accent'
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm" data-testid="custom-range">
      <label className="flex items-center gap-2">
        <span className="text-muted">с</span>
        <input
          type="date"
          value={from}
          onChange={(e) => {
            setFrom(e.target.value)
            apply({ from: e.target.value, to })
          }}
          className={input}
          data-testid="range-from"
        />
      </label>
      <label className="flex items-center gap-2">
        <span className="text-muted">по</span>
        <input
          type="date"
          value={to}
          onChange={(e) => {
            setTo(e.target.value)
            apply({ from, to: e.target.value })
          }}
          className={input}
          data-testid="range-to"
        />
      </label>
      {error && <span className="text-bad">{error}</span>}
    </div>
  )
}

/** Цифры, таблица, график — общее для периода и отчёта. */
function PnLBody({
  figures,
  manual,
  byType,
  revenue,
  manualBlock,
  paramsBlock,
  days,
  warnings,
  missing,
  busy,
  productsHref,
}: {
  figures: FiguresData
  manual: boolean
  byType: Record<string, string>
  revenue: string | null
  manualBlock: React.ReactNode
  paramsBlock: React.ReactNode
  days: DayPnL[] | null
  warnings: string[]
  missing: ReportSummary['missing_cost']
  busy: boolean
  productsHref: string
}) {
  return (
    <div className={`space-y-5 transition-opacity ${busy ? 'opacity-60' : ''}`} aria-busy={busy}>
      <PnLFigures data={figures} />
      <Warnings items={shown(warnings)} />
      {manualBlock}
      {paramsBlock}
      <PnLTable byType={byType} pnl={figures.pnl} revenue={revenue} manual={manual} />
      {days && <DayChart days={days} dayHref={dayProductsHref} moreHref={productsHref} />}
      <MissingCostTable items={missing} />
    </div>
  )
}

function PeriodView({
  range,
  periods,
  onRange,
  onReport,
  productsHref,
}: {
  range: Range
  periods: PnLPeriods | null
  onRange: (r: Range) => void
  onReport: (id: string) => void
  productsHref: string
}) {
  const [calc, setCalc] = useState<CalcParams>(PROFILE_PARAMS)
  const [manual, setManual] = useState<string | null>(null)
  const [data, setData] = useState<PeriodPnL | null>(null)
  const [base, setBase] = useState<string | null>(null) // сколько пришло без своей суммы (с ручными суммами отчётов)
  const [days, setDays] = useState<DayPnL[] | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const seq = useRef(0)

  useEffect(() => {
    const id = ++seq.current
    setBusy(true)
    const query = { date_from: range.from, date_to: range.to, ...calcQuery(calc), ...(manual !== null ? { manual_total: manual } : {}) }
    api
      .get<PeriodPnL>('/pnl', { params: query })
      .then((r) => {
        if (id !== seq.current) return
        setData(r.data)
        setError(null)
        if (manual === null) setBase(r.data.pnl?.effective_total ?? null)
      })
      .catch((err) => id === seq.current && setError(errorMessage(err)))
      .finally(() => id === seq.current && setBusy(false))
  }, [range.from, range.to, calc, manual])

  useEffect(() => {
    const vat = calc.vat_multiplier
    api
      .get<{ days: DayPnL[] }>('/pnl/by-day', { params: { date_from: range.from, date_to: range.to, ...(vat ? { vat_multiplier: vat } : {}) } })
      .then((r) => setDays(r.data.days))
      .catch(() => setDays(null))
  }, [range.from, range.to, calc.vat_multiplier])

  const pnl = data?.pnl ?? null
  const applied = data?.reports.filter((r) => r.manual_total_applied) ?? []
  return (
    <>
      <div className="space-y-2">
        <div className="text-sm">
          <span className="font-semibold" data-testid="range-label">
            {fmtDay(range.from)} – {fmtDay(range.to)}
          </span>
          <span className="text-muted"> · {daysWord(daysCount(range))}</span>
          {data?.from_snapshot && <span className="text-faint"> · сохранённый итог закрытого месяца</span>}
        </div>
        {data && <SourceNote source={data.data_source} coverage={data.coverage} />}
      </div>
      <FormError message={error} />
      {!data && !error && <p className="text-sm text-muted">Загрузка…</p>}
      {data && pnl === null && <Empty data={data} periods={periods} onRange={onRange} onReport={onReport} />}
      {data && pnl !== null && base !== null && (
        <PnLBody
          figures={{
            revenue: data.revenue_base,
            revenueHint: 'начисления «Выручка»',
            received: pnl.effective_total,
            receivedHint:
              manual !== null
                ? `введено вручную, по начислениям ${rub(base)}`
                : applied.length > 0
                  ? `с ручными суммами отчётов; по начислениям ${rub(data.auto_total)}`
                  : 'по начислениям',
            costSales: data.cost_sales,
            missingCostCount: data.missing_cost.length,
            pnl,
          }}
          manual={manual !== null}
          byType={data.by_type}
          revenue={data.revenue_base}
          manualBlock={
            <ManualTotal
              auto={base}
              manual={manual}
              onChange={setManual}
              note="Для периода не сохраняется — сохранить можно в отчёте за этот период."
            />
          }
          paramsBlock={<ParamsPanel pnl={pnl} value={calc} onChange={setCalc} note="Только для этого расчёта — профиль не меняется." />}
          days={days}
          warnings={data.warnings}
          missing={data.missing_cost}
          busy={busy}
          productsHref={productsHref}
        />
      )}
    </>
  )
}

function Empty({
  data,
  periods,
  onRange,
  onReport,
}: {
  data: PeriodPnL
  periods: PnLPeriods | null
  onRange: (r: Range) => void
  onReport: (id: string) => void
}) {
  const months = (periods?.months ?? []).filter((m) => m.xlsx_days + m.api_days > 0).slice(0, 6)
  const reports = (periods?.reports ?? []).slice(0, 4)
  const chip = 'rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent'
  return (
    <section className="space-y-4 rounded-xl border border-line bg-card p-6" data-testid="empty">
      <div>
        <h2 className="text-lg font-semibold">За этот период данных нет</h2>
        <p className="mt-1 text-sm text-muted">
          P&amp;L считается по начислениям Ozon: загрузите отчёт по начислениям из личного кабинета или подключите ключ Ozon
          API — начисления загрузятся сами.
        </p>
      </div>
      <div className="flex flex-wrap gap-3">
        <Link href="/dashboard/reports/upload" className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90">
          Загрузить отчёт
        </Link>
        <Link href="/dashboard/settings/api-keys" className={chip}>
          Подключить ключ Ozon API
        </Link>
      </div>
      {(months.length > 0 || reports.length > 0) && (
        <div className="space-y-2 border-t border-line pt-4">
          {months.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm text-muted">Данные есть за:</span>
              {months.map((m) => (
                <button key={m.month} type="button" className={chip} onClick={() => onRange({ from: m.date_from, to: m.date_to })} data-testid="month-chip">
                  {monthTitle(m.month)}
                </button>
              ))}
            </div>
          )}
          {reports.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm text-muted">Отчёты:</span>
              {reports.map((r) => (
                <button key={r.id} type="button" className={chip} onClick={() => onReport(r.id)} data-testid="report-chip">
                  {TYPE_SHORT[r.report_type]} · {periodLabel(r)}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {shown(data.warnings).filter((w) => !w.startsWith('За период нет данных')).length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-sm text-muted">
          {shown(data.warnings)
            .filter((w) => !w.startsWith('За период нет данных'))
            .map((w, i) => (
              <li key={i}>{w}</li>
            ))}
        </ul>
      )}
    </section>
  )
}

function ReportView({ id, onClose, productsHref }: { id: string; onClose: () => void; productsHref: string }) {
  const [report, setReport] = useState<ReportDetail | null>(null)
  const [days, setDays] = useState<DayPnL[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const seq = useRef(0)

  useEffect(() => {
    api
      .get<ReportDetail>(`/reports/${id}`)
      .then((r) => setReport(r.data))
      .catch((err) => setError(errorMessage(err)))
  }, [id])

  const accrual = report?.report_type === 'accrual'
  const vat = report?.vat_multiplier ?? null
  useEffect(() => {
    if (!accrual) return
    api
      .get<{ days: DayPnL[] }>('/pnl/by-day', { params: { report_id: id, ...(vat ? { vat_multiplier: vat } : {}) } })
      .then((r) => setDays(r.data.days))
      .catch(() => setDays(null))
  }, [id, accrual, vat])

  async function save(body: Record<string, string | null>) {
    const n = ++seq.current
    setBusy(true)
    setStatus('Сохраняем…')
    try {
      const { data } = await api.patch<ReportDetail>(`/reports/${id}`, body)
      if (n !== seq.current) return
      setReport(data)
      setError(null)
      setStatus('Сохранено в отчёте')
    } catch (err) {
      if (n !== seq.current) return
      setError(errorMessage(err))
      setStatus(null)
    } finally {
      if (n === seq.current) setBusy(false)
    }
  }

  if (error && !report) return <FormError message={error} />
  if (!report) return <p className="text-sm text-muted">Загрузка…</p>
  const r = report
  return (
    <>
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0">
          <div className="text-sm">
            <span className="font-semibold" data-testid="range-label">
              {r.report_type === 'accrual' ? 'Отчёт по начислениям' : 'Отчёт о реализации'} · {periodLabel(r)}
            </span>
          </div>
          <p className="truncate text-xs text-muted">{r.filename}</p>
        </div>
        <div className="ml-auto flex flex-wrap gap-3 text-sm">
          <Link href={`/dashboard/reports/${id}`} className="text-accent hover:underline">
            Страница отчёта: товары →
          </Link>
          <button type="button" onClick={onClose} className="text-muted hover:text-fg" data-testid="close-report">
            ✕ Закрыть отчёт
          </button>
        </div>
      </div>
      <SourceNote source="xlsx" />
      <FormError message={error} />
      <PnLBody
        figures={reportFigures(r)}
        manual={r.manual_total !== null}
        byType={r.summary.by_type}
        revenue={r.revenue_base}
        manualBlock={
          <ManualTotal
            auto={r.auto_total}
            manual={r.manual_total}
            onChange={(v) => save({ manual_total: v })}
            note="Сохраняется в отчёте."
            status={status}
          />
        }
        paramsBlock={
          <ParamsPanel
            pnl={r.pnl}
            value={{ vat_multiplier: r.vat_multiplier, tax_rate: r.tax_rate, tax_base: r.tax_base }}
            onChange={(c) => save({ vat_multiplier: c.vat_multiplier, tax_rate: c.tax_rate, tax_base: c.tax_rate === null ? null : (c.tax_base ?? 'profit') })}
            note="Сохраняется в отчёте; «↩ Как в профиле» — снова по налоговому профилю."
          />
        }
        days={accrual ? days : null}
        warnings={[...r.summary.warnings, ...r.pnl.warnings]}
        missing={r.summary.missing_cost}
        busy={busy}
        productsHref={productsHref}
      />
    </>
  )
}

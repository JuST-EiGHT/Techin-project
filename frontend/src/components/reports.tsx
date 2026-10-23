// Общее для страниц отчётов: типы ответа API, форматирование, блоки итогов отчёта.
import Link from 'next/link'

import { CostTemplateButton } from '@/components/CostTemplateButton'
import { useCostLabel } from '@/lib/settings'

export type ReportType = 'accrual' | 'realization'

export type ReportItem = {
  id: string
  filename: string
  report_type: ReportType
  period: string
  period_start: string | null
  period_end: string | null
  status: 'processing' | 'ready' | 'error'
  error_message: string | null
  uploaded_at: string
  parsed_at: string
  auto_total: string
  manual_total: string | null
  effective_total: string
  revenue_base: string
  cost_sales: string
  vat_multiplier: string | null // null — по налоговому профилю
  tax_base: 'profit' | 'revenue' | null
  tax_rate: string | null
  rows_count: number
  source: 'file' | 'api' // загружен файлом или получен через Ozon API (10.12)
  missing_cost_count: number
  warnings_count: number
  pnl: PnL
}

/** P&L отчёта (шаг 3.1). Налог не задан — tax, vat, net, margin = null, а не 0. */
export type PnL = {
  effective_total: string
  owed: string // себестоимость с НДС поставщика
  profit_before_tax: string
  tax: string | null
  vat: string | null
  net: string | null
  margin: string | null
  tax_title: string | null
  tax_lines: string[]
  supplier_vat: string
  pct_of_revenue: Record<'owed' | 'profit_before_tax' | 'tax' | 'vat' | 'net', string | null>
  by_type_pct: Record<string, string>
  warnings: string[]
  data_source: string
}

export type ArticleSales = {
  name: string
  sku: string
  qty: number
  revenue: string
  cost: string | null
  total_cost: string | null
  has_cost: boolean
}

export type ReportSummary = {
  by_type: Record<string, string>
  by_article: Record<string, ArticleSales>
  missing_cost: { article: string; sku: string; name: string; qty: number }[]
  warnings: string[]
  sheet_name: string
  header_row: number
}

/** Подсказка по выписке банка (шаг 3.11): сколько пришло от Ozon за продажи периода — сохраняется только по кнопке. */
export type BankHint = { amount: string; count: number; date_from: string; date_to: string; lag_weeks: number; note: string }
export type ReportDetail = ReportItem & { file_size: number; summary: ReportSummary; bank_hint?: BankHint | null }

export type Conflict = {
  id: string
  filename: string
  period: string
  period_start: string | null
  period_end: string | null
  uploaded_at: string
  same_file: boolean
}

export const TYPE_LABEL: Record<ReportType, string> = {
  accrual: 'Отчёт по начислениям',
  realization: 'Отчёт о реализации',
}

export const rub = (v: string | null) =>
  v === null
    ? '—'
    : `${Number(v).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`

/** «2026-09-07» → «07.09.2026» без сдвига часового пояса. */
export const fmtDay = (iso: string | null) => (iso ? iso.split('-').reverse().join('.') : '—')

export const fmtDateTime = (iso: string) =>
  new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })

export function periodLabel(r: { period: string; period_start: string | null; period_end: string | null }) {
  if (r.period_start && r.period_end) {
    return r.period_start === r.period_end ? fmtDay(r.period_start) : `${fmtDay(r.period_start)} – ${fmtDay(r.period_end)}`
  }
  return r.period || 'Период не указан'
}

export function Badge({ tone, children, title }: { tone: 'warn' | 'muted' | 'bad' | 'accent' | 'ok'; children: React.ReactNode; title?: string }) {
  const cls = {
    ok: 'bg-ok/15 text-ok',
    warn: 'bg-warn/15 text-warn',
    muted: 'bg-line text-muted',
    bad: 'bg-bad/15 text-bad',
    accent: 'bg-accent/15 text-accent',
  }[tone]
  return (
    <span title={title} className={`whitespace-nowrap rounded px-2 py-0.5 text-xs ${cls}`}>
      {children}
    </span>
  )
}

export const pctText = (v: string | null) => (v === null ? '—' : `${Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} %`)

/** Пять главных цифр P&L: откуда они взяты — подсказки под суммами. */
export type FiguresData = {
  revenue: string | null // продано: выручка
  revenueHint: string
  received: string // пришло от Ozon: ручная сумма или по начислениям
  receivedHint: string
  costSales: string | null // себестоимость без НДС
  missingCostCount: number
  pnl: PnL
}

export const vatPctText = (multiplier: string) => {
  const pct = Math.round((Number(multiplier) - 1) * 1000) / 10
  return pct === 0 ? 'без НДС' : `${pct.toLocaleString('ru-RU')} %`
}

/** Фактический НДС поставщика: у артикулов со своей ставкой в справочнике она своя — берём себестоимость с НДС / без. */
const effectiveVat = (owed: string, costSales: string | null, fallback: string) =>
  costSales !== null && Number(costSales) > 0 ? String(Number(owed) / Number(costSales)) : fallback

/** Полоса из пяти цифр: продано, получено от Ozon, себестоимость с НДС, налог, остаток и маржа (самый заметный блок). */
export function PnLFigures({ data }: { data: FiguresData }) {
  const costLabel = useCostLabel()
  const { pnl } = data
  const taxSet = pnl.tax !== null
  const net = pnl.net === null ? null : Number(pnl.net)
  const resultTone = net === null ? '' : net < 0 ? 'bg-bad/10' : 'bg-ok/10'
  return (
    <div
      className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-3 lg:grid-cols-[1fr_1fr_1fr_1fr_1.3fr]"
      data-testid="report-figures"
    >
      <Cell label="Продано (выручка)" value={rub(data.revenue)} hint={data.revenueHint} tone="ok" testid="figure-revenue" />
      <Cell
        label="Получено от Ozon"
        value={rub(data.received)}
        hint={data.receivedHint}
        tone={Number(data.received) < 0 ? 'bad' : undefined}
        testid="figure-received"
      />
      <Cell
        label={costLabel}
        value={rub(pnl.owed)}
        hint={
          data.missingCostCount > 0
            ? `не найдена для ${data.missingCostCount} арт. — занижена`
            : `без НДС ${rub(data.costSales)}; НДС поставщика ${vatPctText(effectiveVat(pnl.owed, data.costSales, pnl.supplier_vat))}`
        }
        hintTone={data.missingCostCount > 0 ? 'warn' : undefined}
        testid="figure-cost"
      />
      {taxSet ? (
        <Cell
          label={Number(pnl.vat) > 0 ? 'Налог и НДС' : 'Налог'}
          value={rub(String(Number(pnl.tax) + Number(pnl.vat ?? 0)))}
          hint={Number(pnl.vat) > 0 ? `налог ${rub(pnl.tax)}, НДС ${rub(pnl.vat)}` : (pnl.tax_title ?? '')}
          tone="warn"
          testid="figure-tax"
        />
      ) : (
        <div className="bg-card px-4 py-3" data-testid="figure-tax">
          <div className="text-[11px] uppercase tracking-wider text-muted">Налог</div>
          <div className="mt-1 text-lg font-bold text-warn">не задан</div>
          <Link href="/dashboard/settings/taxes" className="mt-0.5 block text-xs text-accent hover:underline">
            Указать систему налогообложения →
          </Link>
        </div>
      )}
      <div className="col-span-2 bg-card sm:col-span-2 lg:col-span-1" data-testid="figure-net">
        <div className={`h-full px-4 py-3 ${resultTone}`}>
          <div className="text-[11px] uppercase tracking-wider text-muted">Остаток (чистая прибыль)</div>
          <div className={`mt-1 text-2xl font-bold tabular-nums ${net === null ? '' : net < 0 ? 'text-bad' : 'text-ok'}`}>
            {taxSet ? rub(pnl.net) : '—'}
          </div>
          <div className={`text-sm font-semibold ${net === null ? 'text-muted' : net < 0 ? 'text-bad' : 'text-ok'}`}>
            {taxSet ? `Маржа ${pctText(pnl.margin)}` : `до налога ${rub(pnl.profit_before_tax)}`}
          </div>
        </div>
      </div>
    </div>
  )
}

/** Цифры отчёта (шаг 3.1). */
export function ReportFigures({ report }: { report: ReportItem }) {
  return <PnLFigures data={reportFigures(report)} />
}

export function reportFigures(report: ReportItem): FiguresData {
  return {
    revenue: report.revenue_base,
    revenueHint: report.report_type === 'accrual' ? 'начисления «Выручка»' : 'итого реализовано',
    received: report.effective_total,
    receivedHint: report.manual_total !== null ? `введено вручную, по отчёту ${rub(report.auto_total)}` : 'по отчёту',
    costSales: report.cost_sales,
    missingCostCount: report.missing_cost_count,
    pnl: report.pnl,
  }
}

function Cell({
  label,
  value,
  hint,
  tone,
  hintTone,
  testid,
}: {
  label: string
  value: string
  hint: string
  tone?: 'ok' | 'bad' | 'warn'
  hintTone?: 'warn'
  testid?: string
}) {
  const valueTone = tone ? { ok: 'text-ok', bad: 'text-bad', warn: 'text-warn' }[tone] : ''
  return (
    <div className="bg-card px-4 py-3" data-testid={testid}>
      <div className="text-[11px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`mt-1 text-lg font-bold tabular-nums ${valueTone}`}>{value}</div>
      <div className={`mt-0.5 text-xs ${hintTone === 'warn' ? 'text-warn' : 'text-faint'}`}>{hint}</div>
    </div>
  )
}

const TONE = { ok: 'text-ok', bad: 'text-bad', accent: 'text-accent' } as const

/** Предупреждения P&L для списка на странице: «налог не задан» уже видно в цифрах — не повторяем. */
export const pnlWarnings = (report: ReportItem) => report.pnl.warnings.filter((w) => !w.startsWith('Налог не задан'))

/** Расчёт остатка по шагам: пришло от Ozon − себестоимость с НДС − налог − НДС; проценты — от выручки. */
export function PnLBreakdown({ report }: { report: ReportItem }) {
  const { pnl } = report
  const costLabel = useCostLabel()
  const rows: { label: string; value: string | null; pct: string | null; strong?: boolean; tone?: 'ok' | 'bad' | 'accent' }[] = [
    { label: 'Пришло от Ozon', value: pnl.effective_total, pct: null },
    { label: `− ${costLabel}`, value: pnl.owed, pct: pnl.pct_of_revenue.owed },
    { label: '= Прибыль до налога', value: pnl.profit_before_tax, pct: pnl.pct_of_revenue.profit_before_tax, strong: true, tone: 'accent' },
    { label: '− Налог', value: pnl.tax, pct: pnl.pct_of_revenue.tax },
    ...(Number(pnl.vat) > 0 ? [{ label: '− НДС к уплате', value: pnl.vat, pct: pnl.pct_of_revenue.vat }] : []),
    {
      label: '= Остаток',
      value: pnl.net,
      pct: pnl.pct_of_revenue.net,
      strong: true,
      tone: pnl.net === null ? undefined : Number(pnl.net) < 0 ? ('bad' as const) : ('ok' as const),
    },
  ]
  return (
    <section className="rounded-xl border border-line bg-card" data-testid="pnl-breakdown">
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2">
        <h2 className="text-sm font-semibold">Остаток и маржа</h2>
        <span className="text-xs text-muted">{pnl.tax_title ? `налог: ${pnl.tax_title}` : 'налог не задан'}</span>
        {pnl.tax_title === null && (
          <Link href="/dashboard/settings/taxes" className="ml-auto text-xs text-accent hover:underline">
            Указать систему налогообложения →
          </Link>
        )}
      </div>
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-faint">
          <tr>
            <th className="px-4 pt-2 font-normal" />
            <th className="px-4 pt-2 text-right font-normal">₽</th>
            <th className="px-4 pt-2 text-right font-normal">% от выручки</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label} className={r.strong ? 'font-semibold' : ''}>
              <td className="px-4 py-1.5">{r.label}</td>
              <td className={`whitespace-nowrap px-4 py-1.5 text-right tabular-nums ${r.tone ? TONE[r.tone] : ''}`}>
                {r.value === null ? 'не задан' : rub(r.value)}
              </td>
              <td className="whitespace-nowrap px-4 py-1.5 text-right tabular-nums text-muted">{pctText(r.pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {pnl.tax_lines.length > 0 && (
        <details className="border-t border-line px-4 py-2 text-sm">
          <summary className="cursor-pointer text-muted hover:text-fg">Как посчитан налог</summary>
          <ul className="mt-2 space-y-1 text-muted" data-testid="tax-lines">
            {pnl.tax_lines.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}

export function MissingCostTable({ items }: { items: ReportSummary['missing_cost'] }) {
  if (items.length === 0) return null
  return (
    <section className="rounded-xl border border-warn/40 bg-card">
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2">
        <h2 className="text-sm font-semibold text-warn">Не найдена себестоимость — прибыль завышена</h2>
        <span className="ml-auto flex flex-wrap items-center gap-4">
          <CostTemplateButton rows="missing">Скачать шаблон с этими товарами</CostTemplateButton>
          <Link href="/dashboard/products/costs?missing=1" className="text-sm text-accent hover:underline">
            Указать себестоимость →
          </Link>
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted">
            <tr className="border-b border-line">
              <th className="px-4 py-2 font-normal">Артикул</th>
              <th className="px-4 py-2 font-normal">Название</th>
              <th className="px-4 py-2 text-right font-normal">Продано, шт.</th>
            </tr>
          </thead>
          <tbody>
            {items.map((m) => (
              <tr key={m.article} className="border-b border-line last:border-b-0" data-testid="missing-cost">
                <td className="px-4 py-2 font-medium">{m.article}</td>
                <td className="max-w-md truncate px-4 py-2 text-muted">{m.name || '—'}</td>
                <td className="px-4 py-2 text-right tabular-nums">{m.qty}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export function Warnings({ items }: { items: string[] }) {
  if (items.length === 0) return null
  return (
    <section className="rounded-xl border border-line bg-card p-4">
      <h2 className="mb-2 text-sm font-semibold text-warn">Предупреждения</h2>
      <ul className="list-disc space-y-1 pl-5 text-sm text-fg">
        {items.map((w, i) => (
          <li key={i}>{w}</li>
        ))}
      </ul>
    </section>
  )
}

/** Начисления по типам; pct — доля в своей группе (доходы и расходы — каждая 100 %). */
export function ByTypeTable({ byType, pct }: { byType: Record<string, string>; pct?: Record<string, string> }) {
  const rows = Object.entries(byType)
  const income = rows.filter(([, v]) => Number(v) >= 0).sort((a, b) => Number(b[1]) - Number(a[1]))
  const expense = rows.filter(([, v]) => Number(v) < 0).sort((a, b) => Number(a[1]) - Number(b[1]))
  return (
    <section className="rounded-xl border border-line bg-card">
      <h2 className="border-b border-line px-4 py-2 text-sm font-semibold">Начисления по типам</h2>
      <table className="w-full text-sm">
        <tbody>
          {[...income, ...expense].map(([name, value]) => (
            <tr key={name} className="border-b border-line last:border-b-0">
              <td className="px-4 py-1.5">{name}</td>
              <td className={`whitespace-nowrap px-4 py-1.5 text-right tabular-nums ${Number(value) < 0 ? 'text-bad' : 'text-ok'}`}>{rub(value)}</td>
              {pct && <td className="w-20 whitespace-nowrap px-4 py-1.5 text-right tabular-nums text-muted">{pctText(pct[name] ?? null)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

/** Шапка отчёта: тип, период, файл. */
export function ReportHeading({ report, link }: { report: ReportItem; link?: boolean }) {
  const title = `${TYPE_LABEL[report.report_type]} · ${periodLabel(report)}`
  return (
    <div>
      <h2 className="text-lg font-semibold">{link ? <Link href={`/dashboard/analytics/reports/${report.id}`} className="hover:text-accent">{title}</Link> : title}</h2>
      <p className="text-xs text-muted">
        {report.source === 'api' ? `получен через API Ozon ${fmtDateTime(report.uploaded_at)}` : `${report.filename} · загружен ${fmtDateTime(report.uploaded_at)}`}
      </p>
    </div>
  )
}

export const rubSigned = (v: string | number | null) => {
  if (v === null) return '—'
  const n = Number(v)
  return `${n < 0 ? '−' : n > 0 ? '+' : ''}${rub(String(Math.abs(n)))}`
}

const ROW = 'grid grid-cols-[1fr_3.75rem_7rem] items-center gap-2 px-3 py-1.5 text-[13px] sm:grid-cols-[1fr_6rem_9.5rem] sm:text-sm'

function Row({ label, pct, value, className = '', valueClass = '', testid }: {
  label: React.ReactNode
  pct: string
  value: string
  className?: string
  valueClass?: string
  testid?: string
}) {
  return (
    <div className={`${ROW} rounded-md ${className}`} data-testid={testid}>
      <span className="min-w-0 hyphens-auto break-words">{label}</span>
      <span className="text-right tabular-nums text-muted">{pct}</span>
      <span className={`whitespace-nowrap text-right tabular-nums ${valueClass}`}>{value}</span>
    </div>
  )
}

const SectionLabel = ({ children }: { children: React.ReactNode }) => (
  <div className="px-3 pb-0.5 pt-3 text-[11px] uppercase tracking-wider text-muted">{children}</div>
)

/** Начисления по типам и P&L (как в калькуляторе): доходы и расходы Ozon — каждая группа 100 %,
 *  итог — сколько пришло от Ozon, ниже P&L с процентом от выручки: прибыль синим, остаток зелёным или красным. */
export function PnLTable({ byType, pnl, revenue, manual }: {
  byType: Record<string, string>
  pnl: PnL
  revenue: string | null
  manual: boolean
}) {
  const costLabel = useCostLabel()
  const entries = Object.entries(byType)
  const income = entries.filter(([, v]) => Number(v) > 0).sort((a, b) => Number(b[1]) - Number(a[1]))
  const expense = entries.filter(([, v]) => Number(v) <= 0).sort((a, b) => Number(a[1]) - Number(b[1]))
  const incomeSum = income.reduce((s, [, v]) => s + Number(v), 0)
  const expenseSum = expense.reduce((s, [, v]) => s + Number(v), 0)
  const received = Number(pnl.effective_total)
  const net = pnl.net === null ? null : Number(pnl.net)
  const pct = (name: string) => pctText(pnl.by_type_pct[name] ?? null)
  return (
    <section className="rounded-xl border border-line bg-card pb-3" data-testid="pnl-table">
      <h2 className="border-b border-line px-4 py-2 text-sm font-semibold">Начисления по типам и P&amp;L</h2>
      <div className="px-1 sm:px-2">
        <div className={`${ROW} border-b border-line text-[11px] uppercase tracking-wider text-muted`}>
          <span>Тип начисления</span>
          <span className="text-right">% группы</span>
          <span className="text-right">Сумма</span>
        </div>
        {income.length > 0 && <SectionLabel>Доходы · итого {rub(String(incomeSum))}</SectionLabel>}
        {income.map(([name, v]) => (
          <Row
            key={name}
            label={name}
            pct={pct(name)}
            value={rubSigned(v)}
            valueClass="text-ok"
            className={`hover:bg-line/40 ${name.trim().toLowerCase() === 'выручка' ? 'font-semibold' : ''}`}
            testid="type-row"
          />
        ))}
        {expense.length > 0 && <SectionLabel>Расходы Ozon · итого {rub(String(Math.abs(expenseSum)))}</SectionLabel>}
        {expense.map(([name, v]) => (
          <Row key={name} label={name} pct={pct(name)} value={rubSigned(v)} valueClass="text-bad" className="hover:bg-line/40" testid="type-row" />
        ))}
        <Row
          label={manual ? 'Итого к получению от Ozon (введено вручную)' : 'Итого к получению от Ozon'}
          pct={incomeSum > 0 ? pctText(String((Math.abs(received) / incomeSum) * 100)) : '—'}
          value={rubSigned(pnl.effective_total)}
          className="mt-2 border-y border-line bg-line/40 font-semibold"
          valueClass={received < 0 ? 'text-bad' : 'text-ok'}
          testid="received-row"
        />
        <SectionLabel>P&amp;L · % от выручки{revenue !== null ? ` (${rub(revenue)})` : ''}</SectionLabel>
        <Row label={costLabel} pct={pctText(pnl.pct_of_revenue.owed)} value={rubSigned(-Number(pnl.owed))} valueClass="text-bad" />
        <Row
          label="Прибыль до налога"
          pct={pctText(pnl.pct_of_revenue.profit_before_tax)}
          value={rubSigned(pnl.profit_before_tax)}
          className="bg-accent/10 font-semibold text-accent"
          testid="profit-row"
        />
        {pnl.tax === null ? (
          <Row
            label={
              <>
                Налог — <span className="text-warn">не задан</span>{' '}
                <Link href="/dashboard/settings/taxes" className="text-accent hover:underline">
                  указать →
                </Link>
              </>
            }
            pct="—"
            value="—"
          />
        ) : (
          <Row label={pnl.tax_title ? `Налог · ${pnl.tax_title}` : 'Налог'} pct={pctText(pnl.pct_of_revenue.tax)} value={rubSigned(-Number(pnl.tax))} valueClass="text-warn" />
        )}
        {Number(pnl.vat) > 0 && (
          <Row label="НДС к уплате" pct={pctText(pnl.pct_of_revenue.vat)} value={rubSigned(-Number(pnl.vat))} valueClass="text-warn" />
        )}
        <Row
          label={net === null ? 'Остаток — после налога' : `Остаток · маржа ${pctText(pnl.margin)}`}
          pct={pctText(pnl.pct_of_revenue.net)}
          value={net === null ? '—' : rub(pnl.net)}
          className={`mt-2 border font-semibold ${
            net === null ? 'border-line' : net < 0 ? 'border-bad bg-bad/10 text-bad' : 'border-ok bg-ok/10 text-ok'
          }`}
          testid="net-row"
        />
      </div>
    </section>
  )
}

/** Отчёт за этот период уже есть (409): заменить, оставить оба или отменить — при загрузке файла и получении через API. */
export function ConflictDialog({ detail, conflicts, onCancel, onKeep, onReplace }: {
  detail: string
  conflicts: Conflict[]
  onCancel: () => void
  onKeep: () => void
  onReplace: () => void
}) {
  return (
    <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/60 p-4" role="dialog" aria-modal="true">
      <div className="w-full max-w-lg rounded-xl border border-line bg-card p-6" data-testid="conflict-dialog">
        <h2 className="text-lg font-semibold">Отчёт за этот период уже есть</h2>
        <p className="mt-2 text-sm text-fg">{detail}</p>
        <ul className="mt-3 space-y-1 text-sm text-muted">
          {conflicts.map((c) => (
            <li key={c.id}>
              {c.filename} · {periodLabel(c)} · загружен {fmtDateTime(c.uploaded_at)}
              {c.same_file && ' · тот же файл'}
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-faint">Заменённый отчёт пропадёт из списка и расчётов, но не удалится — его можно будет вернуть.</p>
        <div className="mt-5 flex flex-wrap justify-end gap-3">
          <button onClick={onCancel} className="rounded-lg border border-line px-4 py-2 text-sm hover:border-muted">
            Отмена
          </button>
          <button onClick={onKeep} className="rounded-lg border border-line px-4 py-2 text-sm hover:border-accent hover:text-accent">
            Оставить оба
          </button>
          <button onClick={onReplace} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90" data-testid="conflict-replace">
            Заменить
          </button>
        </div>
      </div>
    </div>
  )
}

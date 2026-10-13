// Общее для страниц отчётов: типы ответа API, форматирование, блоки итогов отчёта.
import Link from 'next/link'

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
  vat_multiplier: string
  tax_rate: string
  rows_count: number
  missing_cost_count: number
  warnings_count: number
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

export type ReportDetail = ReportItem & { file_size: number; summary: ReportSummary }

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

export function Badge({ tone, children, title }: { tone: 'warn' | 'muted' | 'bad' | 'accent'; children: React.ReactNode; title?: string }) {
  const cls = {
    warn: 'bg-warn/15 text-warn',
    muted: 'bg-line text-slate-400',
    bad: 'bg-bad/15 text-bad',
    accent: 'bg-accent/15 text-accent',
  }[tone]
  return (
    <span title={title} className={`whitespace-nowrap rounded px-2 py-0.5 text-xs ${cls}`}>
      {children}
    </span>
  )
}

/** Главные цифры отчёта. Остаток и маржа появятся с расчётом P&L (шаг 3.1). */
export function ReportFigures({ report }: { report: ReportItem }) {
  const manual = report.manual_total !== null
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      <Figure label="Продано" value={rub(report.revenue_base)} hint={report.report_type === 'accrual' ? 'начисления «Выручка»' : 'итого реализовано'} />
      <Figure
        label="Пришло от Ozon"
        value={rub(report.effective_total)}
        hint={manual ? `введено вручную, по отчёту ${rub(report.auto_total)}` : 'по отчёту'}
        tone="accent"
      />
      <Figure
        label="Себестоимость проданного"
        value={rub(report.cost_sales)}
        hint={report.missing_cost_count > 0 ? `без НДС; не найдена для ${report.missing_cost_count} арт.` : 'без НДС'}
        tone={report.missing_cost_count > 0 ? 'warn' : undefined}
      />
    </div>
  )
}

function Figure({ label, value, hint, tone }: { label: string; value: string; hint: string; tone?: 'accent' | 'warn' }) {
  return (
    <div className="rounded-xl border border-line bg-card p-4">
      <div className="text-xs text-slate-400">{label}</div>
      <div className={`mt-1 text-xl font-semibold tabular-nums ${tone === 'accent' ? 'text-accent' : ''}`}>{value}</div>
      <div className={`mt-1 text-xs ${tone === 'warn' ? 'text-warn' : 'text-slate-500'}`}>{hint}</div>
    </div>
  )
}

export function MissingCostTable({ items }: { items: ReportSummary['missing_cost'] }) {
  if (items.length === 0) return null
  return (
    <section className="rounded-xl border border-warn/40 bg-card">
      <h2 className="border-b border-line px-4 py-2 text-sm font-semibold text-warn">
        Не найдена себестоимость — долг поставщику занижен, прибыль завышена
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-slate-400">
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
                <td className="max-w-md truncate px-4 py-2 text-slate-400">{m.name || '—'}</td>
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
      <ul className="list-disc space-y-1 pl-5 text-sm text-slate-300">
        {items.map((w, i) => (
          <li key={i}>{w}</li>
        ))}
      </ul>
    </section>
  )
}

export function ByTypeTable({ byType }: { byType: Record<string, string> }) {
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
      <h2 className="text-lg font-semibold">{link ? <Link href={`/dashboard/reports/${report.id}`} className="hover:text-accent">{title}</Link> : title}</h2>
      <p className="text-xs text-slate-400">
        {report.filename} · загружен {fmtDateTime(report.uploaded_at)}
      </p>
    </div>
  )
}

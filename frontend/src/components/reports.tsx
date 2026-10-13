// Общее для страниц отчётов: типы ответа API, форматирование, блоки итогов отчёта.
import Link from 'next/link'

import { CostTemplateButton } from '@/components/CostTemplateButton'

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
  tax_rate: string | null
  rows_count: number
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

/** Главные цифры отчёта: продано, пришло от Ozon, себестоимость с НДС, налог, остаток и маржа. */
export function ReportFigures({ report }: { report: ReportItem }) {
  const manual = report.manual_total !== null
  const { pnl } = report
  const vatPct = Math.round((Number(pnl.supplier_vat) - 1) * 1000) / 10
  const taxSet = pnl.tax !== null
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5" data-testid="report-figures">
      <Figure label="Продано" value={rub(report.revenue_base)} hint={report.report_type === 'accrual' ? 'начисления «Выручка»' : 'итого реализовано'} />
      <Figure
        label="Пришло от Ozon"
        value={rub(report.effective_total)}
        hint={manual ? `введено вручную, по отчёту ${rub(report.auto_total)}` : 'по отчёту'}
        tone="accent"
      />
      <Figure
        label="Себестоимость с НДС"
        value={rub(pnl.owed)}
        hint={
          report.missing_cost_count > 0
            ? `не найдена для ${report.missing_cost_count} арт. — занижена`
            : `без НДС ${rub(report.cost_sales)}; НДС поставщика ${vatPct === 0 ? 'нет' : `${vatPct.toLocaleString('ru-RU')} %`}`
        }
        tone={report.missing_cost_count > 0 ? 'warn' : undefined}
      />
      {taxSet ? (
        <Figure
          label={Number(pnl.vat) > 0 ? 'Налог и НДС' : 'Налог'}
          value={rub(String(Number(pnl.tax) + Number(pnl.vat ?? 0)))}
          hint={Number(pnl.vat) > 0 ? `налог ${rub(pnl.tax)}, НДС ${rub(pnl.vat)}` : (pnl.tax_title ?? '')}
        />
      ) : (
        <div className="rounded-xl border border-warn/40 bg-card p-4">
          <div className="text-xs text-muted">Налог</div>
          <div className="mt-1 text-xl font-semibold text-warn">не задан</div>
          <Link href="/dashboard/settings/taxes" className="mt-1 block text-xs text-accent hover:underline">
            Указать систему налогообложения →
          </Link>
        </div>
      )}
      <Figure
        label="Остаток"
        value={rub(pnl.net)}
        hint={taxSet ? `маржа ${pctText(pnl.margin)}` : `до налога ${rub(pnl.profit_before_tax)}`}
        tone={pnl.net === null ? undefined : Number(pnl.net) < 0 ? 'bad' : 'ok'}
        testid="figure-net"
      />
    </div>
  )
}

function Figure({ label, value, hint, tone, testid }: { label: string; value: string; hint: string; tone?: 'accent' | 'warn' | 'ok' | 'bad'; testid?: string }) {
  const valueTone = { accent: 'text-accent', ok: 'text-ok', bad: 'text-bad', warn: '' }[tone ?? 'warn']
  return (
    <div className="rounded-xl border border-line bg-card p-4" data-testid={testid}>
      <div className="text-xs text-muted">{label}</div>
      <div className={`mt-1 text-xl font-semibold tabular-nums ${tone ? valueTone : ''}`}>{value}</div>
      <div className={`mt-1 text-xs ${tone === 'warn' ? 'text-warn' : 'text-faint'}`}>{hint}</div>
    </div>
  )
}

const TONE = { ok: 'text-ok', bad: 'text-bad', accent: 'text-accent' } as const

/** Предупреждения P&L для списка на странице: «налог не задан» уже видно в цифрах — не повторяем. */
export const pnlWarnings = (report: ReportItem) => report.pnl.warnings.filter((w) => !w.startsWith('Налог не задан'))

/** Расчёт остатка по шагам: пришло от Ozon − себестоимость с НДС − налог − НДС; проценты — от выручки. */
export function PnLBreakdown({ report }: { report: ReportItem }) {
  const { pnl } = report
  const rows: { label: string; value: string | null; pct: string | null; strong?: boolean; tone?: 'ok' | 'bad' | 'accent' }[] = [
    { label: 'Пришло от Ozon', value: pnl.effective_total, pct: null },
    { label: '− Себестоимость с НДС поставщика', value: pnl.owed, pct: pnl.pct_of_revenue.owed },
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
        <h2 className="text-sm font-semibold text-warn">Не найдена себестоимость — долг поставщику занижен, прибыль завышена</h2>
        <span className="ml-auto flex flex-wrap items-center gap-4">
          <CostTemplateButton rows="missing">Скачать шаблон с этими товарами</CostTemplateButton>
          <Link href="/dashboard/costs?missing=1" className="text-sm text-accent hover:underline">
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
      <h2 className="text-lg font-semibold">{link ? <Link href={`/dashboard/reports/${report.id}`} className="hover:text-accent">{title}</Link> : title}</h2>
      <p className="text-xs text-muted">
        {report.filename} · загружен {fmtDateTime(report.uploaded_at)}
      </p>
    </div>
  )
}

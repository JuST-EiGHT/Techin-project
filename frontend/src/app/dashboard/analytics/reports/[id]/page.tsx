'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { BankHintBox, ManualTotal } from '@/components/finance'
import {
  type ArticleSales,
  ByTypeTable,
  PnLBreakdown,
  pnlWarnings,
  MissingCostTable,
  ReportFigures,
  ReportHeading,
  type ReportDetail,
  Warnings,
  fmtDateTime,
  rub,
} from '@/components/reports'
import { Panel, Skeleton, Sub, buttonClass } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'

function ArticlesTable({ byArticle }: { byArticle: Record<string, ArticleSales> }) {
  const rows = Object.entries(byArticle).sort((a, b) => Number(b[1].revenue) - Number(a[1].revenue))
  if (rows.length === 0) return null
  return (
    <Panel flush title="Продажи по товарам">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-soft/60 text-left text-xs font-medium text-muted">
            <tr>
              <th className="px-5 py-2 font-medium">Товар</th>
              <th className="px-5 py-2 text-right font-medium">Продано</th>
              <th className="px-5 py-2 text-right font-medium">Выручка</th>
              <th className="px-5 py-2 text-right font-medium">Себестоимость</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([article, a]) => (
              <tr key={article} className="border-t border-line" data-testid="article-row">
                <td className="px-5 py-2.5">
                  <div className="max-w-md truncate font-medium">{a.name || article}</div>
                  <div className="text-xs text-faint">{article}</div>
                </td>
                <td className="whitespace-nowrap px-5 py-2.5 text-right tabular-nums">{a.qty} шт.</td>
                <td className="whitespace-nowrap px-5 py-2.5 text-right tabular-nums">{rub(a.revenue)}</td>
                <td className="whitespace-nowrap px-5 py-2.5 text-right tabular-nums">
                  {a.has_cost ? rub(a.total_cost) : <span className="text-warn">нет</span>}
                  {a.has_cost && <Sub>{rub(a.cost)} за шт.</Sub>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

export default function ReportPage({ params }: { params: { id: string } }) {
  const router = useRouter()
  const [report, setReport] = useState<ReportDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<ReportDetail>(`/reports/${params.id}`)
      .then((r) => setReport(r.data))
      .catch((err) => setError(errorMessage(err)))
  }, [params.id])

  async function reparse() {
    setBusy(true)
    setError(null)
    try {
      setReport((await api.post<ReportDetail>(`/reports/${params.id}/reparse`)).data)
    } catch (err) {
      setError(errorMessage(err))
      api.get<ReportDetail>(`/reports/${params.id}`).then((r) => setReport(r.data)).catch(() => undefined)
    } finally {
      setBusy(false)
    }
  }

  /** «Сколько пришло от Ozon» — та же правка, что в P&L отчёта (PATCH /reports/{id}): сохраняется в отчёте. */
  async function saveManual(value: string | null) {
    setStatus('Сохраняем…')
    try {
      setReport((await api.patch<ReportDetail>(`/reports/${params.id}`, { manual_total: value })).data)
      setError(null)
      setStatus('Сохранено в отчёте')
    } catch (err) {
      setError(errorMessage(err))
      setStatus(null)
    }
  }

  async function remove() {
    if (!report || !window.confirm(`Удалить отчёт «${report.filename}»? Его можно будет вернуть.`)) return
    try {
      await api.delete(`/reports/${params.id}`)
      router.push(`/dashboard/analytics/reports?deleted=${params.id}${report.report_type === 'realization' ? '&tab=realization' : ''}`)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <Link href="/dashboard/analytics/reports" className="text-sm text-muted hover:text-fg">
        ← Все отчёты
      </Link>
      <FormError message={error} />
      {!report && !error && (
        <div className="space-y-5" aria-busy>
          <Skeleton className="h-12 w-2/3" />
          <Skeleton className="h-28 rounded-xl" />
          <Skeleton className="h-40 rounded-xl" />
        </div>
      )}
      {report && (
        <>
          <div className="flex flex-wrap items-start gap-4">
            <ReportHeading report={report} />
            <div className="ml-auto flex flex-wrap items-center gap-3">
              <Link
                href={`/dashboard/finance/pnl?report=${report.id}`}
                className={buttonClass('secondary')}
                title="P&L отчёта: НДС поставщика, налог, начисления по дням — пересчёт сразу"
              >
                Открыть в P&amp;L
              </Link>
              <button
                onClick={reparse}
                disabled={busy}
                title="Разобрать сохранённый файл заново — с текущей себестоимостью и версией разбора. Ручные правки сохранятся."
                className={buttonClass('secondary')}
              >
                {busy ? 'Перечитываем…' : 'Перечитать файл'}
              </button>
              <button onClick={remove} className={buttonClass('ghost')}>
                Удалить
              </button>
            </div>
          </div>
          {report.status === 'error' && (
            <p className="rounded-lg border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad">
              Не удалось перечитать файл: {report.error_message} Показаны итоги прошлого разбора.
            </p>
          )}
          <ReportFigures report={report} />
          <div id="manual-total">
            <ManualTotal
              auto={report.auto_total}
              manual={report.manual_total}
              onChange={saveManual}
              autoLabel="по отчёту Ozon"
              note="Сохраняется в отчёте — та же сумма в P&L отчёта."
              status={status}
            >
              <BankHintBox hint={report.bank_hint} manual={report.manual_total} onApply={saveManual} />
            </ManualTotal>
          </div>
          <p className="text-xs text-faint">
            Разобран {fmtDateTime(report.parsed_at)} · лист «{report.summary.sheet_name}», строка заголовков {report.summary.header_row}.
          </p>
          <PnLBreakdown report={report} />
          <MissingCostTable items={report.summary.missing_cost} />
          <Warnings items={[...report.summary.warnings, ...pnlWarnings(report)]} />
          <ArticlesTable byArticle={report.summary.by_article} />
          <ByTypeTable byType={report.summary.by_type} pct={report.pnl.by_type_pct} />
        </>
      )}
    </div>
  )
}

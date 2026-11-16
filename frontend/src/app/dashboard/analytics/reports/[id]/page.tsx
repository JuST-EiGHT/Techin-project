'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'

import { CopyText } from '@/components/copy'
import { FormError } from '@/components/AuthForm'
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
import { api, errorMessage } from '@/lib/api'
import { SortTh, num, useSort } from '@/components/table'

function ArticlesTable({ byArticle }: { byArticle: Record<string, ArticleSales> }) {
  const rows = Object.entries(byArticle).sort((a, b) => Number(b[1].revenue) - Number(a[1].revenue))
  const s = useSort(rows, {
    article: ([article]) => article,
    qty: ([, a]) => a.qty,
    revenue: ([, a]) => num(a.revenue),
    cost: ([, a]) => (a.has_cost ? num(a.cost) : null),
    total: ([, a]) => (a.has_cost ? num(a.total_cost) : null),
  })
  if (rows.length === 0) return null
  return (
    <section className="rounded-xl border border-line bg-card">
      <h2 className="border-b border-line px-4 py-2 text-sm font-semibold">Продажи по товарам</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted">
            <tr className="border-b border-line">
              <SortTh {...s.th('article')} kind="text" className="px-4 py-2 font-normal">
                Артикул
              </SortTh>
              <SortTh {...s.th('qty')} align="right" className="px-4 py-2 font-normal">
                Шт.
              </SortTh>
              <SortTh {...s.th('revenue')} align="right" className="px-4 py-2 font-normal">
                Выручка
              </SortTh>
              <SortTh {...s.th('cost')} align="right" className="px-4 py-2 font-normal">
                Себестоимость, шт.
              </SortTh>
              <SortTh {...s.th('total')} align="right" className="px-4 py-2 font-normal">
                Себестоимость, всего
              </SortTh>
            </tr>
          </thead>
          <tbody>
            {s.rows.map(([article, a]) => (
              <tr key={article} className="border-b border-line last:border-b-0" data-testid="article-row">
                <td className="px-4 py-2">
                  <div className="font-medium">{article}</div>
                  {a.name && <CopyText value={a.name} label="название" className="max-w-md text-xs text-muted" />}
                </td>
                <td className="px-4 py-2 text-right tabular-nums">{a.qty}</td>
                <td className="whitespace-nowrap px-4 py-2 text-right tabular-nums">{rub(a.revenue)}</td>
                <td className={`whitespace-nowrap px-4 py-2 text-right tabular-nums ${a.has_cost ? '' : 'text-warn'}`}>
                  {a.has_cost ? rub(a.cost) : 'нет'}
                </td>
                <td className="whitespace-nowrap px-4 py-2 text-right tabular-nums">{a.has_cost ? rub(a.total_cost) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export default function ReportPage({ params }: { params: { id: string } }) {
  const router = useRouter()
  const [report, setReport] = useState<ReportDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

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
    <div className="mx-auto max-w-5xl space-y-6">
      <Link href="/dashboard/analytics/reports" className="text-sm text-muted hover:text-fg">
        ← Все отчёты
      </Link>
      <FormError message={error} />
      {!report && !error && <p className="text-sm text-muted">Загрузка…</p>}
      {report && (
        <>
          <div className="flex flex-wrap items-start gap-4">
            <ReportHeading report={report} />
            <div className="ml-auto flex flex-wrap items-center gap-3">
              <Link
                href={`/dashboard/finance/pnl?report=${report.id}`}
                className="rounded-lg border border-line px-4 py-2 text-sm hover:border-accent hover:text-accent"
                title="Поправить сумму от Ozon, НДС и налог — пересчёт сразу"
              >
                Открыть в P&amp;L
              </Link>
              <button
                onClick={reparse}
                disabled={busy}
                title="Разобрать сохранённый файл заново — с текущей себестоимостью и версией разбора. Ручные правки сохранятся."
                className="rounded-lg border border-line px-4 py-2 text-sm hover:border-accent hover:text-accent disabled:opacity-60"
              >
                {busy ? 'Перечитываем…' : 'Перечитать файл'}
              </button>
              <button onClick={remove} className="rounded-lg border border-line px-4 py-2 text-sm hover:border-bad hover:text-bad">
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

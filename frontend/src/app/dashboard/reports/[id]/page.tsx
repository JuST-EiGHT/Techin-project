'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import {
  type ArticleSales,
  ByTypeTable,
  MissingCostTable,
  ReportFigures,
  ReportHeading,
  type ReportDetail,
  Warnings,
  fmtDateTime,
  rub,
} from '@/components/reports'
import { api, errorMessage } from '@/lib/api'

function ArticlesTable({ byArticle }: { byArticle: Record<string, ArticleSales> }) {
  const rows = Object.entries(byArticle).sort((a, b) => Number(b[1].revenue) - Number(a[1].revenue))
  if (rows.length === 0) return null
  return (
    <section className="rounded-xl border border-line bg-card">
      <h2 className="border-b border-line px-4 py-2 text-sm font-semibold">Продажи по товарам</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted">
            <tr className="border-b border-line">
              <th className="px-4 py-2 font-normal">Артикул</th>
              <th className="px-4 py-2 text-right font-normal">Шт.</th>
              <th className="px-4 py-2 text-right font-normal">Выручка</th>
              <th className="px-4 py-2 text-right font-normal">Себестоимость, шт.</th>
              <th className="px-4 py-2 text-right font-normal">Себестоимость, всего</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([article, a]) => (
              <tr key={article} className="border-b border-line last:border-b-0" data-testid="article-row">
                <td className="px-4 py-2">
                  <div className="font-medium">{article}</div>
                  {a.name && <div className="max-w-md truncate text-xs text-muted">{a.name}</div>}
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
      router.push(`/dashboard/reports?deleted=${params.id}${report.report_type === 'realization' ? '&tab=realization' : ''}`)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <Link href="/dashboard/reports" className="text-sm text-muted hover:text-fg">
        ← Все отчёты
      </Link>
      <FormError message={error} />
      {!report && !error && <p className="text-sm text-muted">Загрузка…</p>}
      {report && (
        <>
          <div className="flex flex-wrap items-start gap-4">
            <ReportHeading report={report} />
            <div className="ml-auto flex gap-3">
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
            Остаток, налог и маржа появятся с расчётом P&amp;L.
          </p>
          <MissingCostTable items={report.summary.missing_cost} />
          <Warnings items={report.summary.warnings} />
          <ArticlesTable byArticle={report.summary.by_article} />
          <ByTypeTable byType={report.summary.by_type} />
        </>
      )}
    </div>
  )
}

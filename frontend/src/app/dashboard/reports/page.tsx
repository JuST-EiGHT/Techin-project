'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { Badge, type ReportItem, type ReportType, ReportFigures, ReportHeading } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'

const PAGE = 20
const TABS: { type: ReportType; label: string }[] = [
  { type: 'accrual', label: 'Недельные (начисления)' },
  { type: 'realization', label: 'Месячные (реализация)' },
]

type Page = { items: ReportItem[]; total: number }

function ReportCard({ report, onDelete }: { report: ReportItem; onDelete: () => void }) {
  return (
    <section className="space-y-3 rounded-xl border border-line bg-card/40 p-4" data-testid="report-card">
      <div className="flex flex-wrap items-start gap-3">
        <ReportHeading report={report} link />
        <div className="flex flex-wrap gap-2">
          {report.status === 'error' && (
            <Badge tone="bad" title={report.error_message ?? undefined}>
              Не удалось перечитать файл
            </Badge>
          )}
          {report.missing_cost_count > 0 && <Badge tone="warn">Без себестоимости: {report.missing_cost_count}</Badge>}
          {report.warnings_count > 0 && <Badge tone="muted">Предупреждений: {report.warnings_count}</Badge>}
        </div>
        <div className="ml-auto flex gap-2">
          <Link href={`/dashboard/reports/${report.id}`} className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent">
            Открыть
          </Link>
          <button onClick={onDelete} className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-bad hover:text-bad">
            Удалить
          </button>
        </div>
      </div>
      <ReportFigures report={report} />
    </section>
  )
}

export default function ReportsPage() {
  const [tab, setTab] = useState<ReportType>('accrual')
  const [page, setPage] = useState<Page | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [deleted, setDeleted] = useState<ReportItem | { id: string; filename?: undefined } | null>(null)

  // вкладка и «отчёт удалён» могут прийти из адреса (?tab=realization, ?deleted=<id> со страницы отчёта)
  useEffect(() => {
    const q = new URLSearchParams(window.location.search)
    if (q.get('tab') === 'realization') setTab('realization')
    const id = q.get('deleted')
    if (id) setDeleted({ id })
  }, [])

  const load = useCallback(async (type: ReportType) => {
    setError(null)
    try {
      const { data } = await api.get<Page>('/reports', { params: { report_type: type, limit: PAGE } })
      setPage(data)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    setPage(null)
    load(tab)
  }, [tab, load])

  async function more() {
    if (!page) return
    setLoadingMore(true)
    try {
      const { data } = await api.get<Page>('/reports', {
        params: { report_type: tab, limit: PAGE, offset: page.items.length },
      })
      setPage({ items: [...page.items, ...data.items], total: data.total })
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoadingMore(false)
    }
  }

  async function remove(report: ReportItem) {
    if (!window.confirm(`Удалить отчёт «${report.filename}»? Его можно будет вернуть.`)) return
    try {
      await api.delete(`/reports/${report.id}`)
      setDeleted(report)
      setPage((p) => p && { items: p.items.filter((r) => r.id !== report.id), total: p.total - 1 })
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  async function undo() {
    if (!deleted) return
    try {
      const { data } = await api.post<ReportItem>(`/reports/${deleted.id}/restore`)
      setDeleted(null)
      window.history.replaceState(null, '', '/dashboard/reports')
      if (data.report_type !== tab) setTab(data.report_type)
      else load(tab)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-4">
        <h1 className="text-2xl font-semibold">Отчёты</h1>
        <Link href="/dashboard/reports/upload" className="ml-auto rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90">
          Загрузить отчёт
        </Link>
      </div>

      <div className="flex gap-2 border-b border-line">
        {TABS.map((t) => (
          <button
            key={t.type}
            onClick={() => setTab(t.type)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm ${tab === t.type ? 'border-accent text-accent' : 'border-transparent text-slate-400 hover:text-slate-200'}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {deleted && (
        <div className="flex items-center gap-4 rounded-lg border border-line bg-card px-4 py-2 text-sm" data-testid="undo">
          <span>Отчёт {deleted.filename ? `«${deleted.filename}» ` : ''}удалён.</span>
          <button onClick={undo} className="text-accent hover:underline">
            Отменить
          </button>
          <button onClick={() => setDeleted(null)} className="ml-auto text-slate-500 hover:text-slate-300" aria-label="Закрыть">
            ×
          </button>
        </div>
      )}

      <FormError message={error} />

      {!page && !error && <p className="text-sm text-slate-400">Загрузка…</p>}
      {page && page.items.length === 0 && (
        <div className="rounded-xl border border-line bg-card p-6 text-sm text-slate-400">
          {tab === 'accrual'
            ? 'Недельных отчётов пока нет. Загрузите «Отчёт по начислениям» из личного кабинета Ozon (Финансы → Отчёт по начислениям).'
            : 'Месячных отчётов пока нет. Загрузите «Отчёт о реализации» из личного кабинета Ozon (Финансы → Отчёт о реализации).'}
        </div>
      )}
      {page && page.items.length > 0 && (
        <>
          <p className="text-xs text-slate-500">Остаток, налог и маржа появятся в карточках с расчётом P&amp;L.</p>
          <div className="space-y-4">
            {page.items.map((r) => (
              <ReportCard key={r.id} report={r} onDelete={() => remove(r)} />
            ))}
          </div>
          {page.items.length < page.total && (
            <button onClick={more} disabled={loadingMore} className="rounded-lg border border-line px-4 py-2 text-sm hover:border-accent disabled:opacity-60">
              {loadingMore ? 'Загрузка…' : `Показать ещё (${page.total - page.items.length})`}
            </button>
          )}
        </>
      )}
    </div>
  )
}

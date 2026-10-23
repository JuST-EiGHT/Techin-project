'use client'
// Отчёты Ozon (шаг 10.12): одна страница с двумя входами — «Аналитика → Отчёты Ozon и загрузка» и «Финансы → Отчёты и
// документы» (там — только финансовые документы). Основа — загрузка и история 2.1–2.2 (конфликты периода, скрытие,
// перечитывание); здесь же — отчёт о реализации через API, документы Ozon файлом, полнота данных по неделям и месяцам и
// какие отчёты бывают. Вкладка — в адресе (?tab=accrual | realization | documents | coverage | catalog).
import axios from 'axios'
import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import {
  Badge,
  type Conflict,
  ConflictDialog,
  type ReportItem,
  type ReportType,
  ReportFigures,
  ReportHeading,
  fmtDay,
  fmtDateTime,
} from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { saveFile } from '@/lib/download'

const PAGE = 20
type Tab = ReportType | 'documents' | 'coverage' | 'catalog'
const TABS: { tab: Tab; label: string }[] = [
  { tab: 'accrual', label: 'Недельные (начисления)' },
  { tab: 'realization', label: 'Месячные (реализация)' },
  { tab: 'documents', label: 'Документы Ozon' },
  { tab: 'coverage', label: 'Полнота данных' },
  { tab: 'catalog', label: 'Какие отчёты бывают' },
]
type Page = { items: ReportItem[]; total: number }

const MONTHS = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']
/** Закончившиеся месяцы, новые первыми; from — не раньше (Ozon отдаёт реализацию с августа 2023). */
function pastMonths(count: number, from?: { year: number; month: number }) {
  const now = new Date()
  const out: { year: number; month: number; label: string }[] = []
  for (let i = 1; i <= count; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1)
    const ym = { year: d.getFullYear(), month: d.getMonth() + 1 }
    if (from && ym.year * 12 + ym.month < from.year * 12 + from.month) break
    out.push({ ...ym, label: `${MONTHS[ym.month - 1]} ${ym.year}` })
  }
  return out
}
const ym = (y: number, m: number) => `${y}-${String(m).padStart(2, '0')}`
const monthRange = (from: string | null, to: string | null) => (from && to ? `period=custom&from=${from}&to=${to}` : null)

// ── история ──
function ReportCard({ report, onDelete }: { report: ReportItem; onDelete: () => void }) {
  const range = monthRange(report.period_start, report.period_end)
  return (
    <section className="space-y-3 rounded-xl border border-line bg-card/40 p-4" data-testid="report-card">
      <div className="flex flex-wrap items-start gap-3">
        <ReportHeading report={report} link />
        <div className="flex flex-wrap gap-2">
          <Badge tone={report.source === 'api' ? 'accent' : 'muted'}>{report.source === 'api' ? 'Через API' : 'Файл'}</Badge>
          {report.status === 'error' && <Badge tone="bad">Не удалось перечитать</Badge>}
          {report.missing_cost_count > 0 && <Badge tone="warn">Без себестоимости: {report.missing_cost_count}</Badge>}
          {report.warnings_count > 0 && <Badge tone="muted">Предупреждений: {report.warnings_count}</Badge>}
        </div>
        <div className="ml-auto flex gap-2">
          <Link href={`/dashboard/analytics/reports/${report.id}`} className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent">
            Открыть
          </Link>
          <button onClick={onDelete} className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-bad hover:text-bad">
            Удалить
          </button>
        </div>
      </div>
      {report.status === 'error' && (
        <p className="rounded-lg bg-bad/10 px-3 py-2 text-sm" data-testid="report-error">
          Файл не удалось перечитать{report.error_message ? `: ${report.error_message}` : ''}. Итоги — прежние. Что сделать: откройте
          отчёт и нажмите «Перечитать» ещё раз или загрузите файл заново.
        </p>
      )}
      <ReportFigures report={report} />
      {range && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm" data-testid="report-links">
          {report.report_type === 'accrual' && (
            <Link href={`/dashboard/finance/pnl?${range}`} className="text-accent hover:underline">
              P&amp;L за период →
            </Link>
          )}
          <Link href={`/dashboard/analytics/profit?${range}`} className="text-accent hover:underline">
            Прибыльность за период →
          </Link>
          <Link href={`/dashboard/analytics/sales?${range}`} className="text-accent hover:underline">
            Продажи за период →
          </Link>
        </div>
      )}
    </section>
  )
}

/** «Получить через API»: отчёт о реализации за закончившийся месяц — тот же отчёт, что «Финансы → Документы» в кабинете. */
function FetchRealization({ onDone, onStart, initial }: {
  onDone: (r: ReportItem) => void
  onStart: () => void
  initial?: { year: number; month: number } | null
}) {
  const months = pastMonths(36, { year: 2023, month: 8 })
  const [value, setValue] = useState(initial ? ym(initial.year, initial.month) : months[0] ? ym(months[0].year, months[0].month) : '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [conflict, setConflict] = useState<{ detail: string; conflicts: Conflict[] } | null>(null)
  useEffect(() => {
    if (initial) setValue(ym(initial.year, initial.month))
  }, [initial])

  async function fetchReport(onConflict: 'ask' | 'replace' | 'keep') {
    const [year, month] = value.split('-').map(Number)
    setBusy(true)
    setError(null)
    setConflict(null)
    onStart()
    try {
      const { data } = await api.post<ReportItem>('/reports/fetch', { year, month, on_conflict: onConflict })
      onDone(data)
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 409) {
        setConflict(err.response.data as { detail: string; conflicts: Conflict[] })
      } else setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-2 rounded-xl border border-line bg-card p-4" data-testid="fetch-realization">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-2 font-semibold">Получить через API Ozon</h2>
        <select
          value={value}
          onChange={(e) => setValue(e.target.value)}
          aria-label="Месяц"
          className="rounded-lg border border-line bg-bg px-3 py-1.5 text-sm"
          data-testid="fetch-month"
        >
          {months.map((m) => (
            <option key={ym(m.year, m.month)} value={ym(m.year, m.month)}>
              {m.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={busy || !value}
          onClick={() => fetchReport('ask')}
          className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
          data-testid="fetch-submit"
        >
          {busy ? 'Получаем…' : 'Получить'}
        </button>
      </div>
      <p className="text-xs text-muted">
        Без скачивания файла: Ozon отдаёт отчёт за закончившийся месяц (формирует до 5-го числа следующего), с августа 2023. Ответ Ozon
        сохраняется как есть — отчёт можно перечитать.
      </p>
      <div data-testid="fetch-error">
        <FormError message={error} />
      </div>
      {conflict && (
        <ConflictDialog
          detail={conflict.detail}
          conflicts={conflict.conflicts}
          onCancel={() => setConflict(null)}
          onKeep={() => fetchReport('keep')}
          onReplace={() => fetchReport('replace')}
        />
      )}
    </section>
  )
}

function History({ type, fetchFor }: { type: ReportType; fetchFor: { year: number; month: number } | null }) {
  const [page, setPage] = useState<Page | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [deleted, setDeleted] = useState<ReportItem | { id: string; filename?: undefined } | null>(null)
  const [added, setAdded] = useState<ReportItem | null>(null)

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get('deleted')
    if (id) setDeleted({ id })
  }, [])

  const load = useCallback(async () => {
    setError(null)
    try {
      const { data } = await api.get<Page>('/reports', { params: { report_type: type, limit: PAGE } })
      setPage(data)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [type])
  useEffect(() => {
    setPage(null)
    load()
  }, [load])

  async function more() {
    if (!page) return
    setLoadingMore(true)
    try {
      const { data } = await api.get<Page>('/reports', { params: { report_type: type, limit: PAGE, offset: page.items.length } })
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
      await api.post<ReportItem>(`/reports/${deleted.id}/restore`)
      setDeleted(null)
      const q = new URLSearchParams(window.location.search)
      q.delete('deleted')
      window.history.replaceState(null, '', q.toString() ? `${window.location.pathname}?${q}` : window.location.pathname)
      load()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <div className="space-y-4">
      {type === 'realization' && (
        <FetchRealization
          initial={fetchFor}
          onStart={() => setAdded(null)}
          onDone={(r) => {
            setAdded(r)
            load()
          }}
        />
      )}
      {added && (
        <p className="rounded-lg border border-ok/40 bg-ok/10 px-4 py-2 text-sm" data-testid="fetch-done">
          Отчёт о реализации получен через API: {fmtDay(added.period_start)} – {fmtDay(added.period_end)}.
        </p>
      )}
      {deleted && (
        <div className="flex items-center gap-4 rounded-lg border border-line bg-card px-4 py-2 text-sm" data-testid="undo">
          <span>Отчёт {deleted.filename ? `«${deleted.filename}» ` : ''}удалён.</span>
          <button onClick={undo} className="text-accent hover:underline">
            Отменить
          </button>
          <button onClick={() => setDeleted(null)} className="ml-auto text-faint hover:text-fg" aria-label="Закрыть">
            ×
          </button>
        </div>
      )}
      <FormError message={error} />
      {!page && !error && <p className="text-sm text-muted">Загрузка…</p>}
      {page && page.items.length === 0 && (
        <div className="rounded-xl border border-line bg-card p-6 text-sm text-muted" data-testid="reports-empty">
          {type === 'accrual'
            ? 'Недельных отчётов пока нет. Начисления и так загружаются через API по дням; файл за неделю точнее — загрузите «Отчёт по начислениям» из личного кабинета Ozon (Финансы → Начисления).'
            : 'Месячных отчётов пока нет. Получите отчёт о реализации через API выше или загрузите файл из личного кабинета Ozon (Финансы → Документы → Отчёты о реализации).'}
        </div>
      )}
      {page && page.items.length > 0 && (
        <>
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

// ── документы Ozon файлом ──
type Doc = {
  id: string
  doc_type: string
  title: string
  period_start: string
  period_end: string
  status: 'requested' | 'ready' | 'error'
  state_text: string | null
  requested_at: string
  ready_at: string | null
  error: string | null
  file_name: string | null
  file_size: number | null
}
const DOC_TYPES = [
  { value: 'mutual_settlement', label: 'Отчёт о взаиморасчётах' },
  { value: 'compensation', label: 'Отчёт о компенсациях' },
  { value: 'decompensation', label: 'Отчёт о декомпенсациях' },
]

function Documents() {
  const months = pastMonths(24)
  const [docs, setDocs] = useState<Doc[] | null>(null)
  const [type, setType] = useState(DOC_TYPES[0].value)
  const [month, setMonth] = useState(months[0] ? ym(months[0].year, months[0].month) : '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => {
    try {
      const { data } = await api.get<Doc[]>('/reports/documents')
      setDocs(data)
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])
  useEffect(() => {
    load()
  }, [load])
  // пока Ozon формирует — спрашиваем каждые 5 секунд
  const waiting = docs?.some((d) => d.status === 'requested') ?? false
  useEffect(() => {
    if (!waiting) return
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [waiting, load])

  async function request(docType = type, value = month) {
    const [year, m] = value.split('-').map(Number)
    setBusy(true)
    setError(null)
    try {
      await api.post('/reports/documents', { doc_type: docType, year, month: m })
      await load()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  async function download(d: Doc) {
    try {
      await saveFile(api.get(`/reports/documents/${d.id}/file`, { responseType: 'blob' }), d.file_name ?? 'document.xlsx')
    } catch (err) {
      setError(errorMessage(err, 'Не удалось скачать файл'))
    }
  }

  return (
    <div className="space-y-4">
      <section className="space-y-2 rounded-xl border border-line bg-card p-4" data-testid="doc-request">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="mr-2 font-semibold">Запросить у Ozon</h2>
          <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Документ" className="rounded-lg border border-line bg-bg px-3 py-1.5 text-sm" data-testid="doc-type">
            {DOC_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
          <select value={month} onChange={(e) => setMonth(e.target.value)} aria-label="Месяц" className="rounded-lg border border-line bg-bg px-3 py-1.5 text-sm" data-testid="doc-month">
            {months.map((m) => (
              <option key={ym(m.year, m.month)} value={ym(m.year, m.month)}>
                {m.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            disabled={busy || !month}
            onClick={() => request()}
            className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
            data-testid="doc-submit"
          >
            {busy ? 'Запрашиваем…' : 'Запросить'}
          </button>
        </div>
        <p className="text-xs text-muted">
          Ozon формирует документ минуту-другую и отдаёт файлом — он появится ниже, его можно скачать. Цифры из этих файлов сервис пока не
          разбирает: компенсации и удержания уже есть в начислениях. Акты и УПД за услуги Ozon через API не отдаёт — они в личном кабинете
          (Финансы → Документы).
        </p>
        <FormError message={error} />
      </section>
      {!docs && !error && <p className="text-sm text-muted">Загрузка…</p>}
      {docs && docs.length === 0 && (
        <p className="rounded-xl border border-line bg-card p-6 text-sm text-muted" data-testid="docs-empty">
          Документов пока нет — запросите отчёт о взаиморасчётах или компенсациях за месяц.
        </p>
      )}
      {docs && docs.length > 0 && (
        <ul className="divide-y divide-line rounded-xl border border-line bg-card" data-testid="docs">
          {docs.map((d) => (
            <li key={d.id} className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm" data-testid="doc" data-status={d.status}>
              <div className="min-w-0 flex-1">
                <div className="font-medium">
                  {d.title} · {MONTHS[Number(d.period_start.slice(5, 7)) - 1]} {d.period_start.slice(0, 4)}
                </div>
                <div className="text-xs text-muted">
                  запрошен {fmtDateTime(d.requested_at)}
                  {d.ready_at && ` · готов ${fmtDateTime(d.ready_at)}`}
                  {d.file_size !== null && ` · ${Math.max(1, Math.round(d.file_size / 1024))} КБ`}
                </div>
                {d.error && <div className={`mt-0.5 text-xs ${d.status === 'error' ? 'text-bad' : 'text-warn'}`}>{d.error}</div>}
              </div>
              {d.status === 'requested' && <Badge tone="muted">{d.state_text ?? 'запрошен'}…</Badge>}
              {d.status === 'ready' && (
                <button type="button" onClick={() => download(d)} className="rounded-lg border border-line px-3 py-1.5 hover:border-accent hover:text-accent" data-testid="doc-download">
                  Скачать
                </button>
              )}
              {d.status === 'error' && (
                <button
                  type="button"
                  onClick={() => request(d.doc_type, d.period_start.slice(0, 7))}
                  className="rounded-lg border border-line px-3 py-1.5 hover:border-accent hover:text-accent"
                  data-testid="doc-retry"
                >
                  Запросить заново
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// ── полнота данных ──
type CovWeek = {
  date_from: string
  date_to: string
  days: number
  xlsx: number
  api: number
  missing: number
  state: 'xlsx' | 'api' | 'mixed' | 'partial' | 'none' | 'future'
  missing_ranges: { date_from: string; date_to: string }[]
}
type CovMonth = {
  year: number
  month: number
  title: string
  days: number
  missing: number
  realization: 'have' | 'current' | 'not_yet' | 'missing'
  realization_sources: string[]
  realization_ids: string[]
  weeks: CovWeek[]
}
const WEEK_TEXT: Record<CovWeek['state'], string> = {
  xlsx: 'файл — точно, как в кабинете',
  api: 'через API по дням',
  mixed: 'файл и API',
  partial: 'есть дыры',
  none: 'нет данных',
  future: 'ещё не наступила',
}
const short = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`
const weekDays = (w: CovWeek) => Math.round((Date.parse(w.date_to) - Date.parse(w.date_from)) / 86400000) + 1

function Coverage({ onFetch }: { onFetch: (y: number, m: number) => void }) {
  const [months, setMonths] = useState(6)
  const [data, setData] = useState<CovMonth[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api
      .get<{ months: CovMonth[] }>('/reports/coverage', { params: { months } })
      .then(({ data }) => setData(data.months))
      .catch((err) => setError(errorMessage(err)))
  }, [months])
  if (error) return <FormError message={error} />
  if (!data) return <p className="text-sm text-muted">Загрузка…</p>
  return (
    <div className="space-y-4" data-testid="coverage">
      <p className="text-sm text-muted">
        Чем закрыт каждый день: отчётом по начислениям (файл — сходится с кабинетом до копейки) или начислениями через API по дням. Дыра —
        день без данных: P&amp;L и аналитика за него неполные. За месяц — ещё отчёт о реализации.
      </p>
      <div className="flex flex-wrap gap-3 text-xs text-muted">
        <span className="flex items-center gap-1.5"><span className="inline-block h-2.5 w-2.5 rounded-sm bg-ok/70" /> файл</span>
        <span className="flex items-center gap-1.5"><span className="inline-block h-2.5 w-2.5 rounded-sm bg-accent/70" /> API</span>
        <span className="flex items-center gap-1.5"><span className="inline-block h-2.5 w-2.5 rounded-sm bg-bad/50" /> нет данных</span>
      </div>
      {data.map((m) => (
        <section key={`${m.year}-${m.month}`} className="rounded-xl border border-line bg-card" data-testid="coverage-month">
          <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
            <h2 className="mr-auto font-semibold">{m.title}</h2>
            {m.missing > 0 && <Badge tone="bad">нет данных за {m.missing} дн.</Badge>}
            <RealizationState m={m} onFetch={onFetch} />
          </div>
          <ul className="divide-y divide-line">
            {m.weeks.map((w) => (
              <li key={w.date_from} className="grid grid-cols-[6.5rem_minmax(0,1fr)] items-center gap-x-3 gap-y-1 px-4 py-2 text-sm sm:grid-cols-[7rem_minmax(0,1fr)_14rem]" data-testid="coverage-week" data-state={w.state}>
                <span className="tabular-nums text-muted">
                  {short(w.date_from)}–{short(w.date_to)}
                </span>
                {/* доли — от всех дней недели: ещё не наступившие остаются серыми */}
                <span className="flex h-2.5 overflow-hidden rounded-full bg-line">
                  <span className="bg-ok/70" style={{ width: `${(w.xlsx / weekDays(w)) * 100}%` }} />
                  <span className="bg-accent/70" style={{ width: `${(w.api / weekDays(w)) * 100}%` }} />
                  <span className="bg-bad/50" style={{ width: `${(w.missing / weekDays(w)) * 100}%` }} />
                </span>
                <span className={`col-span-2 text-xs sm:col-span-1 ${w.state === 'partial' || w.state === 'none' ? 'text-bad' : 'text-muted'}`}>
                  {WEEK_TEXT[w.state]}
                  {w.missing > 0 && `: нет ${w.missing_ranges.map((r) => (r.date_from === r.date_to ? short(r.date_from) : `${short(r.date_from)}–${short(r.date_to)}`)).join(', ')}`}
                </span>
              </li>
            ))}
          </ul>
          {m.missing > 0 && (
            <p className="border-t border-line px-4 py-2 text-xs text-muted">
              Закрыть дыры:{' '}
              <Link href="/dashboard/analytics/reports/upload" className="text-accent hover:underline">
                загрузить отчёт по начислениям за эти дни
              </Link>{' '}
              или{' '}
              <Link href="/dashboard?sync=1" className="text-accent hover:underline">
                обновить начисления из Ozon
              </Link>
              .
            </p>
          )}
        </section>
      ))}
      {months < 24 && (
        <button type="button" onClick={() => setMonths(months === 6 ? 12 : 24)} className="w-full rounded-xl border border-line bg-card py-2 text-sm text-accent hover:border-accent" data-testid="coverage-more">
          Показать за {months === 6 ? 'год' : 'два года'}
        </button>
      )}
    </div>
  )
}

function RealizationState({ m, onFetch }: { m: CovMonth; onFetch: (y: number, m: number) => void }) {
  if (m.realization === 'have')
    return (
      <Link href={`/dashboard/analytics/reports/${m.realization_ids[0]}`} className="text-xs text-ok hover:underline" data-testid="coverage-realization">
        Отчёт о реализации есть ({m.realization_sources.map((s) => (s === 'api' ? 'API' : 'файл')).join(', ')}) →
      </Link>
    )
  if (m.realization === 'current') return <span className="text-xs text-faint">Отчёт о реализации — когда месяц закончится</span>
  if (m.realization === 'not_yet') return <span className="text-xs text-faint">Отчёт о реализации Ozon формирует до 5-го числа</span>
  return (
    <span className="flex flex-wrap items-center gap-2 text-xs" data-testid="coverage-realization">
      <span className="text-warn">Отчёта о реализации нет</span>
      <button type="button" onClick={() => onFetch(m.year, m.month)} className="text-accent hover:underline" data-testid="coverage-fetch">
        Получить через API
      </button>
      <Link href="/dashboard/analytics/reports/upload" className="text-accent hover:underline">
        Загрузить файл
      </Link>
    </span>
  )
}

// ── какие отчёты бывают ──
type Kind = {
  key: string
  title: string
  what: string
  cabinet: string
  how: string[]
  how_titles: string[]
  api: string[]
  gives: string | null
  used_for: string
  finance: boolean
  link: string | null
}
const HOW_TONE: Record<string, 'ok' | 'accent' | 'muted' | 'warn'> = { auto: 'ok', button: 'accent', upload: 'accent', not_used: 'muted', cabinet: 'warn' }

function Catalog({ scope, onTab }: { scope: 'analytics' | 'finance'; onTab: (t: Tab) => void }) {
  const [kinds, setKinds] = useState<Kind[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api
      .get<Kind[]>('/reports/catalog', { params: { scope } })
      .then(({ data }) => setKinds(data))
      .catch((err) => setError(errorMessage(err)))
  }, [scope])
  if (error) return <FormError message={error} />
  if (!kinds) return <p className="text-sm text-muted">Загрузка…</p>
  const action = (k: Kind) => {
    if (k.key === 'realization') return { label: 'Получить или загрузить', tab: 'realization' as Tab }
    if (k.how.includes('button')) return { label: 'Запросить файл', tab: 'documents' as Tab }
    return null
  }
  return (
    <ul className="space-y-3" data-testid="catalog">
      {kinds.map((k) => {
        const act = action(k)
        return (
          <li key={k.key} className="rounded-xl border border-line bg-card p-4 text-sm" data-testid="catalog-kind" data-key={k.key}>
            <div className="flex flex-wrap items-start gap-2">
              <h2 className="mr-auto font-semibold">{k.title}</h2>
              {k.how_titles.map((t, i) => (
                <Badge key={t} tone={HOW_TONE[k.how[i]] ?? 'muted'}>
                  {t}
                </Badge>
              ))}
            </div>
            <p className="mt-1 text-muted">{k.what}</p>
            <p className="mt-1 text-xs text-faint">В кабинете Ozon: {k.cabinet}</p>
            <p className="mt-2">{k.used_for}</p>
            {k.api.length > 0 && <p className="mt-1 break-words text-xs text-faint">Методы Ozon API: {k.api.join(', ')}</p>}
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
              {act && (
                <button type="button" onClick={() => onTab(act.tab)} className="text-accent hover:underline" data-testid="catalog-action">
                  {act.label} →
                </button>
              )}
              {k.how.includes('upload') && (
                <Link href="/dashboard/analytics/reports/upload" className="text-accent hover:underline">
                  Загрузить файл →
                </Link>
              )}
              {k.link && (
                <Link href={k.link} className="text-accent hover:underline">
                  Где это в сервисе →
                </Link>
              )}
            </div>
          </li>
        )
      })}
    </ul>
  )
}

/** Страница отчётов: вкладки, история, документы, полнота, каталог. scope finance — «Финансы → Отчёты и документы». */
export function ReportsPage({ scope, header }: { scope: 'analytics' | 'finance'; header: React.ReactNode }) {
  const [tab, setTab] = useState<Tab | null>(null)
  const nav = useRef<HTMLElement>(null)
  // на телефоне вкладки прокручиваются — текущая должна быть видна
  useEffect(() => {
    const el = nav.current?.querySelector<HTMLElement>('[aria-current=page]')
    if (nav.current && el) nav.current.scrollLeft = el.offsetLeft - (nav.current.clientWidth - el.offsetWidth) / 2
  }, [tab])
  const [fetchFor, setFetchFor] = useState<{ year: number; month: number } | null>(null)
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get('tab') as Tab | null
    setTab(TABS.some((x) => x.tab === t) ? (t as Tab) : 'accrual')
  }, [])
  function go(next: Tab) {
    setTab(next)
    const q = new URLSearchParams(window.location.search)
    if (next === 'accrual') q.delete('tab')
    else q.set('tab', next)
    q.delete('deleted')
    window.history.replaceState(null, '', q.toString() ? `${window.location.pathname}?${q}` : window.location.pathname)
  }
  return (
    <div className="space-y-6">
      {header}
      <nav ref={nav} className="flex gap-1 overflow-x-auto border-b border-line" aria-label="Отчёты" data-testid="reports-tabs">
        {TABS.map((t) => (
          <button
            key={t.tab}
            type="button"
            onClick={() => go(t.tab)}
            aria-current={tab === t.tab ? 'page' : undefined}
            className={`-mb-px whitespace-nowrap border-b-2 px-4 py-2 text-sm ${tab === t.tab ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-fg'}`}
            data-testid={`reports-tab-${t.tab}`}
          >
            {t.label}
          </button>
        ))}
      </nav>
      {(tab === 'accrual' || tab === 'realization') && <History key={tab} type={tab} fetchFor={tab === 'realization' ? fetchFor : null} />}
      {tab === 'documents' && <Documents />}
      {tab === 'coverage' && (
        <Coverage
          onFetch={(year, month) => {
            setFetchFor({ year, month })
            go('realization')
          }}
        />
      )}
      {tab === 'catalog' && <Catalog scope={scope} onTab={go} />}
    </div>
  )
}

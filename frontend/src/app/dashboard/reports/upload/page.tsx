'use client'

import axios from 'axios'
import Link from 'next/link'
import { useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import {
  ByTypeTable,
  type Conflict,
  MissingCostTable,
  ReportFigures,
  ReportHeading,
  type ReportDetail,
  Warnings,
  fmtDateTime,
  periodLabel,
} from '@/components/reports'
import { api, errorMessage } from '@/lib/api'

const MAX_MB = 10 // как REPORT_MAX_SIZE_MB на бэкенде

type ConflictState = { file: File; detail: string; conflicts: Conflict[] }

function checkFile(file: File): string | null {
  const name = file.name.toLowerCase()
  if (name.endsWith('.xls')) return 'Файл в старом формате Excel (.xls). Откройте его в Excel и сохраните как .xlsx.'
  if (!name.endsWith('.xlsx')) return 'Поддерживаются только файлы .xlsx — отчёты из личного кабинета Ozon в формате Excel.'
  if (file.size > MAX_MB * 1024 * 1024) return `Файл больше ${MAX_MB} МБ — это не похоже на отчёт Ozon.`
  return null
}

export default function UploadReportPage() {
  const input = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)
  const [progress, setProgress] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [conflict, setConflict] = useState<ConflictState | null>(null)
  const [report, setReport] = useState<ReportDetail | null>(null)

  async function send(file: File, onConflict: 'ask' | 'replace' | 'keep' = 'ask') {
    const problem = checkFile(file)
    if (problem) {
      setError(problem)
      return
    }
    setError(null)
    setConflict(null)
    setReport(null)
    setProgress(0)
    const form = new FormData()
    form.append('file', file)
    try {
      const { data } = await api.post<ReportDetail>('/reports/upload', form, {
        params: { on_conflict: onConflict },
        onUploadProgress: (e) => setProgress(e.total ? Math.round((e.loaded / e.total) * 100) : 0),
      })
      setReport(data)
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 409) {
        const body = err.response.data as { detail: string; conflicts: Conflict[] }
        setConflict({ file, detail: body.detail, conflicts: body.conflicts })
      } else {
        setError(errorMessage(err))
      }
    } finally {
      setProgress(null)
      if (input.current) input.current.value = ''
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files[0]
    if (file) send(file)
  }

  const busy = progress !== null

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="flex flex-wrap items-center gap-4">
        <h1 className="text-2xl font-semibold">Загрузка отчёта</h1>
        <Link href="/dashboard/reports" className="ml-auto text-sm text-slate-400 hover:text-slate-200">
          ← Все отчёты
        </Link>
      </div>

      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        data-testid="dropzone"
        className={`rounded-xl border-2 border-dashed p-10 text-center transition-colors ${
          dragOver ? 'border-accent bg-accent/5' : 'border-line bg-card'
        }`}
      >
        <p className="text-lg">Перетащите сюда XLSX-отчёт Ozon</p>
        <p className="mt-1 text-sm text-slate-400">
          «Отчёт по начислениям» (Финансы → Отчёт по начислениям) или «Отчёт о реализации» (Финансы → Отчёт о
          реализации), до {MAX_MB} МБ
        </p>
        <button
          type="button"
          onClick={() => input.current?.click()}
          disabled={busy}
          className="mt-4 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
        >
          Выбрать файл
        </button>
        <input
          ref={input}
          type="file"
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          className="hidden"
          data-testid="file-input"
          onChange={(e) => e.target.files?.[0] && send(e.target.files[0])}
        />
        {busy && (
          <div className="mx-auto mt-6 max-w-sm">
            <div className="h-2 overflow-hidden rounded bg-line">
              <div className="h-full bg-accent transition-all" style={{ width: `${progress}%` }} />
            </div>
            <p className="mt-2 text-sm text-slate-400">{progress! < 100 ? `Загрузка… ${progress}%` : 'Разбираем отчёт…'}</p>
          </div>
        )}
      </div>

      <FormError message={error} />

      {conflict && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/60 p-4" role="dialog" aria-modal="true">
          <div className="w-full max-w-lg rounded-xl border border-line bg-card p-6" data-testid="conflict-dialog">
            <h2 className="text-lg font-semibold">Отчёт за этот период уже есть</h2>
            <p className="mt-2 text-sm text-slate-300">{conflict.detail}</p>
            <ul className="mt-3 space-y-1 text-sm text-slate-400">
              {conflict.conflicts.map((c) => (
                <li key={c.id}>
                  {c.filename} · {periodLabel(c)} · загружен {fmtDateTime(c.uploaded_at)}
                  {c.same_file && ' · тот же файл'}
                </li>
              ))}
            </ul>
            <p className="mt-3 text-xs text-slate-500">
              Заменённый отчёт пропадёт из списка и расчётов, но не удалится — его можно будет вернуть.
            </p>
            <div className="mt-5 flex flex-wrap justify-end gap-3">
              <button onClick={() => setConflict(null)} className="rounded-lg border border-line px-4 py-2 text-sm hover:border-slate-400">
                Отмена
              </button>
              <button onClick={() => send(conflict.file, 'keep')} className="rounded-lg border border-line px-4 py-2 text-sm hover:border-accent hover:text-accent">
                Оставить оба
              </button>
              <button onClick={() => send(conflict.file, 'replace')} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90">
                Заменить
              </button>
            </div>
          </div>
        </div>
      )}

      {report && (
        <div className="space-y-4" data-testid="preview">
          <div className="flex flex-wrap items-start gap-4">
            <ReportHeading report={report} />
            <div className="ml-auto flex gap-3">
              <button
                onClick={() => {
                  setReport(null)
                  input.current?.click()
                }}
                className="rounded-lg border border-line px-4 py-2 text-sm hover:border-accent hover:text-accent"
              >
                Загрузить ещё
              </button>
              <Link href={`/dashboard/reports/${report.id}`} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90">
                Открыть отчёт
              </Link>
            </div>
          </div>
          <ReportFigures report={report} />
          <p className="text-xs text-slate-500">Остаток, налог и маржа появятся в отчёте с расчётом P&amp;L.</p>
          <MissingCostTable items={report.summary.missing_cost} />
          <Warnings items={report.summary.warnings} />
          <ByTypeTable byType={report.summary.by_type} />
        </div>
      )}
    </div>
  )
}

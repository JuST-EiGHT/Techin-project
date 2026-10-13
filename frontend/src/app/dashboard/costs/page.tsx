'use client'

import { History, Upload } from 'lucide-react'
import { Fragment, useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { Badge, fmtDateTime, fmtDay, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'

type CostRow = {
  offer_id: string
  name: string | null
  sku: string | null
  cost_price: string | null
  vat_multiplier: string | null
  effective_from: string | null
  note: string | null
  updated_at: string | null
  history_count: number
  in_catalog: boolean
  report_qty: number
}
type CostsPage = { items: CostRow[]; total: number; missing_count: number }
type CostRecord = { cost_price: string; vat_multiplier: string | null; effective_from: string; note: string | null; source: string; created_at: string }
type ImportRow = {
  line: number
  offer_id: string
  cost_price: string | null
  vat_multiplier: string | null
  note: string | null
  effective_from: string | null
  status: 'new' | 'changed' | 'unchanged' | 'error'
  current_cost: string | null
  message: string | null
}
type ImportResult = {
  applied: boolean
  counts: { new: number; changed: number; unchanged: number; error: number }
  recosted_reports: number
  rows: ImportRow[]
}

const BEGINNING = '2000-01-01' // первая цена без даты — «с начала учёта»
const SOURCE_LABEL: Record<string, string> = { manual: 'вручную', import: 'импорт', seed: 'начальная загрузка' }
const STATUS: Record<ImportRow['status'], { label: string; tone: 'accent' | 'warn' | 'muted' | 'bad' }> = {
  new: { label: 'Новая', tone: 'accent' },
  changed: { label: 'Изменится', tone: 'warn' },
  unchanged: { label: 'Без изменений', tone: 'muted' },
  error: { label: 'Ошибка', tone: 'bad' },
}
const TEMPLATE = 'offer_id;cost_price;vat_multiplier;note;effective_from\nАРТИКУЛ-1;1250,50;1,22;поставка;\n'

const since = (d: string | null) => (d === null ? '—' : d === BEGINNING ? 'с начала учёта' : `с ${fmtDay(d)}`)
const toNumber = (text: string) => {
  const n = Number(text.replace(/\s/g, '').replace(',', '.'))
  return text.trim() && Number.isFinite(n) ? n : null
}
const vatText = (v: string | null) => (v === null ? 'как в настройках' : Number(v).toLocaleString('ru-RU'))

function downloadTemplate() {
  const url = URL.createObjectURL(new Blob(['﻿' + TEMPLATE], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = 'себестоимость_шаблон.csv'
  a.click()
  URL.revokeObjectURL(url)
}

function HistoryRows({ offerId }: { offerId: string }) {
  const [records, setRecords] = useState<CostRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api
      .get<CostRecord[]>(`/costs/${offerId.split('/').map(encodeURIComponent).join('/')}/history`)
      .then((r) => setRecords(r.data))
      .catch((err) => setError(errorMessage(err)))
  }, [offerId])
  return (
    <tr className="border-b border-line bg-bg/40">
      <td colSpan={7} className="px-4 py-3">
        <FormError message={error} />
        {!records && !error && <p className="text-sm text-muted">Загрузка…</p>}
        {records && (
          <table className="text-sm" data-testid="cost-history">
            <thead className="text-left text-xs text-faint">
              <tr>
                <th className="pr-6 font-normal">Действует</th>
                <th className="pr-6 text-right font-normal">Цена</th>
                <th className="pr-6 font-normal">НДС</th>
                <th className="pr-6 font-normal">Комментарий</th>
                <th className="font-normal">Записано</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r, i) => (
                <tr key={i} className={i === 0 ? '' : 'text-muted'}>
                  <td className="pr-6">{since(r.effective_from)}</td>
                  <td className="pr-6 text-right tabular-nums">{rub(r.cost_price)}</td>
                  <td className="pr-6">{vatText(r.vat_multiplier)}</td>
                  <td className="pr-6">{r.note || '—'}</td>
                  <td>
                    {fmtDateTime(r.created_at)} · {SOURCE_LABEL[r.source] ?? r.source}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </td>
    </tr>
  )
}

function EditRow({ row, onSaved, onCancel }: { row: CostRow; onSaved: (row: CostRow, recosted: number) => void; onCancel: () => void }) {
  const [price, setPrice] = useState(row.cost_price ? String(Number(row.cost_price)) : '')
  const [vat, setVat] = useState(row.vat_multiplier ? String(Number(row.vat_multiplier)) : '')
  const [note, setNote] = useState(row.note ?? '')
  const [from, setFrom] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function save() {
    setError(null)
    const p = toNumber(price)
    if (p === null || p <= 0) return setError('Себестоимость: число больше 0')
    const v = vat.trim() ? toNumber(vat) : null
    if (vat.trim() && (v === null || v < 1 || v > 2)) return setError('Коэффициент НДС: от 1 до 2, например 1,22')
    setBusy(true)
    try {
      const { data } = await api.put<{ item: CostRow; recosted_reports: number }>(
        `/costs/${row.offer_id.split('/').map(encodeURIComponent).join('/')}`,
        { cost_price: p.toFixed(2), vat_multiplier: v === null ? null : v.toFixed(3), note: note.trim() || null, effective_from: from || null },
      )
      onSaved(data.item, data.recosted_reports)
    } catch (err) {
      setError(errorMessage(err))
      setBusy(false)
    }
  }

  const keys = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') save()
    if (e.key === 'Escape') onCancel()
  }
  const input = 'rounded-lg border border-line bg-bg px-2 py-1 outline-none focus:border-accent'
  return (
    <tr className="border-b border-line bg-accent/5" data-testid="cost-edit">
      <td className="px-4 py-2">
        <div className="font-medium">{row.offer_id}</div>
        {row.name && <div className="max-w-xs truncate text-xs text-muted">{row.name}</div>}
      </td>
      <td className="px-4 py-2 text-right">
        <input autoFocus aria-label="Себестоимость" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} onKeyDown={keys} className={`${input} w-28 text-right`} />
      </td>
      <td className="px-4 py-2">
        <input aria-label="Коэффициент НДС" inputMode="decimal" placeholder="как в настройках" value={vat} onChange={(e) => setVat(e.target.value)} onKeyDown={keys} className={`${input} w-36`} />
      </td>
      <td className="px-4 py-2">
        <input aria-label="Действует с" type="date" value={from} onChange={(e) => setFrom(e.target.value)} onKeyDown={keys} className={`${input} w-36`} />
        <div className="mt-1 text-xs text-faint">{row.cost_price === null ? 'пусто — с начала учёта' : 'пусто — с сегодня'}</div>
      </td>
      <td className="px-4 py-2" colSpan={2}>
        <input aria-label="Комментарий" value={note} maxLength={255} onChange={(e) => setNote(e.target.value)} onKeyDown={keys} className={`${input} w-full`} />
        {error && <div className="mt-1 text-xs text-bad">{error}</div>}
      </td>
      <td className="whitespace-nowrap px-4 py-2 text-right">
        <button onClick={save} disabled={busy} className="rounded-lg bg-accent px-3 py-1 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60">
          {busy ? '…' : 'Сохранить'}
        </button>
        <button onClick={onCancel} className="ml-2 text-sm text-muted hover:text-fg">
          Отмена
        </button>
      </td>
    </tr>
  )
}

function ImportPanel({ onDone, onClose }: { onDone: (r: ImportResult) => void; onClose: () => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<ImportResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function send(f: File, apply: boolean) {
    setError(null)
    setBusy(true)
    const form = new FormData()
    form.append('file', f)
    try {
      const { data } = await api.post<ImportResult>('/costs/import', form, { params: { dry_run: !apply } })
      if (apply) onDone(data)
      else setPreview(data)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const rows = preview ? [...preview.rows].sort((a, b) => ['error', 'changed', 'new', 'unchanged'].indexOf(a.status) - ['error', 'changed', 'new', 'unchanged'].indexOf(b.status)) : []
  const toApply = preview ? preview.counts.new + preview.counts.changed : 0
  return (
    <section className="space-y-4 rounded-xl border border-accent/40 bg-card p-5" data-testid="import-panel">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">Импорт из файла</h2>
        <button onClick={downloadTemplate} className="text-sm text-accent hover:underline">
          Скачать шаблон CSV
        </button>
        <button onClick={onClose} className="ml-auto text-sm text-muted hover:text-fg">
          Закрыть
        </button>
      </div>
      <p className="text-sm text-muted">
        CSV или XLSX с колонками: артикул, себестоимость без НДС; по желанию — коэффициент НДС, комментарий, «действует с».
        Сначала покажем, что изменится, — применится только после подтверждения.
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <button onClick={() => input.current?.click()} disabled={busy} className="rounded-lg border border-line px-4 py-2 text-sm hover:border-accent hover:text-accent disabled:opacity-60">
          {file ? 'Выбрать другой файл' : 'Выбрать файл'}
        </button>
        {file && <span className="text-sm text-muted">{file.name}</span>}
        <input
          ref={input}
          type="file"
          accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          className="hidden"
          data-testid="import-input"
          onChange={(e) => {
            const f = e.target.files?.[0]
            e.target.value = ''
            if (!f) return
            setFile(f)
            setPreview(null)
            send(f, false)
          }}
        />
      </div>
      <FormError message={error} />
      {busy && !preview && <p className="text-sm text-muted">Читаем файл…</p>}
      {preview && (
        <>
          <div className="flex flex-wrap gap-2 text-sm" data-testid="import-counts">
            <Badge tone="accent">Новых: {preview.counts.new}</Badge>
            <Badge tone="warn">Изменится: {preview.counts.changed}</Badge>
            <Badge tone="muted">Без изменений: {preview.counts.unchanged}</Badge>
            <Badge tone="bad">Ошибок: {preview.counts.error}</Badge>
          </div>
          <div className="max-h-80 overflow-auto rounded-lg border border-line">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-card text-left text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="px-3 py-2 font-normal">Строка</th>
                  <th className="px-3 py-2 font-normal">Артикул</th>
                  <th className="px-3 py-2 text-right font-normal">Сейчас</th>
                  <th className="px-3 py-2 text-right font-normal">Будет</th>
                  <th className="px-3 py-2 font-normal">Статус</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.line} className="border-b border-line last:border-b-0" data-testid="import-row">
                    <td className="px-3 py-1.5 text-faint">{r.line}</td>
                    <td className="px-3 py-1.5">{r.offer_id || '—'}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums text-muted">{rub(r.current_cost)}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">{rub(r.cost_price)}</td>
                    <td className="px-3 py-1.5">
                      <Badge tone={STATUS[r.status].tone}>{STATUS[r.status].label}</Badge>
                      {r.message && <span className="ml-2 text-xs text-bad">{r.message}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={() => file && send(file, true)}
              disabled={busy || toApply === 0}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
            >
              {busy ? 'Применяем…' : `Применить (${toApply})`}
            </button>
            {preview.counts.error > 0 && <span className="text-sm text-muted">Строки с ошибками будут пропущены.</span>}
          </div>
        </>
      )}
    </section>
  )
}

export default function CostsPage() {
  const [page, setPage] = useState<CostsPage | null>(null)
  const [missingOnly, setMissingOnly] = useState(false)
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [historyOf, setHistoryOf] = useState<string | null>(null)
  const [importing, setImporting] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  // ?missing=1 — со страницы отчёта «Указать себестоимость»
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('missing') === '1') setMissingOnly(true)
  }, [])
  // поиск — с задержкой 300 мс
  useEffect(() => {
    const t = setTimeout(() => setQuery(search.trim()), 300)
    return () => clearTimeout(t)
  }, [search])

  const load = useCallback(async () => {
    setError(null)
    try {
      const { data } = await api.get<CostsPage>('/costs', { params: { missing: missingOnly, search: query || undefined } })
      setPage(data)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [missingOnly, query])
  useEffect(() => {
    load()
  }, [load])

  function saved(row: CostRow, recosted: number) {
    setEditing(null)
    setNotice(`Себестоимость ${row.offer_id} сохранена${recosted ? `; пересчитано отчётов: ${recosted}` : ''}.`)
    load()
  }

  function imported(r: ImportResult) {
    setImporting(false)
    setNotice(
      `Добавлено: ${r.counts.new}, обновлено: ${r.counts.changed}, без изменений: ${r.counts.unchanged}, ошибок: ${r.counts.error}` +
        (r.recosted_reports ? `; пересчитано отчётов: ${r.recosted_reports}.` : '.'),
    )
    load()
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Себестоимость</h1>
          <p className="text-sm text-muted">Цена закупки за единицу без НДС. Новая цена — новая запись в истории: прошлые отчёты не меняются.</p>
        </div>
        <button onClick={() => setImporting(true)} className="ml-auto flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90">
          <Upload className="h-4 w-4" />
          Импорт из файла
        </button>
      </div>

      {importing && <ImportPanel onDone={imported} onClose={() => setImporting(false)} />}
      {notice && (
        <div className="flex items-center gap-3 rounded-lg border border-ok/40 bg-ok/10 px-4 py-2 text-sm text-ok" role="status">
          {notice}
          <button onClick={() => setNotice(null)} className="ml-auto" aria-label="Закрыть">
            ×
          </button>
        </div>
      )}
      <FormError message={error} />

      {page && page.missing_count > 0 && !missingOnly && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-warn/40 bg-warn/10 px-4 py-2 text-sm text-warn">
          Себестоимость не задана для {page.missing_count} артикулов — прибыль по ним завышена.
          <button onClick={() => setMissingOnly(true)} className="underline">
            Показать
          </button>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-2 border-b border-line">
          {[
            { value: false, label: 'Все' },
            { value: true, label: `Без себестоимости${page ? ` (${page.missing_count})` : ''}` },
          ].map((t) => (
            <button
              key={String(t.value)}
              onClick={() => setMissingOnly(t.value)}
              className={`-mb-px border-b-2 px-3 py-2 text-sm ${missingOnly === t.value ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-fg'}`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <input
          type="search"
          placeholder="Артикул или название"
          aria-label="Поиск"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="ml-auto w-64 rounded-lg border border-line bg-bg px-3 py-1.5 text-sm outline-none focus:border-accent"
        />
      </div>

      {!page && !error && <p className="text-sm text-muted">Загрузка…</p>}
      {page && page.items.length === 0 && (
        <div className="rounded-xl border border-line bg-card p-6 text-sm text-muted">
          {missingOnly
            ? 'Все артикулы, которые встречались в отчётах и каталоге, с себестоимостью.'
            : query
              ? 'Ничего не нашлось.'
              : 'Артикулов пока нет. Импортируйте файл с себестоимостью или загрузите отчёт Ozon — его артикулы появятся здесь.'}
        </div>
      )}
      {page && page.items.length > 0 && (
        <section className="overflow-x-auto rounded-xl border border-line bg-card">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr className="border-b border-line">
                <th className="px-4 py-2 font-normal">Артикул</th>
                <th className="px-4 py-2 text-right font-normal">Себестоимость без НДС</th>
                <th className="px-4 py-2 font-normal">НДС</th>
                <th className="px-4 py-2 font-normal">Действует</th>
                <th className="px-4 py-2 font-normal">Комментарий</th>
                <th className="px-4 py-2 text-right font-normal">Продано в отчётах</th>
                <th className="px-4 py-2 font-normal" />
              </tr>
            </thead>
            <tbody>
              {page.items.map((row) =>
                editing === row.offer_id ? (
                  <EditRow key={row.offer_id} row={row} onSaved={saved} onCancel={() => setEditing(null)} />
                ) : (
                  <Fragment key={row.offer_id}>
                    <tr className="border-b border-line last:border-b-0" data-testid="cost-row">
                      <td className="px-4 py-2">
                        <div className="font-medium">{row.offer_id}</div>
                        {row.name && <div className="max-w-xs truncate text-xs text-muted">{row.name}</div>}
                      </td>
                      <td
                        className="cursor-pointer px-4 py-2 text-right tabular-nums hover:bg-line/40"
                        title="Нажмите, чтобы изменить"
                        onClick={() => setEditing(row.offer_id)}
                      >
                        {row.cost_price === null ? <span className="text-warn">не задана</span> : rub(row.cost_price)}
                      </td>
                      <td className="px-4 py-2 text-muted">{row.cost_price === null ? '—' : vatText(row.vat_multiplier)}</td>
                      <td className="whitespace-nowrap px-4 py-2 text-muted">{since(row.effective_from)}</td>
                      <td className="max-w-xs truncate px-4 py-2 text-muted">{row.note || '—'}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{row.report_qty || '—'}</td>
                      <td className="whitespace-nowrap px-4 py-2 text-right">
                        <button onClick={() => setEditing(row.offer_id)} className="text-sm text-accent hover:underline">
                          {row.cost_price === null ? 'Указать' : 'Изменить'}
                        </button>
                        {row.history_count > 0 && (
                          <button
                            onClick={() => setHistoryOf(historyOf === row.offer_id ? null : row.offer_id)}
                            className="ml-3 inline-flex items-center gap-1 text-sm text-muted hover:text-fg"
                            title="История цен"
                          >
                            <History className="h-4 w-4" />
                            {row.history_count}
                          </button>
                        )}
                      </td>
                    </tr>
                    {historyOf === row.offer_id && <HistoryRows offerId={row.offer_id} />}
                  </Fragment>
                ),
              )}
            </tbody>
          </table>
        </section>
      )}
    </div>
  )
}

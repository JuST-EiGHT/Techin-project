'use client'

import { Download, History, Upload } from 'lucide-react'
import { Fragment, useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { CostTemplateButton } from '@/components/CostTemplateButton'
import { Badge, fmtDateTime, fmtDay, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { downloadCostErrors, type TemplateFormat } from '@/lib/download'

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
type CostsPage = { items: CostRow[]; total: number; all_count: number; missing_count: number }
type CostRecord = { cost_price: string; vat_multiplier: string | null; effective_from: string; note: string | null; source: string; created_at: string }
type ImportStatus = 'new' | 'changed' | 'unchanged' | 'skipped' | 'error'
type ImportRow = {
  line: number
  offer_id: string
  cost_price: string | null
  cost_with_vat: string | null
  vat_multiplier: string | null
  note: string | null
  effective_from: string | null
  status: ImportStatus
  current_cost: string | null
  message: string | null
}
type ImportResult = {
  applied: boolean
  counts: Record<ImportStatus, number>
  recosted_reports: number
  rows: ImportRow[]
}

const BEGINNING = '2000-01-01' // первая цена без даты — «с начала учёта»
const SOURCE_LABEL: Record<string, string> = { manual: 'вручную', import: 'из файла' }
const STATUS: Record<ImportStatus, { label: string; tone: 'accent' | 'warn' | 'muted' | 'bad' }> = {
  error: { label: 'Ошибка', tone: 'bad' },
  changed: { label: 'Изменится', tone: 'warn' },
  new: { label: 'Новая', tone: 'accent' },
  skipped: { label: 'Пропущено', tone: 'muted' },
  unchanged: { label: 'Без изменений', tone: 'muted' },
}
const ORDER = Object.keys(STATUS) as ImportStatus[] // ошибки и изменения — наверху
// ставка НДС поставщика → коэффициент (как в шаблоне); пусто — как в «Основных настройках»
const VAT_OPTIONS = [
  { value: '1.220', label: '22 %' },
  { value: '1.200', label: '20 %' },
  { value: '1.100', label: '10 %' },
  { value: '1.070', label: '7 %' },
  { value: '1.050', label: '5 %' },
  { value: '1.000', label: 'без НДС' },
]

const since = (d: string | null) => (d === null ? '—' : d === BEGINNING ? 'с начала учёта' : `с ${fmtDay(d)}`)
const toNumber = (text: string) => {
  const n = Number(text.replace(/\s/g, '').replace(',', '.'))
  return text.trim() && Number.isFinite(n) ? n : null
}
function vatText(v: string | null): string {
  if (v === null) return 'как в настройках'
  const pct = Math.round((Number(v) - 1) * 1000) / 10
  return pct === 0 ? 'без НДС' : `${pct.toLocaleString('ru-RU')} %`
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
  const [vat, setVat] = useState(row.vat_multiplier ? Number(row.vat_multiplier).toFixed(3) : '')
  const [note, setNote] = useState(row.note ?? '')
  const [from, setFrom] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function save() {
    setError(null)
    const p = toNumber(price)
    if (p === null || p <= 0) return setError('Себестоимость: число больше 0')
    setBusy(true)
    try {
      const { data } = await api.put<{ item: CostRow; recosted_reports: number }>(
        `/costs/${row.offer_id.split('/').map(encodeURIComponent).join('/')}`,
        { cost_price: p.toFixed(2), vat_multiplier: vat || null, note: note.trim() || null, effective_from: from || null },
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
        <select aria-label="Ставка НДС" value={vat} onChange={(e) => setVat(e.target.value)} onKeyDown={keys} className={`${input} w-40`}>
          <option value="">как в настройках</option>
          {vat && !VAT_OPTIONS.some((o) => o.value === vat) && <option value={vat}>{vatText(vat)}</option>}
          {VAT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
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

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <li className="space-y-3 rounded-lg border border-line bg-bg/40 p-4">
      <div className="flex items-center gap-2 font-medium">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-accent/15 text-sm text-accent">{n}</span>
        {title}
      </div>
      {children}
    </li>
  )
}

function ImportPanel({ allCount, missingCount, onDone, onClose }: { allCount: number; missingCount: number; onDone: (r: ImportResult) => void; onClose: () => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [format, setFormat] = useState<TemplateFormat>('xlsx')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<ImportResult | null>(null)
  const [showAll, setShowAll] = useState(false)
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

  async function errorsFile() {
    if (!file) return
    setError(null)
    try {
      await downloadCostErrors(file)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const sorted = preview ? [...preview.rows].sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status)) : []
  const actionable = sorted.filter((r) => ['error', 'changed', 'new'].includes(r.status))
  const rows = showAll ? sorted : actionable
  const toApply = preview ? preview.counts.new + preview.counts.changed : 0
  const button = 'inline-flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent disabled:opacity-60'
  return (
    <section className="space-y-4 rounded-xl border border-accent/40 bg-card p-5" data-testid="import-panel">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">Загрузка себестоимости из файла</h2>
        <button onClick={onClose} className="ml-auto text-sm text-muted hover:text-fg">
          Закрыть
        </button>
      </div>
      <ol className="grid gap-3 lg:grid-cols-3">
        <Step n={1} title="Скачайте шаблон">
          <div className="inline-flex rounded-lg border border-line p-0.5 text-sm" role="group" aria-label="Формат шаблона">
            {(['xlsx', 'csv'] as const).map((f) => (
              <button
                key={f}
                type="button"
                aria-pressed={format === f}
                onClick={() => setFormat(f)}
                className={`rounded-md px-3 py-1 ${format === f ? 'bg-accent text-white' : 'text-muted hover:text-fg'}`}
              >
                {f === 'xlsx' ? 'Excel' : 'CSV'}
              </button>
            ))}
          </div>
          <div className="flex flex-col items-start gap-2">
            <CostTemplateButton rows="all" format={format} className={button}>
              С вашими товарами ({allCount})
            </CostTemplateButton>
            {missingCount > 0 && (
              <CostTemplateButton rows="missing" format={format} className={button}>
                Только без себестоимости ({missingCount})
              </CostTemplateButton>
            )}
            <CostTemplateButton rows="empty" format={format} className={button}>
              Пустой
            </CostTemplateButton>
          </div>
          <p className="text-xs text-faint">
            {format === 'xlsx'
              ? 'В шаблоне уже есть ваши товары и текущие цены, на втором листе — инструкция.'
              : 'Excel при открытии CSV может превратить артикулы вроде 1-2 в даты — надёжнее шаблон Excel.'}
          </p>
        </Step>
        <Step n={2} title="Заполните себестоимость">
          <p className="text-sm text-muted">
            Колонка «Себестоимость за шт. без НДС» — цена закупки одной штуки. Ставка НДС поставщика, «Действует с» и комментарий — по
            желанию.
          </p>
          <p className="text-xs text-faint">Пустые строки пропустим, такие же цены оставим как есть. «Действует с» пусто — с сегодня.</p>
        </Step>
        <Step n={3} title="Загрузите файл">
          <button onClick={() => input.current?.click()} disabled={busy} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60">
            {file ? 'Выбрать другой файл' : 'Выбрать файл'}
          </button>
          {file && <p className="truncate text-sm text-muted">{file.name}</p>}
          <p className="text-xs text-faint">Покажем, что изменится, — применится только после подтверждения.</p>
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
              setShowAll(false)
              send(f, false)
            }}
          />
        </Step>
      </ol>
      <FormError message={error} />
      {busy && !preview && <p className="text-sm text-muted">Читаем файл…</p>}
      {preview && (
        <>
          <div className="flex flex-wrap gap-2 text-sm" data-testid="import-counts">
            <Badge tone="accent">Новых: {preview.counts.new}</Badge>
            <Badge tone="warn">Изменится: {preview.counts.changed}</Badge>
            <Badge tone="muted">Без изменений: {preview.counts.unchanged}</Badge>
            <Badge tone="muted">Пропущено: {preview.counts.skipped}</Badge>
            <Badge tone="bad">Ошибок: {preview.counts.error}</Badge>
          </div>
          {actionable.length === 0 && !showAll && (
            <p className="text-sm text-muted">Изменений нет: цены в файле не заполнены или совпадают с текущими.</p>
          )}
          {rows.length > 0 && (
            <div className="max-h-80 overflow-auto rounded-lg border border-line">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-card text-left text-xs text-muted">
                  <tr className="border-b border-line">
                    <th className="px-3 py-2 font-normal">Строка</th>
                    <th className="px-3 py-2 font-normal">Артикул</th>
                    <th className="px-3 py-2 text-right font-normal">Сейчас</th>
                    <th className="px-3 py-2 text-right font-normal">Будет</th>
                    <th className="px-3 py-2 font-normal">НДС</th>
                    <th className="px-3 py-2 font-normal">Действует</th>
                    <th className="px-3 py-2 font-normal">Статус</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.line} className="border-b border-line last:border-b-0" data-testid="import-row">
                      <td className="px-3 py-1.5 text-faint">{r.line}</td>
                      <td className="px-3 py-1.5">{r.offer_id || '—'}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums text-muted">{rub(r.current_cost)}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {rub(r.cost_price)}
                        {r.cost_with_vat && <div className="text-xs text-faint">с НДС {rub(r.cost_with_vat)}</div>}
                      </td>
                      <td className="px-3 py-1.5 text-muted">{r.cost_price === null ? '—' : vatText(r.vat_multiplier)}</td>
                      <td className="whitespace-nowrap px-3 py-1.5 text-muted">{r.effective_from ? `с ${fmtDay(r.effective_from)}` : '—'}</td>
                      <td className="px-3 py-1.5">
                        <Badge tone={STATUS[r.status].tone}>{STATUS[r.status].label}</Badge>
                        {r.message && r.status === 'error' && <span className="ml-2 text-xs text-bad">{r.message}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {sorted.length > actionable.length && (
            <button onClick={() => setShowAll(!showAll)} className="text-sm text-muted hover:text-fg">
              {showAll ? 'Показать только изменения и ошибки' : `Показать все строки (${sorted.length})`}
            </button>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={() => file && send(file, true)}
              disabled={busy || toApply === 0}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
            >
              {busy ? 'Применяем…' : `Применить (${toApply})`}
            </button>
            {preview.counts.error > 0 && (
              <>
                <button onClick={errorsFile} className={button}>
                  <Download className="h-4 w-4" />
                  Скачать строки с ошибками
                </button>
                <span className="text-sm text-muted">Строки с ошибками не применятся — исправьте их в файле и загрузите снова.</span>
              </>
            )}
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
      `Добавлено: ${r.counts.new}, обновлено: ${r.counts.changed}, без изменений: ${r.counts.unchanged}, пропущено: ${r.counts.skipped}, ошибок: ${r.counts.error}` +
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
          Загрузить из файла
        </button>
      </div>

      {importing && <ImportPanel allCount={page?.all_count ?? 0} missingCount={page?.missing_count ?? 0} onDone={imported} onClose={() => setImporting(false)} />}
      {notice && (
        <div className="flex items-center gap-3 rounded-lg border border-ok/40 bg-ok/10 px-4 py-2 text-sm text-ok" role="status">
          {notice}
          <button onClick={() => setNotice(null)} className="ml-auto" aria-label="Закрыть">
            ×
          </button>
        </div>
      )}
      <FormError message={error} />

      {page && page.missing_count > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-warn/40 bg-warn/10 px-4 py-2 text-sm text-warn" data-testid="missing-banner">
          Себестоимость не задана для {page.missing_count} артикулов — прибыль по ним завышена.
          {!missingOnly && (
            <button onClick={() => setMissingOnly(true)} className="underline">
              Показать
            </button>
          )}
          <span className="ml-auto">
            <CostTemplateButton rows="missing">Скачать шаблон с этими товарами</CostTemplateButton>
          </span>
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
              : 'Артикулов пока нет. Подключите ключ Ozon или загрузите отчёт — товары появятся здесь, и шаблон себестоимости будет уже с ними.'}
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

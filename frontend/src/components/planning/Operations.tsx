'use client'
// «Операции → Все операции» (шаги 3.8, 3.12, 10.19): быстрое добавление, журнал факт + план (платежи календаря) с фильтрами,
// сортировкой и Excel, «Оплачено», массовое изменение, история исправлений, загрузка списком.
import { Check, Download, History, Pencil, Trash2, Upload } from 'lucide-react'
import { useSearchParams } from 'next/navigation'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { CategoryOptions, CategoryPicker } from '@/components/planning/Categories'
import { Modal, input } from '@/components/planning/ui'
import {
  type Account,
  type Category,
  type JournalPage,
  type JournalRow,
  type JournalSource,
  type JournalStatus,
  type Operation,
  type RuleSuggestion,
  parseAmount,
} from '@/components/planning/types'
import { Badge, fmtDateTime, fmtDay, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { saveFile } from '@/lib/download'
import { addDays, todayMsk } from '@/lib/period'

type OpType = 'expense' | 'income' | 'transfer'
const TYPES: { value: OpType; label: string }[] = [
  { value: 'expense', label: 'Расход' },
  { value: 'income', label: 'Приход' },
  { value: 'transfer', label: 'Перевод' },
]
export { Modal, input } // окно и поле ввода — общие для раздела (components/planning/ui.tsx)
const DAY = /^\d{4}-\d{2}-\d{2}$/
const SOURCE: Record<Operation['source'], string | null> = { manual: null, import: 'из файла', ozon_payout: 'Ozon', bank: 'банк' }

// ---------- форма: новая операция или исправление ----------

/** Название статьи для сравнения — как на сервере: без регистра, «ё» = «е», лишние пробелы не важны. */
const nameKey = (name: string) => name.toLowerCase().replace(/ё/g, 'е').split(/\s+/).filter(Boolean).join(' ')
export const sameName = (a: string, b: string) => nameKey(a) === nameKey(b)

/** Подсказка под статьёй: выбрана из списка, такая уже есть (вписали руками) или новая — сохранится в списке. */
export function CategoryHint({ text, match, typed }: { text: string; match?: Category; typed: boolean }) {
  if (!text.trim()) return <span className="mt-1 block text-xs text-faint">Пусто — без статьи. Впишите новую — она сохранится в списке</span>
  if (match && typed)
    return (
      <span className="mt-1 block text-xs text-ok" data-testid="cat-hint">
        Такая статья уже есть — выбрана «{match.name}»
      </span>
    )
  if (match) return null
  return (
    <span className="mt-1 block text-xs text-accent" data-testid="cat-hint">
      Новая статья — сохранится в списке
    </span>
  )
}

function OperationForm({
  accounts,
  categories,
  initial,
  onSaved,
  onCancel,
  onCategoryCreated,
}: {
  accounts: Account[]
  categories: Category[]
  initial?: Operation
  onSaved: (suggestion?: RuleSuggestion | null) => void // статью поставили вручную — можно сделать правилом (шаг 3.12)
  onCancel?: () => void
  onCategoryCreated?: () => void // «+» у статьи добавил новую — обновить список статей страницы
}) {
  const today = todayMsk()
  const active = useMemo(() => accounts.filter((a) => !a.archived), [accounts])
  const ozon = initial?.source === 'ozon_payout'
  const bank = initial?.source === 'bank' // из выписки банка: сумма, дата и счёт — как в банке
  const [type, setType] = useState<OpType>(initial ? (initial.transfer ? 'transfer' : Number(initial.amount) > 0 ? 'income' : 'expense') : 'expense')
  const [amount, setAmount] = useState(initial ? String(Math.abs(Number(initial.amount))).replace('.', ',') : '')
  const [day, setDay] = useState(initial?.day ?? today)
  const [account, setAccount] = useState(initial?.account.id ?? active[0]?.id ?? '')
  const [toAccount, setToAccount] = useState(active.find((a) => a.id !== account)?.id ?? '')
  const [categoryId, setCategoryId] = useState(initial?.category?.id ?? '') // из списка; новая — «+» рядом (шаг 10.19)
  const [notice, setNotice] = useState<string | null>(null)
  const [counterparty, setCounterparty] = useState(initial?.counterparty ?? '')
  const [comment, setComment] = useState(initial?.comment ?? '')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  // счёт ушёл в архив или появился первый — выбираем доступный
  useEffect(() => {
    if (initial) return
    if (!active.some((a) => a.id === account) && active[0]) setAccount(active[0].id)
    if ((!active.some((a) => a.id === toAccount) || toAccount === account) && active.length > 1) {
      setToAccount(active.find((a) => a.id !== account)?.id ?? '')
    }
  }, [active, account, toAccount, initial])
  const categoryBody = () => ({ category_id: categoryId || null })

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    const value = parseAmount(amount)
    if (!ozon && (value === null || Number(value) <= 0)) return setError('Сумма — числом больше нуля, например 12 000,50')
    setSaving(true)
    try {
      if (initial) {
        const body = ozon || bank
          ? initial.transfer
            ? { comment }
            : { ...categoryBody(), counterparty, comment }
          : initial.transfer
            ? { amount: value, day, comment }
            : { type, amount: value, day, account_id: account, ...categoryBody(), counterparty, comment }
        const { data } = await api.patch<{ rule_suggestion: RuleSuggestion | null }>(`/planning/operations/${initial.op_id}`, body)
        onSaved(data.rule_suggestion)
        return
      } else {
        const { data } = await api.post<{ category: { name: string } | null; category_note: 'existing' | 'created' | 'restored' | null }>(
          '/planning/operations',
          {
            type,
            amount: value,
            day,
            account_id: account,
            ...(type === 'transfer' ? { to_account_id: toAccount } : { ...categoryBody(), counterparty }),
            comment,
          },
        )
        setAmount('')
        setCounterparty('')
        setComment('')
        setNotice(data.category_note === 'restored' ? `Добавлено. Статья «${data.category?.name}» возвращена из архива.` : 'Добавлено.')
      }
      onSaved()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const lockMoney = ozon || bank // у выплаты Ozon и операции банка сумма, дата и счёт — из данных Ozon или выписки
  const transfer = type === 'transfer'
  return (
    <form onSubmit={submit} className="space-y-3" data-testid={initial ? 'op-edit-form' : 'op-form'}>
      {!initial && (
        <div className="flex flex-wrap gap-1 rounded-lg border border-line bg-bg p-1" role="group" aria-label="Тип операции">
          {TYPES.map((t) => (
            <button
              key={t.value}
              type="button"
              aria-pressed={type === t.value}
              onClick={() => {
                setType(t.value)
                setCategoryId('')
                setNotice(null)
              }}
              className={`flex-1 rounded-md px-3 py-1.5 text-sm ${type === t.value ? (t.value === 'income' ? 'bg-ok text-white' : t.value === 'expense' ? 'bg-bad text-white' : 'bg-accent text-white') : 'text-muted hover:text-fg'}`}
              data-testid={`op-type-${t.value}`}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}
      {lockMoney && (
        <p className="text-xs text-muted">
          {bank
            ? 'Операция из выписки банка: сумма, дата и счёт — как в банке. Здесь можно поменять статью, контрагента и описание.'
            : 'Выплата Ozon обновляется сама по данным Ozon — здесь можно поменять статью, контрагента и комментарий.'}
        </p>
      )}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="block text-sm">
          <span className="text-muted">Сумма, ₽</span>
          <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" disabled={lockMoney} required className={`${input} mt-1`} data-testid="op-amount" />
        </label>
        <label className="block text-sm">
          <span className="text-muted">Дата</span>
          <input type="date" value={day} max={today} onChange={(e) => setDay(e.target.value)} disabled={lockMoney} required className={`${input} mt-1`} data-testid="op-day" />
        </label>
        {(!initial || !initial.transfer) && (
          <label className="block text-sm">
            <span className="text-muted">{transfer ? 'Со счёта' : 'Счёт'}</span>
            <select value={account} onChange={(e) => setAccount(e.target.value)} disabled={lockMoney} className={`${input} mt-1`} data-testid="op-account">
              {(lockMoney || initial ? accounts : active).map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {transfer && !initial ? (
          <label className="block text-sm">
            <span className="text-muted">На счёт</span>
            <select value={toAccount} onChange={(e) => setToAccount(e.target.value)} className={`${input} mt-1`} data-testid="op-to-account">
              {active
                .filter((a) => a.id !== account)
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
            </select>
          </label>
        ) : (
          !initial?.transfer && (
            <div className="block text-sm">
              <span className="text-muted">Статья</span>
              <CategoryPicker
                categories={categories}
                kind={type === 'income' ? 'income' : 'expense'}
                value={categoryId}
                keepId={initial?.category?.id}
                onChange={(id) => {
                  setCategoryId(id)
                  setNotice(null)
                }}
                onCreated={onCategoryCreated}
                className="mt-1"
              />
            </div>
          )
        )}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {!transfer && (
          <input value={counterparty} onChange={(e) => setCounterparty(e.target.value)} placeholder="Контрагент — необязательно" aria-label="Контрагент" className={input} data-testid="op-counterparty" />
        )}
        <textarea
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          maxLength={2000}
          rows={2}
          placeholder="Описание — необязательно: что за платёж, для чего"
          aria-label="Описание"
          className={`${input} resize-y`}
          data-testid="op-comment"
        />
      </div>
      {transfer && !initial && <p className="text-xs text-faint">Перевод между своими счетами — не доход и не расход: общий остаток не меняется.</p>}
      <FormError message={error} />
      {notice && (
        <p className="text-sm text-ok" data-testid="op-notice">
          {notice}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={saving || active.length === 0} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="op-save">
          {saving ? 'Сохраняем…' : initial ? 'Сохранить' : 'Добавить'}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} className="rounded-lg border border-line px-4 py-2 text-sm text-muted hover:text-fg">
            Отмена
          </button>
        )}
      </div>
    </form>
  )
}

// ---------- карточка операции, исправление и история ----------

/** Первые слова описания для списка: «Оплата упаковки для партии…». */
export function preview(text: string | null, words = 8): string | null {
  if (!text) return null
  const all = text.split(/\s+/).filter(Boolean)
  const head = all.slice(0, words).join(' ')
  return all.length > words || head.length > 70 ? `${head.slice(0, 70).trimEnd()}…` : head
}

const SOURCE_TITLE: Record<Operation['source'], string> = { manual: 'вручную', import: 'из файла', ozon_payout: 'выплата Ozon — пришла сама', bank: 'из банка' }

/** Вся информация об операции — по нажатию на неё в списке: полное описание, счёт, статья, откуда, версии. */
function OperationCard({ op, onEdit, onHistory, onDelete }: { op: Operation; onEdit: () => void; onHistory: () => void; onDelete: () => void }) {
  const amount = Number(op.amount)
  const row = (label: string, value: React.ReactNode, testid?: string) => (
    <div className="grid grid-cols-[8rem_minmax(0,1fr)] gap-3 border-b border-line py-2 last:border-b-0" data-testid={testid}>
      <dt className="text-muted">{label}</dt>
      <dd className="min-w-0 break-words">{value}</dd>
    </div>
  )
  return (
    <div className="space-y-4" data-testid="op-card">
      <div>
        <div className={`text-2xl font-bold tabular-nums ${op.transfer ? '' : amount > 0 ? 'text-ok' : 'text-bad'}`}>
          {op.transfer ? rub(String(Math.abs(amount))) : `${amount > 0 ? '+' : ''}${rub(op.amount)}`}
        </div>
        <div className="text-sm text-muted">
          {op.transfer ? 'Перевод между своими счетами' : amount > 0 ? 'Приход' : 'Расход'} · {fmtDay(op.day)}
        </div>
      </div>
      <dl className="text-sm">
        {op.transfer
          ? row('Счета', op.transfer.direction === 'out' ? `${op.account.name} → ${op.transfer.account?.name ?? '—'}` : `${op.transfer.account?.name ?? '—'} → ${op.account.name}`)
          : row('Счёт', op.account.name)}
        {!op.transfer && row('Статья', op.category?.name ?? <span className="text-faint">без статьи</span>)}
        {!op.transfer && row('Контрагент', op.counterparty ?? <span className="text-faint">—</span>)}
        {row(
          'Описание',
          op.comment ? <span className="whitespace-pre-wrap">{op.comment}</span> : <span className="text-faint">нет — можно добавить в «Исправить»</span>,
          'op-card-description',
        )}
        {row('Откуда', SOURCE_TITLE[op.source])}
        {op.version > 1 && row('Исправлялась', `${op.version - 1} раз — прежние версии в истории`)}
        {op.before_opening && row('Остаток', 'операция раньше начального остатка счёта — в остаток не входит')}
      </dl>
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={onEdit} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90" data-testid="op-card-edit">
          Исправить
        </button>
        {op.version > 1 && (
          <button type="button" onClick={onHistory} className="rounded-lg border border-line px-4 py-2 text-sm hover:border-accent hover:text-accent">
            История
          </button>
        )}
        {op.source !== 'ozon_payout' && op.source !== 'bank' && (
          <button type="button" onClick={onDelete} className="rounded-lg border border-line px-4 py-2 text-sm text-muted hover:border-bad hover:text-bad">
            Удалить
          </button>
        )}
      </div>
    </div>
  )
}

type Version = { version: number; day: string; amount: string; account: string; category: string | null; comment: string | null; created_at: string; deleted_at: string | null }

function HistoryView({ opId }: { opId: string }) {
  const [rows, setRows] = useState<Version[] | null>(null)
  useEffect(() => {
    api.get<Version[]>(`/planning/operations/${opId}/history`).then((r) => setRows(r.data))
  }, [opId])
  if (!rows) return <p className="text-sm text-muted">Загрузка…</p>
  return (
    <ul className="space-y-2 text-sm" data-testid="op-history">
      {rows.map((v) => (
        <li key={v.version} className="rounded-lg border border-line p-3">
          <div className="flex flex-wrap gap-x-3 text-xs text-faint">
            <span>версия {v.version}</span>
            <span>{fmtDateTime(v.created_at)}</span>
            {v.deleted_at && <span className="text-bad">скрыта {fmtDateTime(v.deleted_at)}</span>}
          </div>
          <div className="mt-1 tabular-nums">
            {fmtDay(v.day)} · {rub(v.amount)} · {v.account}
            {v.category && ` · ${v.category}`}
            {v.comment && <span className="text-muted"> — {v.comment}</span>}
          </div>
        </li>
      ))}
    </ul>
  )
}

// ---------- загрузка списком ----------

type ImportRow = { line: number; day: string | null; amount: string | null; account: string | null; category: string | null; comment: string | null; status: 'new' | 'changed' | 'unchanged' | 'skipped' | 'error'; message: string | null }
type ImportResult = { applied: boolean; counts: Record<ImportRow['status'], number>; rows: ImportRow[] }

/** Списком через шаблон (правило 14): операции (по умолчанию) или плановые платежи (base='/planning/plans'). */
export function ImportPanel({
  onApplied,
  base = '/planning/operations',
  params = {},
  fileName = 'operations',
  intro = 'Скачайте шаблон — в нём ваши счета, статьи и операции за выбранный период. Исправьте или допишите строки и загрузите обратно: сначала покажем, что изменится.',
  allLabel = 'Шаблон с операциями (Excel)',
}: {
  onApplied: () => void
  base?: string
  params?: Record<string, string>
  fileName?: string
  intro?: string
  allLabel?: string
}) {
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<ImportResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  async function send(f: File, apply: boolean) {
    setBusy(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('file', f)
      const { data } = await api.post<ImportResult>(`${base}/import`, form, { params: { dry_run: !apply } })
      if (apply) {
        setPreview(null)
        setFile(null)
        setDone(`Готово: добавлено ${data.counts.new}, исправлено ${data.counts.changed}.`)
        onApplied()
      } else setPreview(data)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  async function download(rows: 'all' | 'empty', format: 'xlsx' | 'csv') {
    setError(null)
    try {
      await saveFile(api.get(`${base}/template`, { params: { rows, format, ...params }, responseType: 'blob' }), `${fileName}.${format}`)
    } catch (err) {
      setError(errorMessage(err, 'Не удалось скачать файл'))
    }
  }

  async function errorsFile() {
    if (!file) return
    const form = new FormData()
    form.append('file', file)
    try {
      await saveFile(api.post(`${base}/import/errors`, form, { responseType: 'blob' }), `${fileName}-errors.xlsx`)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const link = 'inline-flex items-center gap-1.5 text-sm text-accent hover:underline'
  const toApply = preview ? preview.counts.new + preview.counts.changed : 0
  return (
    <details className="rounded-xl border border-line bg-card p-4" data-testid="import-panel">
      <summary className="cursor-pointer text-sm font-semibold">Списком через Excel или CSV</summary>
      <p className="mt-2 text-sm text-muted">{intro}</p>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-2">
        <button type="button" onClick={() => download('all', 'xlsx')} className={link} data-testid="tpl-all">
          <Download className="h-4 w-4" /> {allLabel}
        </button>
        <button type="button" onClick={() => download('empty', 'xlsx')} className={link} data-testid="tpl-empty">
          <Download className="h-4 w-4" /> Пустой шаблон
        </button>
        <button type="button" onClick={() => download('all', 'csv')} className={link}>
          <Download className="h-4 w-4" /> CSV
        </button>
        <button type="button" onClick={() => inputRef.current?.click()} className={link} data-testid="import-pick">
          <Upload className="h-4 w-4" /> Загрузить файл
        </button>
        <input
          ref={inputRef}
          type="file"
          accept=".xlsx,.csv"
          className="hidden"
          data-testid="import-input"
          onChange={(e) => {
            const f = e.target.files?.[0]
            e.target.value = ''
            if (!f) return
            setFile(f)
            setDone(null)
            send(f, false)
          }}
        />
      </div>
      <FormError message={error} />
      {done && <p className="mt-2 text-sm text-ok">{done}</p>}
      {busy && !preview && <p className="mt-2 text-sm text-muted">Читаем файл…</p>}
      {preview && (
        <div className="mt-3 space-y-3" data-testid="import-preview">
          <div className="flex flex-wrap gap-2 text-sm" data-testid="import-counts">
            <Badge tone="accent">Новых: {preview.counts.new}</Badge>
            <Badge tone="warn">Изменится: {preview.counts.changed}</Badge>
            <Badge tone="muted">Без изменений: {preview.counts.unchanged}</Badge>
            <Badge tone="muted">Пропущено: {preview.counts.skipped}</Badge>
            <Badge tone="bad">Ошибок: {preview.counts.error}</Badge>
          </div>
          {preview.rows.some((r) => r.status === 'error' || r.status === 'skipped') && (
            <ul className="max-h-60 space-y-1 overflow-y-auto text-sm">
              {preview.rows
                .filter((r) => r.status === 'error' || r.status === 'skipped')
                .map((r) => (
                  <li key={r.line} className={r.status === 'error' ? 'text-bad' : 'text-muted'}>
                    Строка {r.line}: {r.message}
                  </li>
                ))}
            </ul>
          )}
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={busy || toApply === 0 || !file} onClick={() => file && send(file, true)} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="import-apply">
              Применить ({toApply})
            </button>
            {preview.counts.error > 0 && (
              <button type="button" onClick={errorsFile} className="rounded-lg border border-line px-4 py-2 text-sm hover:border-accent hover:text-accent">
                Скачать строки с ошибками
              </button>
            )}
            <button type="button" onClick={() => setPreview(null)} className="rounded-lg border border-line px-4 py-2 text-sm text-muted hover:text-fg">
              Отмена
            </button>
          </div>
        </div>
      )}
    </details>
  )
}

// ---------- правила и массовая разметка (шаг 3.12) ----------

const NO_CATEGORY = 'none' // фильтр «Без статьи»

/** Поставили статью вручную — «все операции этого контрагента → эта статья?» */
function SuggestionBanner({ suggestion, onDone, onClose }: { suggestion: RuleSuggestion; onDone: (text: string) => void; onClose: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const who = suggestion.counterparty_name ?? suggestion.counterparty ?? 'этого контрагента'

  async function create() {
    setBusy(true)
    setError(null)
    try {
      const { data } = await api.post<{ applied: number }>('/planning/rules', {
        direction: suggestion.direction,
        counterparty_inn: suggestion.counterparty_inn,
        counterparty: suggestion.counterparty,
        category_id: suggestion.category.id,
      })
      onDone(`Правило создано: «${who}» → «${suggestion.category.name}». ${data.applied ? `Разнесено операций: ${data.applied}.` : 'Новые операции разнесутся сами.'}`)
    } catch (err) {
      setError(errorMessage(err))
      setBusy(false)
    }
  }

  return (
    <div className="rounded-xl border border-accent/40 bg-accent/5 p-3 text-sm" data-testid="rule-suggestion">
      <p>
        {suggestion.direction === 'in' ? 'Все поступления от' : 'Все платежи контрагенту'} <b>{who}</b>
        {suggestion.counterparty_inn && <span className="text-muted"> (ИНН {suggestion.counterparty_inn})</span>} — в статью{' '}
        <b>«{suggestion.category.name}»</b>?{' '}
        <span className="text-muted">
          {suggestion.others ? `Сразу поменяется у операций без ручной разметки: ${suggestion.others}. ` : ''}Новые будут разноситься сами.
        </span>
      </p>
      <FormError message={error} />
      <div className="mt-2 flex gap-2">
        <button type="button" onClick={create} disabled={busy} className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="rule-suggestion-create">
          {busy ? 'Создаю…' : 'Создать правило'}
        </button>
        <button type="button" onClick={onClose} className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-muted">
          Не нужно
        </button>
      </div>
    </div>
  )
}

type BulkField = 'category' | 'account' | 'counterparty' | 'comment'
const BULK_FIELDS: { value: BulkField; label: string }[] = [
  { value: 'category', label: 'Статья' },
  { value: 'account', label: 'Счёт' },
  { value: 'counterparty', label: 'Контрагент' },
  { value: 'comment', label: 'Описание' },
]

/** Выбрано несколько операций — изменить разом статью, счёт, контрагента или описание. Каждой — новая версия; не подходит
 * хоть одна — ничего не меняется, сервис назовёт какая. Статья, поставленная так, — вручную: правила её не меняют. */
function BulkBar({ ops, accounts, categories, onClear, onDone }: { ops: Operation[]; accounts: Account[]; categories: Category[]; onClear: () => void; onDone: (text: string) => void }) {
  const income = ops.every((op) => Number(op.amount) > 0)
  const expense = ops.every((op) => Number(op.amount) < 0)
  const kind = income ? 'income' : expense ? 'expense' : null
  const [field, setField] = useState<BulkField>(kind ? 'category' : 'account')
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // выбор поменялся: статья другого вида не подойдёт, а поступления вместе с платежами — без статьи
  useEffect(() => {
    if (field !== 'category') return
    setValue('')
    if (!kind) setField('account')
  }, [kind]) // eslint-disable-line react-hooks/exhaustive-deps
  const control = 'min-w-0 basis-full rounded-lg border border-line bg-bg px-3 py-1.5 text-sm outline-none focus:border-accent sm:basis-auto'

  async function apply() {
    if (field === 'category' && !value) return setError('Выберите статью')
    if (field === 'account' && !value) return setError('Выберите счёт')
    setBusy(true)
    setError(null)
    try {
      const body = { category: { category_id: value }, account: { account_id: value }, counterparty: { counterparty: value }, comment: { comment: value } }[field]
      const { data } = await api.post<{ changed: number; unchanged: number }>('/planning/operations/bulk', { op_ids: ops.map((op) => op.op_id), ...body })
      const what = BULK_FIELDS.find((f) => f.value === field)?.label.toLowerCase()
      onDone(`Изменено операций: ${data.changed}${data.unchanged ? `, уже были такими: ${data.unchanged}` : ''} (${what}).`)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="sticky top-2 z-10 flex flex-wrap items-center gap-2 rounded-xl border border-accent/40 bg-card p-3 text-sm shadow-sm" data-testid="ops-bulk">
      <span className="font-medium">Выбрано: {ops.length}</span>
      <select
        value={field}
        onChange={(e) => {
          setField(e.target.value as BulkField)
          setValue('')
          setError(null)
        }}
        aria-label="Что изменить"
        className={control}
        data-testid="ops-bulk-field"
      >
        {BULK_FIELDS.map((f) => (
          <option key={f.value} value={f.value} disabled={f.value === 'category' && !kind}>
            {f.label}
          </option>
        ))}
      </select>
      {field === 'category' && kind && (
        <select value={value} onChange={(e) => setValue(e.target.value)} aria-label="Статья для выбранных" className={control} data-testid="ops-bulk-category">
          <option value="">— статья —</option>
          <CategoryOptions categories={categories} kind={kind} />
        </select>
      )}
      {field === 'account' && (
        <select value={value} onChange={(e) => setValue(e.target.value)} aria-label="Счёт для выбранных" className={control} data-testid="ops-bulk-account">
          <option value="">— счёт —</option>
          {accounts
            .filter((a) => !a.archived)
            .map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
        </select>
      )}
      {(field === 'counterparty' || field === 'comment') && (
        <input value={value} onChange={(e) => setValue(e.target.value)} placeholder={field === 'comment' ? 'Описание — пусто: убрать' : 'Контрагент — пусто: убрать'} aria-label="Новое значение" className={control} data-testid="ops-bulk-text" />
      )}
      <button type="button" onClick={apply} disabled={busy} className="rounded-lg bg-accent px-3 py-1.5 font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="ops-bulk-apply">
        {busy ? 'Меняю…' : 'Изменить у выбранных'}
      </button>
      {!kind && <span className="basis-full text-xs text-warn sm:basis-auto">Выбраны и поступления, и платежи — статью ставьте отдельно</span>}
      <button type="button" onClick={onClear} className="text-muted hover:text-fg">
        Снять выбор
      </button>
      <FormError message={error} />
    </div>
  )
}

// ---------- быстрое добавление ----------

/** Короткая форма: приход или расход — сумма, статья (с «+»), счёт, дата. Перевод, контрагент и описание — «Подробно». */
function QuickAdd({ accounts, categories, onSaved, onCategoryCreated, onMore }: { accounts: Account[]; categories: Category[]; onSaved: () => void; onCategoryCreated: () => void; onMore: () => void }) {
  const today = todayMsk()
  const active = useMemo(() => accounts.filter((a) => !a.archived), [accounts])
  const [type, setType] = useState<'expense' | 'income'>('expense')
  const [amount, setAmount] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [account, setAccount] = useState(active[0]?.id ?? '')
  const [day, setDay] = useState(today)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    if (!active.some((a) => a.id === account) && active[0]) setAccount(active[0].id)
  }, [active, account])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setNotice(null)
    const value = parseAmount(amount)
    if (value === null || Number(value) <= 0) return setError('Сумма — числом больше нуля, например 12 000,50')
    setSaving(true)
    try {
      await api.post('/planning/operations', { type, amount: value, day, account_id: account, category_id: categoryId || null })
      setAmount('')
      setNotice(`Добавлено: ${type === 'income' ? '+' : '−'}${rub(value)}`)
      onSaved()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={submit} className="rounded-xl border border-line bg-card p-4" data-testid="quick-add">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">Быстро добавить</h2>
        <div className="flex gap-1 rounded-lg border border-line bg-bg p-1" role="group" aria-label="Приход или расход">
          {(['expense', 'income'] as const).map((t) => (
            <button
              key={t}
              type="button"
              aria-pressed={type === t}
              onClick={() => {
                setType(t)
                setCategoryId('')
              }}
              className={`rounded-md px-3 py-1 text-sm ${type === t ? (t === 'income' ? 'bg-ok text-white' : 'bg-bad text-white') : 'text-muted hover:text-fg'}`}
              data-testid={`quick-type-${t}`}
            >
              {t === 'income' ? 'Приход' : 'Расход'}
            </button>
          ))}
        </div>
        <button type="button" onClick={onMore} className="ml-auto text-sm text-accent hover:underline" data-testid="op-more">
          Подробно: перевод, контрагент, описание
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[8rem_minmax(0,1fr)_minmax(0,12rem)_10rem_auto] lg:items-end">
        <label className="block text-sm">
          <span className="text-muted">Сумма, ₽</span>
          <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" required className={`${input} mt-1`} data-testid="quick-amount" />
        </label>
        <div className="block text-sm">
          <span className="text-muted">Статья</span>
          <CategoryPicker categories={categories} kind={type} value={categoryId} onChange={setCategoryId} onCreated={onCategoryCreated} testid="quick-category" className="mt-1" />
        </div>
        <label className="block text-sm">
          <span className="text-muted">Счёт</span>
          <select value={account} onChange={(e) => setAccount(e.target.value)} className={`${input} mt-1`} data-testid="quick-account">
            {active.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm">
          <span className="text-muted">Дата</span>
          <input type="date" value={day} max={today} onChange={(e) => setDay(e.target.value)} required className={`${input} mt-1`} data-testid="quick-day" />
        </label>
        <button type="submit" disabled={saving || active.length === 0} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="quick-save">
          {saving ? 'Сохраняем…' : 'Добавить'}
        </button>
      </div>
      <FormError message={error} />
      {notice && (
        <p className="mt-2 text-sm text-ok" data-testid="quick-notice">
          {notice}
        </p>
      )}
    </form>
  )
}

// ---------- журнал: факт и план ----------

const STATUS_FILTERS: { value: '' | JournalStatus; label: string }[] = [
  { value: '', label: 'Все' },
  { value: 'fact', label: 'Факт' },
  { value: 'planned', label: 'План' },
  { value: 'overdue', label: 'Просрочено' },
]
const SOURCES: { value: JournalSource; label: string }[] = [
  { value: 'manual', label: 'Вручную' },
  { value: 'import', label: 'Из файла' },
  { value: 'bank', label: 'Банк' },
  { value: 'ozon_payout', label: 'Выплаты Ozon' },
  { value: 'plan', label: 'Плановые платежи' },
  { value: 'supplier', label: 'Поставщик (план)' },
  { value: 'tax', label: 'Налоги (план)' },
  { value: 'ozon_forecast', label: 'Ozon (прогноз)' },
]
const SORTS = [
  { value: 'day:desc', label: 'Сначала новые' },
  { value: 'day:asc', label: 'Сначала старые' },
  { value: 'amount:desc', label: 'Сумма: больше' },
  { value: 'amount:asc', label: 'Сумма: меньше' },
  { value: 'category:asc', label: 'Статья: А–Я' },
] as const
const PLAN_SOURCE_TITLE: Partial<Record<JournalSource, string>> = { plan: 'плановый платёж', supplier: 'оплата поставщику по плану', tax: 'налог по сроку', ozon_forecast: 'выплата Ozon — прогноз' }
const LIMIT = 500

function CategoryName({ row }: { row: JournalRow }) {
  if (row.type === 'transfer') return <>Перевод</>
  if (!row.category) return <span className="text-warn">Без статьи</span>
  return (
    <>
      {row.parent && <span className="font-normal text-muted">{row.parent.name} / </span>}
      {row.category.name}
    </>
  )
}

const signed = (amount: string | null, transfer = false) =>
  amount === null ? 'сумма уточняется' : transfer ? rub(String(Math.abs(Number(amount)))) : `${Number(amount) > 0 ? '+' : ''}${rub(amount)}`

/** Плановый платёж в журнале: откуда, когда, сколько; «Оплачено» — станет операцией с датой и суммой. */
function PlanCard({ row, onPaid }: { row: JournalRow; onPaid: () => void }) {
  const line = (label: string, value: React.ReactNode) => (
    <div className="grid grid-cols-[8rem_minmax(0,1fr)] gap-3 border-b border-line py-2 last:border-b-0">
      <dt className="text-muted">{label}</dt>
      <dd className="min-w-0 break-words">{value}</dd>
    </div>
  )
  return (
    <div className="space-y-4" data-testid="plan-card">
      <div>
        <div className={`text-2xl font-bold tabular-nums ${row.type === 'income' ? 'text-ok' : 'text-bad'}`}>{signed(row.amount)}</div>
        <div className="text-sm text-muted">
          {row.status === 'overdue' ? 'Срок прошёл, оплаты нет' : 'По плану'} · {fmtDay(row.day)}
          {row.plan?.estimate && ' · сумма — оценка'}
        </div>
      </div>
      <dl className="text-sm">
        {line('Платёж', row.plan?.title ?? '—')}
        {line('Статья', <CategoryName row={row} />)}
        {line('Счёт', row.account?.name ?? <span className="text-faint">выберете при оплате</span>)}
        {row.counterparty && line('Контрагент', row.counterparty)}
        {line('Откуда', PLAN_SOURCE_TITLE[row.source] ?? 'план')}
      </dl>
      <div className="flex flex-wrap gap-2">
        {row.source !== 'ozon_forecast' ? (
          <button type="button" onClick={onPaid} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90" data-testid="plan-card-paid">
            Оплачено
          </button>
        ) : (
          <p className="text-sm text-muted">Выплата Ozon станет фактом сама, когда придёт по данным Ozon.</p>
        )}
        <a href="/dashboard/planning/calendar" className="rounded-lg border border-line px-4 py-2 text-sm hover:border-accent hover:text-accent">
          Открыть в календаре
        </a>
      </div>
    </div>
  )
}

/** Плановая → фактическая: дата и сумма оплаты (как «Оплачено» в календаре) — появится операция. */
function PaidForm({ row, accounts, onDone, onCancel }: { row: JournalRow; accounts: Account[]; onDone: () => void; onCancel: () => void }) {
  const today = todayMsk()
  const active = accounts.filter((a) => !a.archived)
  const [day, setDay] = useState(row.day < today ? row.day : today)
  const [amount, setAmount] = useState(row.amount === null ? '' : String(Math.abs(Number(row.amount))).replace('.', ','))
  const [account, setAccount] = useState(row.account?.id ?? active[0]?.id ?? '')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    const value = parseAmount(amount)
    if (value === null || Number(value) <= 0) return setError('Сумма — числом больше нуля')
    setBusy(true)
    setError(null)
    try {
      await api.post('/planning/calendar/paid', { key: row.plan?.key, day, amount: value, account_id: account || null })
      onDone()
    } catch (err) {
      setError(errorMessage(err))
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3 text-sm" data-testid="paid-form">
      <p className="text-muted">
        «{row.plan?.title}» по плану {fmtDay(row.day)} на {signed(row.amount)}. Укажите, когда и сколько оплатили, — появится операция, а платёж в календаре
        отметится оплаченным.
      </p>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="block">
          <span className="text-muted">Дата оплаты</span>
          <input type="date" value={day} max={today} onChange={(e) => setDay(e.target.value)} required className={`${input} mt-1`} data-testid="paid-day" />
        </label>
        <label className="block">
          <span className="text-muted">Сумма, ₽</span>
          <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" required className={`${input} mt-1`} data-testid="paid-amount" />
        </label>
        <label className="block">
          <span className="text-muted">Счёт</span>
          <select value={account} onChange={(e) => setAccount(e.target.value)} className={`${input} mt-1`} data-testid="paid-account">
            {active.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <FormError message={error} />
      <div className="flex gap-2">
        <button type="submit" disabled={busy} className="rounded-lg bg-accent px-4 py-2 font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="paid-save">
          {busy ? 'Сохраняем…' : 'Оплачено'}
        </button>
        <button type="button" onClick={onCancel} className="rounded-lg border border-line px-4 py-2 text-muted hover:text-fg">
          Отмена
        </button>
      </div>
    </form>
  )
}

export function OperationsTab({ accounts, categories, onChanged }: { accounts: Account[]; categories: Category[]; onChanged: () => void }) {
  const today = todayMsk()
  const params = useSearchParams()
  // из графиков и ДДС: ?from=…&to=…&category=… — операции за этот период и статью; без них — месяц назад и месяц вперёд;
  // из правил (10.20): ?rule=… — операции, которым статью поставило это правило (за год); из счетов: ?account=…
  const [ruleId, setRuleId] = useState(params.get('rule') ?? '')
  const [range, setRange] = useState(() => {
    const from = params.get('from') ?? ''
    const to = params.get('to') ?? ''
    if (DAY.test(from) && DAY.test(to) && from <= to) return { from, to }
    return params.get('rule') ? { from: addDays(today, -364), to: today } : { from: addDays(today, -29), to: addDays(today, 30) }
  })
  const [status, setStatus] = useState<'' | JournalStatus>('')
  const [type, setType] = useState('')
  const [accountId, setAccountId] = useState(params.get('account') ?? '')
  const [categoryId, setCategoryId] = useState(params.get('category') ?? '')
  const [source, setSource] = useState('')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<(typeof SORTS)[number]['value']>('day:desc')
  const [page, setPage] = useState<JournalPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [viewing, setViewing] = useState<Operation | null>(null) // карточка операции
  const [editing, setEditing] = useState<Operation | null>(null)
  const [adding, setAdding] = useState(false) // подробная форма
  const [historyOf, setHistoryOf] = useState<Operation | null>(null)
  const [plan, setPlan] = useState<JournalRow | null>(null) // карточка планового платежа
  const [paying, setPaying] = useState<JournalRow | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set()) // массовое изменение (шаги 3.12, 10.19)
  const [suggestion, setSuggestion] = useState<RuleSuggestion | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const query = useMemo(() => {
    const [by, order] = sort.split(':')
    return {
      date_from: range.from,
      date_to: range.to,
      sort: by,
      order,
      ...(status ? { status } : {}),
      ...(type ? { type } : {}),
      ...(accountId ? { account_id: accountId } : {}),
      ...(categoryId === NO_CATEGORY ? { uncategorized: true } : categoryId ? { category_id: categoryId } : {}),
      ...(source ? { source } : {}),
      ...(ruleId ? { rule_id: ruleId } : {}),
      ...(search.trim() ? { search: search.trim() } : {}),
    }
  }, [range, status, type, accountId, categoryId, source, search, sort, ruleId])

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<JournalPage>('/planning/journal', { params: { ...query, limit: LIMIT } })
      setPage(data)
      const ids = new Set(data.items.map((r) => r.operation?.op_id).filter(Boolean))
      setSelected((prev) => new Set(Array.from(prev).filter((id) => ids.has(id))))
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [query])

  useEffect(() => {
    const t = setTimeout(load, 250)
    return () => clearTimeout(t)
  }, [load])

  const changed = () => {
    load()
    onChanged()
  }

  async function remove(op: Operation) {
    if (!window.confirm(op.transfer ? 'Удалить перевод (обе части)?' : 'Удалить операцию? Она останется в истории.')) return
    try {
      await api.delete(`/planning/operations/${op.op_id}`)
      changed()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  async function exportXlsx() {
    setError(null)
    try {
      await saveFile(api.get('/planning/journal/export', { params: query, responseType: 'blob' }), 'operations.xlsx')
    } catch (err) {
      setError(errorMessage(err, 'Не удалось скачать файл'))
    }
  }

  const facts = page?.items.flatMap((r) => (r.operation && !r.operation.transfer ? [r.operation] : [])) ?? []
  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const select = 'rounded-lg border border-line bg-card px-3 py-2 text-sm outline-none focus:border-accent'
  const cellLabel = 'text-[11px] uppercase tracking-wider text-muted'
  return (
    <div className="space-y-4">
      <QuickAdd accounts={accounts} categories={categories} onSaved={changed} onCategoryCreated={onChanged} onMore={() => setAdding(true)} />

      <div className="space-y-2 text-sm" data-testid="ops-filters">
        <div className="flex flex-wrap items-center gap-2">
          <input type="date" value={range.from} max={range.to} onChange={(e) => e.target.value && setRange({ ...range, from: e.target.value })} className={select} aria-label="С" data-testid="ops-from" />
          <span className="text-muted">—</span>
          <input type="date" value={range.to} min={range.from} onChange={(e) => e.target.value && setRange({ ...range, to: e.target.value })} className={select} aria-label="По" data-testid="ops-to" />
          <div className="flex flex-wrap gap-1 rounded-lg border border-line bg-card p-1" role="group" aria-label="Факт или план" data-testid="ops-status">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.value}
                type="button"
                aria-pressed={status === f.value}
                onClick={() => setStatus(f.value)}
                className={`rounded-md px-2.5 py-1 ${status === f.value ? 'bg-accent text-white' : f.value === 'overdue' && page?.counts.overdue ? 'text-bad hover:text-fg' : 'text-muted hover:text-fg'}`}
                data-testid={`ops-status-${f.value || 'all'}`}
              >
                {f.label}
                {page && f.value && <span className="ml-1 tabular-nums opacity-80">{page.counts[f.value]}</span>}
              </button>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center">
          <select value={type} onChange={(e) => setType(e.target.value)} className={select} aria-label="Тип" data-testid="ops-type">
            <option value="">Все типы</option>
            <option value="income">Поступления</option>
            <option value="expense">Платежи</option>
            <option value="transfer">Переводы</option>
          </select>
          <select value={categoryId} onChange={(e) => setCategoryId(e.target.value)} className={select} aria-label="Статья" data-testid="ops-category">
            <option value="">Все статьи</option>
            <option value={NO_CATEGORY}>Без статьи</option>
            <CategoryOptions categories={categories} keepId={categoryId || null} />
          </select>
          <select value={accountId} onChange={(e) => setAccountId(e.target.value)} className={select} aria-label="Счёт" data-testid="ops-account">
            <option value="">Все счета</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
          <select value={source} onChange={(e) => setSource(e.target.value)} className={select} aria-label="Источник" data-testid="ops-source">
            <option value="">Все источники</option>
            {SOURCES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
          <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)} className={select} aria-label="Сортировка" data-testid="ops-sort">
            {SORTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
          <button type="button" onClick={exportXlsx} className={`${select} inline-flex items-center justify-center gap-1.5 hover:border-accent hover:text-accent`} data-testid="ops-export">
            <Download className="h-4 w-4" /> Excel
          </button>
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Контрагент, описание, платёж" aria-label="Поиск" className={`${select} col-span-2 min-w-0 sm:flex-1`} data-testid="ops-search" />
        </div>
      </div>

      {ruleId && (
        <p className="flex flex-wrap items-center gap-2 text-sm" data-testid="ops-rule-filter">
          <Badge tone="accent">Операции одного правила разнесения</Badge>
          <span className="text-muted">статью им поставило правило — за год</span>
          <button type="button" onClick={() => setRuleId('')} className="text-accent hover:underline">
            Показать все
          </button>
        </p>
      )}
      <FormError message={error} />
      {suggestion && <SuggestionBanner suggestion={suggestion} onDone={(text) => { setSuggestion(null); setNotice(text); changed() }} onClose={() => setSuggestion(null)} />}
      {notice && <p className="text-sm text-ok" data-testid="ops-notice">{notice}</p>}
      {page && selected.size > 0 && (
        <BulkBar
          ops={facts.filter((op) => selected.has(op.op_id))}
          accounts={accounts}
          categories={categories}
          onClear={() => setSelected(new Set())}
          onDone={(text) => {
            setSelected(new Set())
            setNotice(text)
            changed()
          }}
        />
      )}
      {page && facts.length > 0 && selected.size === 0 && (
        <button type="button" onClick={() => setSelected(new Set(facts.map((op) => op.op_id)))} className="text-sm text-accent hover:underline" data-testid="ops-select-all">
          {categoryId === NO_CATEGORY ? `Выбрать все без статьи (${facts.length}) — и поставить статью разом` : `Выбрать все операции в списке (${facts.length}) — изменить разом`}
        </button>
      )}
      {page && (
        <section className="overflow-hidden rounded-xl border border-line bg-card" data-testid="ops-list">
          <div className="grid grid-cols-3 gap-px border-b border-line bg-line text-sm" data-testid="ops-totals">
            <div className="min-w-0 bg-card px-2.5 py-2 sm:px-4">
              <div className={cellLabel}>Пришло</div>
              <div className="whitespace-nowrap text-[13px] font-semibold tabular-nums sm:text-sm text-ok">{rub(page.totals.fact.income)}</div>
            </div>
            <div className="min-w-0 bg-card px-2.5 py-2 sm:px-4">
              <div className={cellLabel}>Ушло</div>
              <div className="whitespace-nowrap text-[13px] font-semibold tabular-nums sm:text-sm text-bad">{rub(page.totals.fact.expense)}</div>
            </div>
            <div className="min-w-0 bg-card px-2.5 py-2 sm:px-4">
              <div className={cellLabel}>Итого факт</div>
              <div className={`whitespace-nowrap text-[13px] font-semibold tabular-nums sm:text-sm ${Number(page.totals.fact.net) < 0 ? 'text-bad' : 'text-ok'}`}>{rub(page.totals.fact.net)}</div>
            </div>
            {page.counts.planned + page.counts.overdue > 0 && status !== 'fact' && (
              <>
                <div className="min-w-0 bg-card px-2.5 py-2 sm:px-4" data-testid="ops-plan-totals">
                  <div className={cellLabel}>Ожидается</div>
                  <div className="whitespace-nowrap text-[13px] font-semibold tabular-nums sm:text-sm text-ok/80">{rub(page.totals.plan.income)}</div>
                </div>
                <div className="min-w-0 bg-card px-2.5 py-2 sm:px-4">
                  <div className={cellLabel}>К оплате</div>
                  <div className="whitespace-nowrap text-[13px] font-semibold tabular-nums sm:text-sm text-bad/80">{rub(page.totals.plan.expense)}</div>
                </div>
                <div className="min-w-0 bg-card px-2.5 py-2 sm:px-4">
                  <div className={cellLabel}>Итого план</div>
                  <div className="whitespace-nowrap text-[13px] font-semibold tabular-nums sm:text-sm text-muted">{rub(page.totals.plan.net)}</div>
                </div>
              </>
            )}
          </div>
          {page.items.length === 0 ? (
            <p className="p-4 text-sm text-muted">За период ничего нет. Добавьте операцию выше — или загрузите списком из Excel.</p>
          ) : (
            <ul className="divide-y divide-line">
              {page.items.map((row) => {
                const op = row.operation
                const isPlan = row.status !== 'fact'
                const transfer = row.type === 'transfer'
                const amount = row.amount === null ? null : Number(row.amount)
                const tone = transfer || amount === null ? 'text-muted' : amount > 0 ? 'text-ok' : 'text-bad'
                const title = op?.transfer
                  ? `Перевод: ${op.transfer.direction === 'out' ? `${op.account.name} → ${op.transfer.account?.name ?? '—'}` : `${op.transfer.account?.name ?? '—'} → ${op.account.name}`}`
                  : null
                const description = isPlan ? (row.comment && row.comment !== row.plan?.title ? row.comment : null) : row.comment
                return (
                  <li
                    key={row.id}
                    onClick={() => (op ? setViewing(op) : setPlan(row))}
                    className={`grid cursor-pointer grid-cols-[1.25rem_2.75rem_minmax(0,1fr)_auto] items-start gap-x-3 px-4 py-2.5 text-sm hover:bg-line/40 sm:grid-cols-[1.25rem_4rem_minmax(0,1fr)_auto_auto] sm:items-center ${op?.before_opening ? 'opacity-60' : ''} ${isPlan ? 'bg-bg/40' : ''} ${op && selected.has(op.op_id) ? 'bg-accent/5' : ''}`}
                    data-testid={isPlan ? 'plan-row' : 'op-row'}
                  >
                    <span onClick={(e) => e.stopPropagation()}>
                      {op && !op.transfer && (
                        <input type="checkbox" checked={selected.has(op.op_id)} onChange={() => toggle(op.op_id)} aria-label="Выбрать для изменения" className="h-4 w-4 accent-accent" data-testid="op-select" />
                      )}
                    </span>
                    <span className={`tabular-nums ${row.status === 'overdue' ? 'text-bad' : 'text-muted'}`}>{fmtDay(row.day).slice(0, 5)}</span>
                    <span className="min-w-0">
                      <span className="font-medium">{title ?? <CategoryName row={row} />}</span>
                      {op?.category_by === 'rule' && <span className="ml-1 text-xs text-faint" data-testid="op-by-rule">· по правилу</span>}
                      {row.status === 'planned' && (
                        <span className="ml-2 inline-block">
                          <Badge tone="accent">{row.source === 'ozon_forecast' ? 'прогноз' : 'план'}</Badge>
                        </span>
                      )}
                      {row.status === 'overdue' && (
                        <span className="ml-2 inline-block" data-testid="plan-overdue">
                          <Badge tone="bad">просрочен</Badge>
                        </span>
                      )}
                      {op && SOURCE[op.source] && (
                        <span className="ml-2 inline-block">
                          <Badge tone={op.source === 'ozon_payout' ? 'accent' : 'muted'}>{SOURCE[op.source]}</Badge>
                        </span>
                      )}
                      {isPlan && row.plan && <span className="block truncate text-xs text-fg/80">{row.plan.title}</span>}
                      {description && (
                        <span className="block truncate text-xs text-fg/80" data-testid="op-description">
                          {preview(description)}
                        </span>
                      )}
                      <span className="block truncate text-xs text-muted">
                        {[!transfer && row.account?.name, row.counterparty, !isPlan && row.plan && `по плану: ${row.plan.title}`].filter(Boolean).join(' · ')}
                        {op?.before_opening && ' · до начального остатка — в остаток не входит'}
                      </span>
                    </span>
                    <span className={`whitespace-nowrap text-right font-semibold tabular-nums ${tone} ${isPlan ? 'opacity-80' : ''}`} data-testid="op-amount-cell">
                      {row.plan?.estimate && isPlan && amount !== null ? '≈ ' : ''}
                      {signed(row.amount, transfer)}
                    </span>
                    <span className="col-start-4 flex justify-end gap-1 text-muted sm:col-start-auto" onClick={(e) => e.stopPropagation()}>
                      {op ? (
                        <>
                          <button type="button" onClick={() => setEditing(op)} className="rounded p-1 hover:text-fg" aria-label="Исправить" data-testid="op-edit">
                            <Pencil className="h-4 w-4" />
                          </button>
                          {op.version > 1 && (
                            <button type="button" onClick={() => setHistoryOf(op)} className="rounded p-1 hover:text-fg" aria-label="История исправлений" data-testid="op-history-btn">
                              <History className="h-4 w-4" />
                            </button>
                          )}
                          {op.source !== 'ozon_payout' && op.source !== 'bank' && (
                            <button type="button" onClick={() => remove(op)} className="rounded p-1 hover:text-bad" aria-label="Удалить" data-testid="op-delete">
                              <Trash2 className="h-4 w-4" />
                            </button>
                          )}
                        </>
                      ) : (
                        row.source !== 'ozon_forecast' && (
                          <button type="button" onClick={() => setPaying(row)} className="inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-xs hover:border-accent hover:text-accent" data-testid="plan-paid">
                            <Check className="h-3.5 w-3.5" /> Оплачено
                          </button>
                        )
                      )}
                    </span>
                  </li>
                )
              })}
            </ul>
          )}
          {page.total > page.items.length && (
            <p className="border-t border-line px-4 py-2 text-xs text-muted">
              Показаны первые {page.items.length} из {page.total} — уточните период или фильтры; в Excel — все.
            </p>
          )}
        </section>
      )}
      {page?.plan_note && <p className="text-xs text-faint" data-testid="ops-plan-note">{page.plan_note}</p>}
      <p className="text-xs text-faint">
        Факт — операции по счетам; план и просроченные — неоплаченные платежи «Календаря»: «Оплачено» превращает их в операцию. Переводы между своими
        счетами не считаются ни приходом, ни расходом. Исправленная или удалённая операция остаётся в истории.
      </p>

      <ImportPanel params={{ date_from: range.from, date_to: range.to > today ? today : range.to }} onApplied={changed} />

      {adding && (
        <Modal title="Новая операция" onClose={() => setAdding(false)}>
          <OperationForm accounts={accounts} categories={categories} onSaved={changed} onCategoryCreated={onChanged} onCancel={() => setAdding(false)} />
        </Modal>
      )}
      {viewing && (
        <Modal title={viewing.category?.name ?? (viewing.transfer ? 'Перевод' : 'Операция')} onClose={() => setViewing(null)}>
          <OperationCard
            op={viewing}
            onEdit={() => {
              setEditing(viewing)
              setViewing(null)
            }}
            onHistory={() => {
              setHistoryOf(viewing)
              setViewing(null)
            }}
            onDelete={() => {
              setViewing(null)
              remove(viewing)
            }}
          />
        </Modal>
      )}
      {editing && (
        <Modal title={editing.transfer ? 'Перевод' : editing.source === 'ozon_payout' ? 'Выплата Ozon' : editing.source === 'bank' ? 'Операция из банка' : 'Исправить операцию'} onClose={() => setEditing(null)}>
          <OperationForm
            accounts={accounts}
            categories={categories}
            initial={editing}
            onCancel={() => setEditing(null)}
            onCategoryCreated={onChanged}
            onSaved={(sg) => {
              setEditing(null)
              setSuggestion(sg ?? null)
              changed()
            }}
          />
        </Modal>
      )}
      {historyOf && (
        <Modal title="История исправлений" onClose={() => setHistoryOf(null)}>
          <HistoryView opId={historyOf.op_id} />
        </Modal>
      )}
      {plan && (
        <Modal title={plan.plan?.title ?? 'Плановый платёж'} onClose={() => setPlan(null)}>
          <PlanCard
            row={plan}
            onPaid={() => {
              setPaying(plan)
              setPlan(null)
            }}
          />
        </Modal>
      )}
      {paying && (
        <Modal title="Оплачено" onClose={() => setPaying(null)}>
          <PaidForm
            row={paying}
            accounts={accounts}
            onCancel={() => setPaying(null)}
            onDone={() => {
              setNotice(`«${paying.plan?.title}» оплачен — операция добавлена, в календаре платёж отмечен оплаченным.`)
              setPaying(null)
              changed()
            }}
          />
        </Modal>
      )}
    </div>
  )
}

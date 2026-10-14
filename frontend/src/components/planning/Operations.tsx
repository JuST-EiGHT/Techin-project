'use client'
// «Планирование → Операции» (шаг 3.8): быстрый ввод, список с фильтрами, история исправлений, загрузка списком.
import { Download, History, Pencil, Trash2, Upload } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { type Account, type Category, type Operation, type OperationsPage, parseAmount } from '@/components/planning/types'
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
export const input = 'w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-accent'
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
}: {
  accounts: Account[]
  categories: Category[]
  initial?: Operation
  onSaved: () => void
  onCancel?: () => void
}) {
  const today = todayMsk()
  const active = useMemo(() => accounts.filter((a) => !a.archived), [accounts])
  const ozon = initial?.source === 'ozon_payout'
  const [type, setType] = useState<OpType>(initial ? (initial.transfer ? 'transfer' : Number(initial.amount) > 0 ? 'income' : 'expense') : 'expense')
  const [amount, setAmount] = useState(initial ? String(Math.abs(Number(initial.amount))).replace('.', ',') : '')
  const [day, setDay] = useState(initial?.day ?? today)
  const [account, setAccount] = useState(initial?.account.id ?? active[0]?.id ?? '')
  const [toAccount, setToAccount] = useState(active.find((a) => a.id !== account)?.id ?? '')
  const [categoryText, setCategoryText] = useState(initial?.category?.name ?? '') // выбрать из списка или вписать новую
  const [typed, setTyped] = useState(false) // вписали руками, а не выбрали из списка
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
  const cats = categories.filter((c) => (!c.archived && c.kind === type) || c.id === initial?.category?.id)
  const match = categoryText.trim() ? cats.find((c) => sameName(c.name, categoryText)) : undefined
  // статья: из списка — по id; вписана новая — по названию, сервис сохранит её в список (такая уже есть — возьмёт её)
  const categoryBody = () => (match ? { category_id: match.id } : categoryText.trim() ? { category_name: categoryText.trim() } : { category_id: null })

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    const value = parseAmount(amount)
    if (!ozon && (value === null || Number(value) <= 0)) return setError('Сумма — числом больше нуля, например 12 000,50')
    setSaving(true)
    try {
      if (initial) {
        const body = ozon
          ? { ...categoryBody(), counterparty, comment }
          : initial.transfer
            ? { amount: value, day, comment }
            : { type, amount: value, day, account_id: account, ...categoryBody(), counterparty, comment }
        await api.patch(`/planning/operations/${initial.op_id}`, body)
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
        setNotice(
          data.category_note === 'created'
            ? `Добавлено. Новая статья «${data.category?.name}» сохранена — дальше её можно выбрать из списка.`
            : data.category_note === 'restored'
              ? `Добавлено. Статья «${data.category?.name}» возвращена из архива.`
              : 'Добавлено.',
        )
      }
      onSaved()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const lockMoney = ozon // у выплаты Ozon сумма, дата и счёт — из данных Ozon
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
                setCategoryText('')
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
        <p className="text-xs text-muted">Выплата Ozon обновляется сама по данным Ozon — здесь можно поменять статью, контрагента и комментарий.</p>
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
            <label className="block text-sm">
              <span className="text-muted">Статья</span>
              <input
                value={categoryText}
                list={`op-cats-${initial?.op_id ?? 'new'}`}
                onChange={(e) => {
                  // из списка браузер вставляет значение целиком (insertReplacementText) — это выбор, а не ввод
                  const kind = (e.nativeEvent as InputEvent).inputType
                  setTyped(kind !== undefined && kind !== 'insertReplacementText')
                  setCategoryText(e.target.value)
                  setNotice(null)
                }}
                onBlur={() => match && setCategoryText(match.name)} // такая уже есть — ставим её название
                placeholder="Выберите или впишите новую"
                className={`${input} mt-1`}
                data-testid="op-category"
              />
              <datalist id={`op-cats-${initial?.op_id ?? 'new'}`}>
                {cats.map((c) => (
                  <option key={c.id} value={c.name} />
                ))}
              </datalist>
              <CategoryHint text={categoryText} match={match} typed={typed} />
            </label>
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
        {op.source !== 'ozon_payout' && (
          <button type="button" onClick={onDelete} className="rounded-lg border border-line px-4 py-2 text-sm text-muted hover:border-bad hover:text-bad">
            Удалить
          </button>
        )}
      </div>
    </div>
  )
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-bg/70 sm:items-center sm:p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-t-xl border border-line bg-card p-5 shadow-xl sm:rounded-xl"
      >
        <div className="mb-3 flex items-start gap-3">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button type="button" onClick={onClose} className="ml-auto rounded-md px-2 py-1 text-muted hover:text-fg" aria-label="Закрыть">
            ✕
          </button>
        </div>
        {children}
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

// ---------- вкладка ----------

export function OperationsTab({ accounts, categories, onChanged }: { accounts: Account[]; categories: Category[]; onChanged: () => void }) {
  const today = todayMsk()
  const [range, setRange] = useState({ from: addDays(today, -29), to: today })
  const [accountId, setAccountId] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState<OperationsPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [viewing, setViewing] = useState<Operation | null>(null) // карточка операции
  const [editing, setEditing] = useState<Operation | null>(null)
  const [historyOf, setHistoryOf] = useState<Operation | null>(null)

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<OperationsPage>('/planning/operations', {
        params: { date_from: range.from, date_to: range.to, limit: 500, ...(accountId ? { account_id: accountId } : {}), ...(categoryId ? { category_id: categoryId } : {}), ...(search.trim() ? { search: search.trim() } : {}) },
      })
      setPage(data)
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [range, accountId, categoryId, search])

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

  const select = 'rounded-lg border border-line bg-card px-3 py-2 text-sm outline-none focus:border-accent'
  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-line bg-card p-4">
        <h2 className="mb-3 text-sm font-semibold">Новая операция</h2>
        <OperationForm accounts={accounts} categories={categories} onSaved={changed} />
      </section>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <input type="date" value={range.from} max={range.to} onChange={(e) => e.target.value && setRange({ ...range, from: e.target.value })} className={select} aria-label="С" data-testid="ops-from" />
        <span className="text-muted">—</span>
        <input type="date" value={range.to} min={range.from} max={today} onChange={(e) => e.target.value && setRange({ ...range, to: e.target.value })} className={select} aria-label="По" data-testid="ops-to" />
        <select value={accountId} onChange={(e) => setAccountId(e.target.value)} className={select} aria-label="Счёт" data-testid="ops-account">
          <option value="">Все счета</option>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
        <select value={categoryId} onChange={(e) => setCategoryId(e.target.value)} className={select} aria-label="Статья">
          <option value="">Все статьи</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Контрагент или описание" aria-label="Поиск" className={`${select} w-full min-w-0 sm:w-auto sm:flex-1`} />
      </div>

      <FormError message={error} />
      {page && (
        <section className="overflow-hidden rounded-xl border border-line bg-card" data-testid="ops-list">
          <div className="grid grid-cols-3 gap-px border-b border-line bg-line text-sm" data-testid="ops-totals">
            <div className="bg-card px-4 py-2">
              <div className="text-[11px] uppercase tracking-wider text-muted">Пришло</div>
              <div className="font-semibold tabular-nums text-ok">{rub(page.totals.income)}</div>
            </div>
            <div className="bg-card px-4 py-2">
              <div className="text-[11px] uppercase tracking-wider text-muted">Ушло</div>
              <div className="font-semibold tabular-nums text-bad">{rub(page.totals.expense)}</div>
            </div>
            <div className="bg-card px-4 py-2">
              <div className="text-[11px] uppercase tracking-wider text-muted">Итого</div>
              <div className={`font-semibold tabular-nums ${Number(page.totals.net) < 0 ? 'text-bad' : 'text-ok'}`}>{rub(page.totals.net)}</div>
            </div>
          </div>
          {page.items.length === 0 ? (
            <p className="p-4 text-sm text-muted">За период операций нет. Добавьте первую выше — или загрузите списком из Excel.</p>
          ) : (
            <ul className="divide-y divide-line">
              {page.items.map((op) => {
                const amount = Number(op.amount)
                const title = op.transfer
                  ? `Перевод: ${op.transfer.direction === 'out' ? `${op.account.name} → ${op.transfer.account?.name ?? '—'}` : `${op.transfer.account?.name ?? '—'} → ${op.account.name}`}`
                  : (op.category?.name ?? 'Без статьи')
                return (
                  <li
                    key={op.op_id}
                    onClick={() => setViewing(op)}
                    className={`grid cursor-pointer grid-cols-[2.75rem_minmax(0,1fr)_auto] items-start gap-x-3 px-4 py-2.5 text-sm hover:bg-line/40 sm:grid-cols-[4rem_minmax(0,1fr)_auto_auto] sm:items-center ${op.before_opening ? 'opacity-60' : ''}`}
                    data-testid="op-row"
                  >
                    <span className="tabular-nums text-muted">{fmtDay(op.day).slice(0, 5)}</span>
                    <span className="min-w-0">
                      <span className="font-medium">{title}</span>
                      {SOURCE[op.source] && (
                        <span className="ml-2 inline-block">
                          <Badge tone={op.source === 'ozon_payout' ? 'accent' : 'muted'}>{SOURCE[op.source]}</Badge>
                        </span>
                      )}
                      {op.comment && (
                        <span className="block truncate text-xs text-fg/80" data-testid="op-description">
                          {preview(op.comment)}
                        </span>
                      )}
                      <span className="block truncate text-xs text-muted">
                        {[!op.transfer && op.account.name, op.counterparty].filter(Boolean).join(' · ')}
                        {op.before_opening && ' · до начального остатка — в остаток не входит'}
                      </span>
                    </span>
                    <span className={`whitespace-nowrap text-right font-semibold tabular-nums ${op.transfer ? 'text-muted' : amount > 0 ? 'text-ok' : 'text-bad'}`} data-testid="op-amount-cell">
                      {op.transfer ? rub(String(Math.abs(amount))) : `${amount > 0 ? '+' : ''}${rub(op.amount)}`}
                    </span>
                    <span className="col-start-3 flex justify-end gap-1 text-muted sm:col-start-auto" onClick={(e) => e.stopPropagation()}>
                      <button type="button" onClick={() => setEditing(op)} className="rounded p-1 hover:text-fg" aria-label="Исправить" data-testid="op-edit">
                        <Pencil className="h-4 w-4" />
                      </button>
                      {op.version > 1 && (
                        <button type="button" onClick={() => setHistoryOf(op)} className="rounded p-1 hover:text-fg" aria-label="История исправлений" data-testid="op-history-btn">
                          <History className="h-4 w-4" />
                        </button>
                      )}
                      {op.source !== 'ozon_payout' && (
                        <button type="button" onClick={() => remove(op)} className="rounded p-1 hover:text-bad" aria-label="Удалить" data-testid="op-delete">
                          <Trash2 className="h-4 w-4" />
                        </button>
                      )}
                    </span>
                  </li>
                )
              })}
            </ul>
          )}
        </section>
      )}
      <p className="text-xs text-faint">Переводы между своими счетами не считаются ни приходом, ни расходом. Исправленная или удалённая операция остаётся в истории.</p>

      <ImportPanel params={{ date_from: range.from, date_to: range.to }} onApplied={changed} />

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
        <Modal title={editing.transfer ? 'Перевод' : editing.source === 'ozon_payout' ? 'Выплата Ozon' : 'Исправить операцию'} onClose={() => setEditing(null)}>
          <OperationForm
            accounts={accounts}
            categories={categories}
            initial={editing}
            onCancel={() => setEditing(null)}
            onSaved={() => {
              setEditing(null)
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
    </div>
  )
}

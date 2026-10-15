'use client'
// «Планирование → Счета → Выписка из банка» (шаг 3.11): выписка файлом в формате 1С — любой банк. Необязательно:
// «Планирование» работает и без банка. Предпросмотр → загрузить; дублей при повторной загрузке нет; поступления от Ozon
// подтверждают выплаты Ozon (одна операция); похожие ручные операции — объединить или «это разные»; сверка выплат Ozon.
import { Upload } from 'lucide-react'
import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { input } from '@/components/planning/Operations'
import { Badge, fmtDateTime, fmtDay, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'

type Ref = { id: string; name: string }
type Preview = {
  applied: boolean
  version: string | null
  encoding: string
  date_from: string | null
  date_to: string | null
  accounts: { number: string; masked: string; account: Ref | null; name: string; opening: string | null; closing: string | null }[]
  counts: { new: number; duplicate: number; ozon: number; ozon_linked: number; transfer: number; manual: number; skipped: number }
  skipped: string[]
  warnings: string[]
  already_loaded: string | null
  candidates: Ref[]
  suggested_account_id: string | null
}
type OpBrief = { op_id: string; day: string; amount: string; account: string; counterparty: string | null; category: string | null; comment: string | null }
type Match = { bank: OpBrief; manual: OpBrief }
type CheckRow = { payout_day: string | null; ozon_amount: string | null; bank_day: string | null; bank_amount: string | null; diff: string | null; status: 'ok' | 'diff' | 'no_bank' | 'waiting' | 'bank_only' }
type Check = { has_bank: boolean; rows: CheckRow[]; counts: Record<string, number> }
type StatementInfo = { id: string; filename: string; date_from: string | null; date_to: string | null; uploaded_at: string; stats: Record<string, number> }

const CHECK: Record<CheckRow['status'], { label: string; tone: 'warn' | 'muted' | 'bad' | 'accent' }> = {
  ok: { label: 'сошлось', tone: 'accent' },
  diff: { label: 'сумма разошлась', tone: 'warn' },
  no_bank: { label: 'нет в выписке', tone: 'bad' },
  waiting: { label: 'ждём выписку', tone: 'muted' },
  bank_only: { label: 'нет в данных Ozon', tone: 'muted' },
}

function Instruction() {
  return (
    <details className="text-sm" data-testid="bank-howto">
      <summary className="cursor-pointer text-accent hover:underline">Где взять файл выписки в формате 1С</summary>
      <ol className="mt-2 list-decimal space-y-1 pl-5 text-muted">
        <li>Откройте интернет-банк для бизнеса и раздел с выпиской по счёту.</li>
        <li>Выберите счёт и период — например, с начала месяца по сегодня.</li>
        <li>
          Нажмите «Экспорт», «Скачать» или «Выгрузить» и выберите формат <b className="text-fg">«1С»</b> (иногда «1С: Предприятие», «для бухгалтерии»).
          Получится текстовый файл, обычно .txt — его и загрузите сюда.
        </li>
      </ol>
      <p className="mt-2 text-xs text-faint">
        Такой файл выгружают все банки для бизнеса: Точка, Т-Банк, Сбер, Альфа, Модульбанк и другие. Можно загружать за любые периоды и по нескольку раз — одна
        и та же операция второй раз не добавится. Пароли и доступ к банку не нужны.
      </p>
    </details>
  )
}

function UploadPanel({ onApplied }: { onApplied: () => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [target, setTarget] = useState('') // '' — новый счёт или найден по номеру
  const [newName, setNewName] = useState('') // как назовём новый счёт «из банка»
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<Preview | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  async function send(f: File, apply: boolean, account = target) {
    setBusy(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('file', f)
      const { data } = await api.post<Preview>('/bank/statements/upload', form, { params: { dry_run: !apply, ...(account ? { account_id: account } : {}) } })
      if (apply) {
        setPreview(null)
        setFile(null)
        setDone(data)
        onApplied()
      } else {
        setPreview(data)
        if (!account) setNewName(data.accounts[0]?.name ?? '')
        if (!account && data.suggested_account_id) {
          setTarget(data.suggested_account_id)
          // счёт, скорее всего, тот же — показываем, что будет при загрузке в него
          const again = await api.post<Preview>('/bank/statements/upload', form, { params: { dry_run: true, account_id: data.suggested_account_id } })
          setPreview(again.data)
        }
      }
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const single = preview && preview.accounts.length === 1 && (preview.candidates.length > 0 || target)
  const c = preview?.counts
  return (
    <section className="space-y-3 rounded-xl border border-line bg-card p-4" data-testid="bank-upload">
      <div>
        <h2 className="text-sm font-semibold">Выписка из банка</h2>
        <p className="mt-0.5 text-xs text-muted">
          Необязательно. Загрузите выписку — операции и остатки будут как в банке, вводить их вручную не нужно; поступления от Ozon подтвердят выплаты Ozon.
        </p>
      </div>
      <Instruction />
      <div>
        <button type="button" onClick={() => inputRef.current?.click()} disabled={busy} className="inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="bank-pick">
          <Upload className="h-4 w-4" /> Загрузить выписку (файл 1С)
        </button>
        <input
          ref={inputRef}
          type="file"
          accept=".txt,.1c,text/plain"
          className="hidden"
          data-testid="bank-input"
          onChange={(e) => {
            const f = e.target.files?.[0]
            e.target.value = ''
            if (!f) return
            setFile(f)
            setDone(null)
            setTarget('')
            send(f, false, '')
          }}
        />
      </div>
      <FormError message={error} />
      {busy && !preview && <p className="text-sm text-muted">Читаем выписку…</p>}
      {preview && c && (
        <div className="space-y-3 rounded-lg border border-line p-3" data-testid="bank-preview">
          <div className="text-sm">
            Период: <b>{fmtDay(preview.date_from)} — {fmtDay(preview.date_to)}</b>
            {preview.already_loaded && <span className="ml-2 text-xs text-warn">этот файл уже загружали {fmtDateTime(preview.already_loaded)}</span>}
          </div>
          {preview.accounts.map((a) => (
            <div key={a.number} className="text-sm">
              Счёт <b>{a.masked}</b>
              {a.opening !== null && <span className="text-muted"> · остаток на начало {rub(a.opening)}</span>}
              {a.closing !== null && <span className="text-muted"> · на конец {rub(a.closing)}</span>}
              {!single && <span className="text-muted"> → {a.account ? `«${a.account.name}»` : `новый счёт «${a.name}»`}</span>}
            </div>
          ))}
          {single && (
            <label className="block text-sm">
              <span className="text-muted">Куда загрузить</span>
              <select
                value={target}
                onChange={(e) => {
                  setTarget(e.target.value)
                  if (file) send(file, false, e.target.value)
                }}
                className={`${input} mt-1 max-w-md`}
                data-testid="bank-target"
              >
                <option value="">Новый счёт «{newName}»</option>
                {preview.candidates.map((a) => (
                  <option key={a.id} value={a.id}>
                    В мой счёт «{a.name}»
                  </option>
                ))}
              </select>
              <span className="mt-1 block text-xs text-faint">
                Если этот счёт вы уже вели вручную — выберите его: остаток и операции окажутся в одном месте, похожие операции предложим объединить.
              </span>
            </label>
          )}
          <div className="flex flex-wrap gap-2 text-sm" data-testid="bank-counts">
            <Badge tone="accent">Новых операций: {c.new}</Badge>
            <Badge tone="muted">Уже загружены: {c.duplicate}</Badge>
            {c.ozon > 0 && (
              <Badge tone="accent">
                От Ozon: {c.ozon}
                {c.ozon_linked > 0 ? ` (подтверждают выплаты: ${c.ozon_linked})` : ''}
              </Badge>
            )}
            {c.transfer > 0 && <Badge tone="muted">Переводы между своими счетами: {c.transfer}</Badge>}
            {c.manual > 0 && <Badge tone="warn">Похожи на внесённые вручную: {c.manual}</Badge>}
            {c.skipped > 0 && <Badge tone="bad">Пропущено: {c.skipped}</Badge>}
          </div>
          {c.manual > 0 && <p className="text-xs text-muted">Похожие операции не объединяем сами — после загрузки предложим: объединить или оставить обе.</p>}
          {preview.skipped.length > 0 && (
            <ul className="max-h-40 space-y-1 overflow-y-auto text-xs text-muted">
              {preview.skipped.map((s) => (
                <li key={s}>• {s}</li>
              ))}
            </ul>
          )}
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={busy || !file || c.new === 0} onClick={() => file && send(file, true)} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="bank-apply">
              Загрузить ({c.new})
            </button>
            <button type="button" onClick={() => setPreview(null)} className="rounded-lg border border-line px-4 py-2 text-sm text-muted hover:text-fg">
              Отмена
            </button>
          </div>
        </div>
      )}
      {done && (
        <div className="space-y-1 text-sm" data-testid="bank-done">
          <p className="text-ok">
            Загружено операций: {done.counts.new}
            {done.counts.duplicate > 0 && `, уже были: ${done.counts.duplicate}`}.
          </p>
          {done.warnings.map((w) => (
            <p key={w} className="text-warn">
              {w}
            </p>
          ))}
        </div>
      )}
    </section>
  )
}

function Matches({ items, onChanged }: { items: Match[]; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null)
  async function act(path: 'merge' | 'dismiss', m: Match) {
    setError(null)
    try {
      await api.post(`/bank/matches/${path}`, { bank_op_id: m.bank.op_id, manual_op_id: m.manual.op_id })
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    }
  }
  if (items.length === 0) return null
  const side = (label: string, op: OpBrief) => (
    <div className="min-w-0">
      <div className="text-[11px] uppercase tracking-wider text-muted">{label}</div>
      <div className="truncate">
        {fmtDay(op.day)} · <b className="tabular-nums">{rub(op.amount)}</b> · {op.account}
      </div>
      <div className="truncate text-xs text-muted">{[op.category, op.counterparty, op.comment].filter(Boolean).join(' · ') || '—'}</div>
    </div>
  )
  return (
    <section className="overflow-hidden rounded-xl border border-warn/40 bg-card" data-testid="bank-matches">
      <div className="border-b border-line px-4 py-2">
        <h2 className="text-sm font-semibold">Возможные дубли</h2>
        <p className="text-xs text-muted">Операция из выписки похожа на внесённую вручную (сумма, дата ± 3 дня, контрагент). Объедините — останется одна, со статьёй и описанием ручной.</p>
      </div>
      <ul className="divide-y divide-line">
        {items.map((m) => (
          <li key={`${m.bank.op_id}-${m.manual.op_id}`} className="grid gap-3 px-4 py-3 text-sm sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]" data-testid="bank-match">
            {side('Из выписки', m.bank)}
            {side('Внесена вручную', m.manual)}
            <div className="flex items-center gap-2">
              <button type="button" onClick={() => act('merge', m)} className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:opacity-90" data-testid="bank-merge">
                Объединить
              </button>
              <button type="button" onClick={() => act('dismiss', m)} className="rounded-lg border border-line px-3 py-1.5 text-xs text-muted hover:text-fg" data-testid="bank-dismiss">
                Это разные
              </button>
            </div>
          </li>
        ))}
      </ul>
      <FormError message={error} />
    </section>
  )
}

function OzonCheckTable({ check }: { check: Check }) {
  if (!check.has_bank || check.rows.length === 0) return null
  const problems = (check.counts.diff ?? 0) + (check.counts.no_bank ?? 0)
  return (
    <section className="overflow-hidden rounded-xl border border-line bg-card" data-testid="ozon-check">
      <div className="border-b border-line px-4 py-2">
        <h2 className="text-sm font-semibold">Выплаты Ozon ↔ поступления на счёт</h2>
        <p className="text-xs text-muted">
          Сверка за 60 дней: выплата по данным Ozon и поступление в выписке.{' '}
          {problems > 0 ? <span className="text-warn">Расхождений: {problems} — проверьте в кабинете Ozon и в банке.</span> : 'Расхождений нет.'}
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th className="px-4 py-1.5 font-normal">Выплата Ozon</th>
              <th className="px-4 py-1.5 text-right font-normal">Сумма Ozon</th>
              <th className="px-4 py-1.5 font-normal">На счёт</th>
              <th className="px-4 py-1.5 text-right font-normal">Сумма в банке</th>
              <th className="px-4 py-1.5 font-normal" />
            </tr>
          </thead>
          <tbody>
            {check.rows.map((r, i) => (
              <tr key={i} className="border-t border-line" data-testid="ozon-check-row">
                <td className="whitespace-nowrap px-4 py-1.5">{r.payout_day ? fmtDay(r.payout_day) : '—'}</td>
                <td className="whitespace-nowrap px-4 py-1.5 text-right tabular-nums">{r.ozon_amount ? rub(r.ozon_amount) : '—'}</td>
                <td className="whitespace-nowrap px-4 py-1.5">{r.bank_day ? fmtDay(r.bank_day) : '—'}</td>
                <td className="whitespace-nowrap px-4 py-1.5 text-right tabular-nums">
                  {r.bank_amount ? rub(r.bank_amount) : '—'}
                  {r.diff && r.diff !== '0.00' && <span className="block text-xs text-warn">разница {rub(r.diff)}</span>}
                </td>
                <td className="whitespace-nowrap px-4 py-1.5">
                  <Badge tone={CHECK[r.status].tone}>{CHECK[r.status].label}</Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export function BankSection({ onChanged }: { onChanged: () => void }) {
  const [matches, setMatches] = useState<Match[]>([])
  const [check, setCheck] = useState<Check | null>(null)
  const [statements, setStatements] = useState<StatementInfo[]>([])

  const load = useCallback(async () => {
    try {
      const [m, c, s] = await Promise.all([
        api.get<Match[]>('/bank/matches'),
        api.get<Check>('/bank/ozon-check'),
        api.get<StatementInfo[]>('/bank/statements'),
      ])
      setMatches(m.data)
      setCheck(c.data)
      setStatements(s.data)
    } catch {
      // блок необязательный — без него счета работают
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const changed = () => {
    load()
    onChanged()
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted" data-testid="bank-api-link">
        Т-Банк или Точка? Подключите банк по API — операции будут приходить сами раз в час:{' '}
        <Link href="/dashboard/settings/banks" className="text-accent hover:underline">
          меню аккаунта → «Банки»
        </Link>
        . Остальные банки — выпиской файлом ниже.
      </p>
      <UploadPanel onApplied={changed} />
      <Matches items={matches} onChanged={changed} />
      {check && <OzonCheckTable check={check} />}
      {statements.length > 0 && (
        <details className="text-sm text-muted" data-testid="bank-statements">
          <summary className="cursor-pointer hover:text-fg">Загруженные выписки ({statements.length})</summary>
          <ul className="mt-2 space-y-1">
            {statements.map((s) => (
              <li key={s.id}>
                {s.filename} · {fmtDay(s.date_from)} — {fmtDay(s.date_to)} · загружена {fmtDateTime(s.uploaded_at)}
                {s.stats.new !== undefined && ` · новых ${s.stats.new}`}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

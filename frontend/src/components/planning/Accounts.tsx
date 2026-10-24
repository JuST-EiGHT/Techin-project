'use client'
// «Операции → Счета и выписки» (шаг 3.8; до 10.1 — вкладка «Планирования»). Статьи — components/planning/Categories.tsx.
import { useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { BankSection } from '@/components/planning/Bank'
import {
  ACCOUNT_KINDS,
  type Account,
  type AccountKind,
  type PlanningState,
  kindLabel,
  parseAmount,
} from '@/components/planning/types'
import { Badge, fmtDay, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { todayMsk } from '@/lib/period'

const input = 'w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-accent'
const button = 'rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent'

// ---------- счета ----------

function AccountEditor({ account, onDone }: { account?: Account; onDone: () => void }) {
  const [name, setName] = useState(account?.name ?? '')
  const [kind, setKind] = useState<AccountKind>(account?.kind ?? 'card')
  const [balance, setBalance] = useState(account?.opening ? String(Number(account.opening.amount)).replace('.', ',') : '')
  const [asOf, setAsOf] = useState(account?.opening?.as_of ?? todayMsk())
  const [error, setError] = useState<string | null>(null)

  async function save(e: React.FormEvent) {
    e.preventDefault()
    const value = balance.trim() ? parseAmount(balance) : null
    if (balance.trim() && value === null) return setError('Остаток — числом, например 150 000,50')
    setError(null)
    try {
      const body = { name, kind, ...(value !== null ? { balance: value, as_of: asOf } : {}) }
      if (account) await api.patch(`/planning/accounts/${account.id}`, body)
      else await api.post('/planning/accounts', body)
      onDone()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <form onSubmit={save} className="space-y-3 rounded-lg border border-accent/40 p-3" data-testid="account-form">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Название" aria-label="Название счёта" required className={input} data-testid="acc-name" />
        <select value={kind} onChange={(e) => setKind(e.target.value as AccountKind)} aria-label="Тип счёта" className={input}>
          {ACCOUNT_KINDS.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </select>
        <input value={balance} onChange={(e) => setBalance(e.target.value)} inputMode="decimal" placeholder="Остаток, ₽ — необязательно" aria-label="Остаток" className={input} data-testid="acc-balance" />
        <input type="date" value={asOf} max={todayMsk()} onChange={(e) => setAsOf(e.target.value)} aria-label="Остаток на начало дня" className={input} data-testid="acc-as-of" />
      </div>
      <p className="text-xs text-faint">
        Остаток — на начало выбранного дня: операции с этого дня добавятся к нему. Исправленный остаток — новая запись, прежний сохраняется в
        истории.
      </p>
      <FormError message={error} />
      <div className="flex gap-2">
        <button type="submit" className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90" data-testid="acc-save">
          Сохранить
        </button>
        <button type="button" onClick={onDone} className="rounded-lg border border-line px-4 py-2 text-sm text-muted hover:text-fg">
          Отмена
        </button>
      </div>
    </form>
  )
}

export function AccountsTab({
  accounts,
  state,
  onChanged,
}: {
  accounts: Account[]
  state: PlanningState
  onChanged: () => void
}) {
  const [editing, setEditing] = useState<string | null>(null) // id или 'new'
  const [error, setError] = useState<string | null>(null)
  const active = accounts.filter((a) => !a.archived)
  const archived = accounts.filter((a) => a.archived)

  async function patch(id: string, body: object) {
    setError(null)
    try {
      await api.patch(`/planning/accounts/${id}`, body)
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  async function ozonTo(id: string) {
    setError(null)
    try {
      await api.put('/planning/settings', { ozon_account_id: id || null })
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-line bg-card p-4" data-testid="ozon-account">
        <h2 className="text-sm font-semibold">Куда приходят выплаты Ozon</h2>
        <p className="mt-0.5 text-xs text-muted">Выплаты Ozon сами становятся поступлениями на этот счёт — вводить их не нужно. Сменили счёт — выплаты переедут на новый.</p>
        <select value={state.ozon_account_id ?? ''} onChange={(e) => ozonTo(e.target.value)} className={`${input} mt-2 max-w-sm`} data-testid="ozon-account-select">
          <option value="">Не указан — выплаты не попадают в операции</option>
          {active.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      </section>

      <FormError message={error} />
      <ul className="space-y-2" data-testid="accounts-list">
        {active.map((a) => (
          <li key={a.id} className="rounded-xl border border-line bg-card p-4" data-testid="account-row">
            {editing === a.id ? (
              <AccountEditor account={a} onDone={() => { setEditing(null); onChanged() }} />
            ) : (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                <div className="min-w-0 flex-1">
                  <div className="font-medium">
                    {a.name} {a.receives_ozon && <Badge tone="accent">выплаты Ozon</Badge>}{' '}
                    {a.source === 'bank' && <Badge tone="muted">из банка{a.number ? ` ···${a.number.slice(-4)}` : ''}</Badge>}
                  </div>
                  <div className="text-xs text-muted">
                    {kindLabel(a.kind)}
                    {a.opening ? ` · начальный остаток ${rub(a.opening.amount)} на начало ${fmtDay(a.opening.as_of)}` : ' · начальный остаток не указан'}
                  </div>
                </div>
                <div className="text-right">
                  <div className={`text-lg font-bold tabular-nums ${a.balance !== null && Number(a.balance) < 0 ? 'text-bad' : ''}`} data-testid="account-balance">
                    {a.balance === null ? '—' : rub(a.balance)}
                  </div>
                  <div className="text-xs text-faint">{a.balance === null ? 'укажите остаток — без него не посчитать' : 'сейчас'}</div>
                </div>
                <div className="flex gap-2">
                  <button type="button" onClick={() => setEditing(a.id)} className={button} data-testid="acc-edit">
                    Изменить
                  </button>
                  <button type="button" onClick={() => patch(a.id, { archived: true })} className={`${button} hover:border-bad hover:text-bad`}>
                    В архив
                  </button>
                </div>
              </div>
            )}
          </li>
        ))}
      </ul>
      {editing === 'new' ? (
        <AccountEditor onDone={() => { setEditing(null); onChanged() }} />
      ) : (
        <button type="button" onClick={() => setEditing('new')} className="text-sm text-accent hover:underline" data-testid="acc-add">
          + Добавить счёт, карту или наличные
        </button>
      )}
      <BankSection onChanged={onChanged} />
      {archived.length > 0 && (
        <details className="text-sm text-muted">
          <summary className="cursor-pointer hover:text-fg">В архиве ({archived.length})</summary>
          <ul className="mt-2 space-y-1">
            {archived.map((a) => (
              <li key={a.id} className="flex items-center gap-3">
                {a.name}
                <button type="button" onClick={() => patch(a.id, { archived: false })} className="text-accent hover:underline">
                  Вернуть
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

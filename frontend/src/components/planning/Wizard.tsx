'use client'
// Мастер первого входа в «Планирование» (шаг 3.8): у каждого вопроса — зачем спрашиваем, у вариантов — что изменится
// (правило 15). Любой вопрос можно пропустить — тогда ничего не предлагаем и считаем как есть.
import { useEffect, useMemo, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { ACCOUNT_KINDS, type AccountKind, GROUP_TITLES, type PlanningState, type TemplateCategory, kindLabel, parseAmount } from '@/components/planning/types'
import { api, errorMessage } from '@/lib/api'
import type { SupplierPayment } from '@/lib/settings'
import { SUPPLIER_OPTIONS, SUPPLIER_WHY } from '@/lib/supplier'

type Draft = { name: string; kind: AccountKind; balance: string }
type Options = { categories: TemplateCategory[]; groups_by_supplier: Record<string, string[]> }

const STEPS = ['Откуда товар', 'Счета и остатки', 'Выплаты Ozon', 'Статьи']

function Why({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-lg bg-accent/5 px-3 py-2 text-sm text-muted" data-testid="wizard-why">
      <span className="font-medium text-fg">Зачем спрашиваем: </span>
      {children}
    </p>
  )
}

function Choice({ active, onClick, title, text, testid }: { active: boolean; onClick: () => void; title: string; text?: string; testid?: string }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      onClick={onClick}
      data-testid={testid}
      className={`w-full rounded-xl border p-4 text-left ${active ? 'border-accent bg-accent/10' : 'border-line hover:border-muted'}`}
    >
      <span className="block font-medium">{title}</span>
      {text && <span className="mt-0.5 block text-xs text-faint">{text}</span>}
    </button>
  )
}

export function Wizard({ onDone }: { onDone: (state: PlanningState) => void }) {
  const [step, setStep] = useState(0)
  const [supplier, setSupplier] = useState<SupplierPayment | null>(null)
  const [days, setDays] = useState('')
  const [accounts, setAccounts] = useState<Draft[]>([{ name: 'Расчётный счёт', kind: 'checking', balance: '' }])
  const [ozon, setOzon] = useState<number | null>(0)
  const [options, setOptions] = useState<Options | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api
      .get<Options>('/planning/setup')
      .then(({ data }) => setOptions(data))
      .catch((err) => setError(errorMessage(err)))
  }, [])

  const groups = useMemo(() => options?.groups_by_supplier[supplier ?? ''] ?? ['common'], [options, supplier])
  // ответ «Откуда товар» поменялся — заново отмечаем подходящие статьи
  useEffect(() => {
    if (!options) return
    setPicked(new Set(options.categories.filter((c) => groups.includes(c.group)).map((c) => c.code)))
  }, [options, groups])

  function next() {
    setError(null)
    if (step === 0 && supplier === 'deferred') {
      const n = Number(days)
      if (!Number.isInteger(n) || n < 1 || n > 365) return setError('Отсрочка — целое число дней от 1 до 365')
    }
    if (step === 1) {
      if (accounts.some((a) => !a.name.trim())) return setError('Укажите название каждого счёта или удалите пустой')
      if (accounts.some((a) => a.balance.trim() && parseAmount(a.balance) === null)) return setError('Остаток — числом, например 150 000,50')
      if (ozon !== null && ozon >= accounts.length) setOzon(accounts.length ? 0 : null)
    }
    setStep(step + 1)
  }

  /** «Пропустить настройку»: один счёт (на него же выплаты Ozon) и простые статьи «Приход» и «Расход». */
  async function quick() {
    setSaving(true)
    setError(null)
    try {
      const { data } = await api.post<PlanningState>('/planning/setup', { quick: true })
      onDone(data)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  async function finish() {
    setSaving(true)
    setError(null)
    try {
      const { data } = await api.post<PlanningState>('/planning/setup', {
        supplier_payment: supplier,
        supplier_deferral_days: supplier === 'deferred' ? Number(days) : null,
        accounts: accounts.map((a) => ({ name: a.name.trim(), kind: a.kind, balance: a.balance.trim() ? parseAmount(a.balance) : null })),
        ozon_account: ozon,
        categories: [...picked],
      })
      onDone(data)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const update = (i: number, patch: Partial<Draft>) => setAccounts(accounts.map((a, j) => (j === i ? { ...a, ...patch } : a)))
  const input = 'rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-accent'
  const templates = options?.categories.filter((c) => c.group !== 'simple') ?? [] // «Приход» / «Расход» — для быстрого старта
  const suggested = templates.filter((c) => groups.includes(c.group))
  const others = templates.filter((c) => !groups.includes(c.group))
  const toggle = (code: string) => {
    const s = new Set(picked)
    if (s.has(code)) s.delete(code)
    else s.add(code)
    setPicked(s)
  }
  const categoryItem = (c: TemplateCategory) => (
    <label key={c.code} className={`flex gap-3 rounded-lg border border-line p-3 ${c.required ? 'opacity-80' : 'cursor-pointer hover:border-muted'}`}>
      <input
        type="checkbox"
        checked={c.required || picked.has(c.code)}
        disabled={c.required}
        onChange={() => toggle(c.code)}
        className="mt-1 accent-accent"
        data-testid={`cat-${c.code}`}
      />
      <span className="min-w-0">
        <span className="block text-sm font-medium">
          {c.name}
          {c.required && <span className="ml-1 text-xs font-normal text-faint">· нужна сервису</span>}
        </span>
        <span className="block text-xs text-muted">{c.why}</span>
      </span>
    </label>
  )

  return (
    <section className="space-y-5 rounded-xl border border-line bg-card p-5 sm:p-6" data-testid="wizard">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        {STEPS.map((title, i) => (
          <span key={title} className={`rounded-full px-2.5 py-1 ${i === step ? 'bg-accent text-white' : i < step ? 'bg-accent/15 text-accent' : 'bg-line'}`}>
            {i + 1}. {title}
          </span>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-dashed border-line px-3 py-2 text-sm" data-testid="wizard-quick">
        <span className="min-w-0 flex-1 text-muted">
          Не хотите настраивать? Начнём с одного счёта — на него же будут приходить выплаты Ozon — и простых статей «Приход» и «Расход».
          Свои статьи добавите, когда понадобятся.
        </span>
        <button type="button" onClick={quick} disabled={saving} className="whitespace-nowrap font-medium text-accent hover:underline disabled:opacity-60" data-testid="w-quick">
          Пропустить настройку →
        </button>
      </div>

      {step === 0 && (
        <div className="space-y-3" data-testid="wizard-step-supplier">
          <h2 className="text-lg font-semibold">Откуда у вас товар?</h2>
          <Why>{SUPPLIER_WHY}</Why>
          <div role="radiogroup" aria-label="Откуда товар" className="grid gap-2 sm:grid-cols-2">
            {SUPPLIER_OPTIONS.map((o) => (
              <Choice key={o.value} active={supplier === o.value} onClick={() => setSupplier(o.value)} title={o.label} text={o.effect} testid={`w-supplier-${o.value}`} />
            ))}
          </div>
          {supplier === 'deferred' && (
            <label className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted">Отсрочка оплаты, дней</span>
              <input value={days} inputMode="numeric" onChange={(e) => setDays(e.target.value.replace(/\D/g, ''))} className={`${input} w-24`} data-testid="w-deferral" />
            </label>
          )}
        </div>
      )}

      {step === 1 && (
        <div className="space-y-3" data-testid="wizard-step-accounts">
          <h2 className="text-lg font-semibold">Где лежат деньги и сколько их сейчас?</h2>
          <Why>
            От этой суммы строится прогноз остатка: хватит ли денег до следующей выплаты Ozon. Сумма видна только вам. Не знаете точно — оставьте
            пустым, укажете потом.
          </Why>
          <div className="space-y-2">
            {accounts.map((a, i) => (
              <div key={i} className="grid grid-cols-2 gap-2 rounded-lg border border-line p-2 sm:grid-cols-[minmax(0,1fr)_10rem_10rem_auto] sm:border-0 sm:p-0">
                <input value={a.name} onChange={(e) => update(i, { name: e.target.value })} placeholder="Название" aria-label="Название счёта" className={`${input} col-span-2 w-full sm:col-span-1`} data-testid={`w-acc-name-${i}`} />
                <select value={a.kind} onChange={(e) => update(i, { kind: e.target.value as AccountKind })} aria-label="Тип счёта" className={`${input} w-full`}>
                  {ACCOUNT_KINDS.map((k) => (
                    <option key={k.value} value={k.value}>
                      {k.label}
                    </option>
                  ))}
                </select>
                <input value={a.balance} onChange={(e) => update(i, { balance: e.target.value })} inputMode="decimal" placeholder="Остаток, ₽" aria-label="Остаток сейчас" className={`${input} w-full`} data-testid={`w-acc-balance-${i}`} />
                {accounts.length > 1 && (
                  <button type="button" onClick={() => setAccounts(accounts.filter((_, j) => j !== i))} className="col-span-2 justify-self-end rounded-lg px-2 text-sm text-muted hover:text-bad sm:col-span-1" aria-label="Удалить счёт">
                    ✕<span className="sm:hidden"> Удалить</span>
                  </button>
                )}
              </div>
            ))}
          </div>
          <button type="button" onClick={() => setAccounts([...accounts, { name: '', kind: 'card', balance: '' }])} className="text-sm text-accent hover:underline" data-testid="w-acc-add">
            + Добавить счёт, карту или наличные
          </button>
          <p className="text-xs text-faint">Остаток — на начало сегодняшнего дня: операции с сегодняшнего дня добавятся к нему.</p>
        </div>
      )}

      {step === 2 && (
        <div className="space-y-3" data-testid="wizard-step-ozon">
          <h2 className="text-lg font-semibold">Куда приходят выплаты Ozon?</h2>
          <Why>Чтобы выплаты Ozon сами появлялись на этом счёте и в прогнозе — вводить их не нужно.</Why>
          <div role="radiogroup" aria-label="Куда приходят выплаты Ozon" className="grid gap-2 sm:grid-cols-2">
            {accounts.map((a, i) => (
              <Choice key={i} active={ozon === i} onClick={() => setOzon(i)} title={a.name || 'Без названия'} text={`${kindLabel(a.kind)} — выплаты Ozon будут поступлениями на него`} testid={`w-ozon-${i}`} />
            ))}
            <Choice active={ozon === null} onClick={() => setOzon(null)} title="Пока не указывать" text="Выплаты Ozon не попадут в операции — выбрать счёт можно потом в «Операциях» → «Счета и выписки»" testid="w-ozon-none" />
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="space-y-3" data-testid="wizard-step-categories">
          <h2 className="text-lg font-semibold">Статьи доходов и расходов</h2>
          <Why>
            По статьям видно, на что уходят деньги. Мы отметили подходящие {supplier ? 'под ваш ответ «Откуда товар»' : 'всем'} — снимите лишние;
            добавить и переименовать можно потом.
          </Why>
          {(['common', 'resale', 'production'] as const)
            .filter((g) => groups.includes(g))
            .map((g) => (
              <div key={g} className="space-y-2">
                <h3 className="text-xs uppercase tracking-wider text-muted">{GROUP_TITLES[g]}</h3>
                <div className="grid gap-2 sm:grid-cols-2">{suggested.filter((c) => c.group === g).map(categoryItem)}</div>
              </div>
            ))}
          {others.length > 0 && (
            <details className="text-sm">
              <summary className="cursor-pointer text-muted hover:text-fg">Другие статьи ({others.length}) — если нужны</summary>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">{others.map(categoryItem)}</div>
            </details>
          )}
        </div>
      )}

      <FormError message={error} />
      <div className="flex flex-wrap items-center gap-2">
        {step > 0 && (
          <button type="button" onClick={() => setStep(step - 1)} className="rounded-lg border border-line px-4 py-2 text-sm text-muted hover:text-fg" data-testid="w-back">
            ← Назад
          </button>
        )}
        <span className="ml-auto" />
        {step === 0 && (
          <button type="button" onClick={() => { setSupplier(null); setStep(1) }} className="text-sm text-muted hover:text-fg" data-testid="w-skip">
            Пропустить
          </button>
        )}
        {step === 1 && (
          <button type="button" onClick={() => { setAccounts(accounts.map((a) => ({ ...a, balance: '' }))); setStep(2) }} className="text-sm text-muted hover:text-fg" data-testid="w-skip">
            Пропустить остатки
          </button>
        )}
        {step < 3 ? (
          <button type="button" onClick={next} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90" data-testid="w-next">
            Далее →
          </button>
        ) : (
          <button type="button" onClick={finish} disabled={saving || !options} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="w-finish">
            {saving ? 'Сохраняем…' : 'Готово'}
          </button>
        )}
      </div>
    </section>
  )
}

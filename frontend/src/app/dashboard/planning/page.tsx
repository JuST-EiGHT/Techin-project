'use client'
// «Планирование» (шаги 3.8–3.9) — необязательный раздел: свои счета, операции и статьи, платёжный календарь с прогнозом
// остатка. Работает без банка. Обзор — шаг 3.10.
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { AccountsTab, CategoriesTab } from '@/components/planning/Accounts'
import { CalendarTab } from '@/components/planning/Calendar'
import { OperationsTab } from '@/components/planning/Operations'
import type { Account, Category, PlanningState, TemplateCategory } from '@/components/planning/types'
import { Wizard } from '@/components/planning/Wizard'
import { rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'

type Tab = 'operations' | 'calendar' | 'accounts' | 'categories'
const TABS: { value: Tab; label: string }[] = [
  { value: 'operations', label: 'Операции' },
  { value: 'calendar', label: 'Календарь' },
  { value: 'accounts', label: 'Счета' },
  { value: 'categories', label: 'Статьи' },
]

export default function PlanningPage() {
  return (
    <Suspense>
      <Planning />
    </Suspense>
  )
}

function Invite({ state, onStart, onEnable }: { state: PlanningState; onStart: () => void; onEnable: () => void }) {
  return (
    <section className="rounded-xl border border-line bg-card p-6 sm:p-8" data-testid="planning-invite">
      <h2 className="text-xl font-semibold">Когда придут выплаты Ozon и хватит ли денег на закупки, зарплату и налоги</h2>
      <ul className="mt-4 space-y-2 text-sm text-muted">
        <li>• Все ваши счета, карты и наличные в одном месте — сколько денег сейчас.</li>
        <li>• Выплаты Ozon появляются сами; свои расходы и доходы — вручную или списком из Excel.</li>
        <li>• Платёжный календарь и прогноз остатка: аренда, зарплата, налоги, оплата поставщику — видно, где может не хватить денег.</li>
      </ul>
      <p className="mt-4 text-sm text-faint">
        Раздел необязательный: «Финансы Ozon» работают и без него. Банк подключать не нужно — остатки и операции можно вести вручную.
      </p>
      {state.setup_done ? (
        <button type="button" onClick={onEnable} className="mt-5 rounded-lg bg-accent px-5 py-2.5 text-sm font-medium text-white hover:opacity-90" data-testid="planning-enable">
          Включить снова — данные сохранены
        </button>
      ) : (
        <button type="button" onClick={onStart} className="mt-5 rounded-lg bg-accent px-5 py-2.5 text-sm font-medium text-white hover:opacity-90" data-testid="planning-start">
          Начать — 4 коротких вопроса
        </button>
      )}
    </section>
  )
}

function Planning() {
  const params = useSearchParams()
  const router = useRouter()
  const raw = params.get('tab')
  const tab: Tab = TABS.some((t) => t.value === raw) ? (raw as Tab) : 'operations'
  const [state, setState] = useState<PlanningState | null>(null)
  const [wizard, setWizard] = useState(false)
  const [accounts, setAccounts] = useState<Account[]>([])
  const [categories, setCategories] = useState<Category[]>([])
  const [templates, setTemplates] = useState<TemplateCategory[]>([])
  const [loaded, setLoaded] = useState(false) // счета и статьи загружены — без них форма операции не знает счетов
  const [error, setError] = useState<string | null>(null)

  const loadData = useCallback(async () => {
    try {
      const [a, c, s, t] = await Promise.all([
        api.get<Account[]>('/planning/accounts'),
        api.get<Category[]>('/planning/categories'),
        api.get<PlanningState>('/planning/settings'),
        api.get<{ categories: TemplateCategory[] }>('/planning/setup'),
      ])
      setAccounts(a.data)
      setCategories(c.data)
      setState(s.data)
      setTemplates(t.data.categories)
      setLoaded(true)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    api
      .get<PlanningState>('/planning/settings')
      .then(({ data }) => {
        setState(data)
        if (data.enabled) loadData()
      })
      .catch((err) => setError(errorMessage(err)))
  }, [loadData])

  async function enable() {
    try {
      const { data } = await api.put<PlanningState>('/planning/settings', { enabled: true })
      setState(data)
      loadData()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const active = accounts.filter((a) => !a.archived)
  const known = active.filter((a) => a.balance !== null)
  const total = known.reduce((sum, a) => sum + Number(a.balance), 0)

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Планирование</h1>
        <p className="mt-1 text-sm text-muted">
          Деньги на ваших счетах: что пришло и что ушло (ДДС). Это не P&amp;L — прибыль по начислениям Ozon в{' '}
          <Link href="/dashboard/finance" className="text-accent hover:underline">
            «Финансах»
          </Link>
          .
        </p>
      </div>
      <FormError message={error} />
      {state === null && !error && <p className="text-sm text-muted">Загрузка…</p>}
      {state && !state.enabled &&
        (wizard ? (
          <Wizard
            onDone={(s) => {
              setState(s)
              setWizard(false)
              loadData()
            }}
          />
        ) : (
          <Invite state={state} onStart={() => setWizard(true)} onEnable={enable} />
        ))}

      {state?.enabled && !loaded && !error && <p className="text-sm text-muted">Загрузка…</p>}
      {state?.enabled && loaded && (
        <>
          <div className="flex flex-wrap gap-px overflow-hidden rounded-xl border border-line bg-line" data-testid="balances">
            {active.map((a) => (
              <div key={a.id} className="min-w-[9rem] flex-1 bg-card px-4 py-3">
                <div className="truncate text-[11px] uppercase tracking-wider text-muted">{a.name}</div>
                <div className={`mt-1 text-lg font-bold tabular-nums ${a.balance !== null && Number(a.balance) < 0 ? 'text-bad' : ''}`}>
                  {a.balance === null ? '—' : rub(a.balance)}
                </div>
                {a.balance === null && <div className="text-xs text-faint">остаток не указан</div>}
              </div>
            ))}
            {active.length > 1 && (
              <div className="min-w-[9rem] flex-1 bg-card px-4 py-3" data-testid="balance-total">
                <div className="text-[11px] uppercase tracking-wider text-muted">Всего</div>
                <div className="mt-1 text-lg font-bold tabular-nums">{known.length ? rub(String(total)) : '—'}</div>
                {known.length < active.length && <div className="text-xs text-faint">без счетов без остатка</div>}
              </div>
            )}
          </div>

          <div className="flex gap-1 overflow-x-auto border-b border-line" role="tablist" aria-label="Раздел">
            {TABS.map((t) => (
              <button
                key={t.value}
                type="button"
                role="tab"
                aria-selected={tab === t.value}
                onClick={() => router.replace(t.value === 'operations' ? '/dashboard/planning' : `/dashboard/planning?tab=${t.value}`, { scroll: false })}
                className={`-mb-px whitespace-nowrap border-b-2 px-4 py-2 text-sm ${tab === t.value ? 'border-accent font-medium text-fg' : 'border-transparent text-muted hover:text-fg'}`}
                data-testid={`tab-${t.value}`}
              >
                {t.label}
              </button>
            ))}
          </div>

          {tab === 'operations' && <OperationsTab accounts={accounts} categories={categories} onChanged={loadData} />}
          {tab === 'calendar' && <CalendarTab accounts={accounts} categories={categories} state={state} onChanged={loadData} />}
          {tab === 'accounts' && <AccountsTab accounts={accounts} state={state} onChanged={loadData} />}
          {tab === 'categories' && <CategoriesTab categories={categories} templates={templates} onChanged={loadData} />}
        </>
      )}
    </div>
  )
}

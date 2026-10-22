'use client'
// Общая часть страниц «Операций» и «Планирования» (шаг 10.1; до него — вкладки одной страницы /dashboard/planning, 3.8–3.10):
// загрузка счетов и статей, приглашение и мастер, пока раздел не включён, остатки по счетам и вкладки-ссылки раздела.
// Оба раздела необязательные и включаются вместе — одной настройкой «Планирования».
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Suspense, useCallback, useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import type { Account, Category, PlanningState, TemplateCategory } from '@/components/planning/types'
import { Wizard } from '@/components/planning/Wizard'
import { rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { NAV } from '@/lib/nav'

export type PlanningData = {
  state: PlanningState
  accounts: Account[]
  categories: Category[]
  templates: TemplateCategory[]
  reload: () => void
}

function Invite({ state, onStart, onEnable }: { state: PlanningState; onStart: () => void; onEnable: () => void }) {
  return (
    <section className="rounded-xl border border-line bg-card p-6 sm:p-8" data-testid="planning-invite">
      <h2 className="text-xl font-semibold">Когда придут выплаты Ozon и хватит ли денег на закупки, зарплату и налоги</h2>
      <ul className="mt-4 space-y-2 text-sm text-muted">
        <li>• Все ваши счета, карты и наличные в одном месте — сколько денег сейчас.</li>
        <li>• Выплаты Ozon появляются сами; свои расходы и доходы — вручную или списком из Excel.</li>
        <li>• Платёжный календарь и прогноз остатка: аренда, зарплата, налоги, оплата поставщику — видно, где может не хватить денег.</li>
        <li>• ДДС по статьям: сколько пришло и ушло за месяц или неделю.</li>
      </ul>
      <p className="mt-4 text-sm text-faint">
        «Операции» и «Планирование» необязательные и включаются вместе: «Финансы Ozon» работают и без них. Банк подключать не нужно —
        остатки и операции можно вести вручную.
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

/** Вкладки раздела — ссылки на его страницы из общего меню (nav.ts). */
function SectionTabs({ section }: { section: string }) {
  const pathname = usePathname()
  const items = NAV.find((s) => s.label === section)?.items ?? []
  return (
    <nav className="flex gap-1 overflow-x-auto border-b border-line" aria-label={section} data-testid="section-tabs">
      {items.map((t) => {
        const active = pathname === t.href
        return (
          <Link
            key={t.href}
            href={t.href}
            aria-current={active ? 'page' : undefined}
            className={`-mb-px whitespace-nowrap border-b-2 px-4 py-2 text-sm ${active ? 'border-accent font-medium text-fg' : 'border-transparent text-muted hover:text-fg'}`}
          >
            {t.label}
          </Link>
        )
      })}
    </nav>
  )
}

function Balances({ accounts }: { accounts: Account[] }) {
  const active = accounts.filter((a) => !a.archived)
  const known = active.filter((a) => a.balance !== null)
  const total = known.reduce((sum, a) => sum + Number(a.balance), 0)
  return (
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
  )
}

const INTRO = {
  Операции: 'Деньги на ваших счетах: что пришло и что ушло — свои расходы и доходы, выплаты Ozon, выписки банков.',
  Планирование: 'Хватит ли денег: прогноз остатка, платёжный календарь и движение денег по статьям (ДДС).',
}

export function PlanningShell({ section, children }: { section: keyof typeof INTRO; children: (data: PlanningData) => React.ReactNode }) {
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

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">{section}</h1>
        <p className="mt-1 text-sm text-muted">
          {INTRO[section]} Это не P&amp;L — прибыль по начислениям Ozon в{' '}
          <Link href="/dashboard/finance/pnl" className="text-accent hover:underline">
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
          <Balances accounts={accounts} />
          <SectionTabs section={section} />
          {/* страницы читают фильтры из адреса (useSearchParams) */}
          <Suspense>{children({ state, accounts, categories, templates, reload: loadData })}</Suspense>
        </>
      )}
    </div>
  )
}

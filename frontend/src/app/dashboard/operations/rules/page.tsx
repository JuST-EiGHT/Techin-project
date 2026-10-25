'use client'
// «Операции → Правила разнесения» (шаг 3.12; до 10.1 — внизу «Статей»): операция из банка сама получает статью.
// 10.20: вкладки (?tab=) — «Правила», «Журнал применения», «План-факт».
import { usePathname, useRouter, useSearchParams } from 'next/navigation'

import { PlanFact } from '@/components/planning/PlanFact'
import { RuleJournal } from '@/components/planning/RuleJournal'
import { RulesSection } from '@/components/planning/Rules'
import { type PlanningData, PlanningShell } from '@/components/planning/Shell'

const TABS = [
  { value: 'rules', label: 'Правила' },
  { value: 'journal', label: 'Журнал применения' },
  { value: 'plan-fact', label: 'План-факт' },
] as const
type Tab = (typeof TABS)[number]['value']

function Rules({ categories, accounts, reload }: PlanningData) {
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const raw = params.get('tab')
  const tab: Tab = TABS.some((t) => t.value === raw) ? (raw as Tab) : 'rules'
  return (
    <div className="space-y-4">
      <div className="flex gap-1 overflow-x-auto rounded-lg border border-line bg-card p-1 sm:w-fit" role="tablist" aria-label="Правила" data-testid="rules-tabs">
        {TABS.map((t) => (
          <button
            key={t.value}
            type="button"
            role="tab"
            aria-selected={tab === t.value}
            onClick={() => router.replace(t.value === 'rules' ? pathname : `${pathname}?tab=${t.value}`, { scroll: false })}
            className={`whitespace-nowrap rounded-md px-3 py-1.5 text-sm ${tab === t.value ? 'bg-accent text-white' : 'text-muted hover:text-fg'}`}
            data-testid={`rules-tab-${t.value}`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'rules' && <RulesSection categories={categories} accounts={accounts} onChanged={reload} />}
      {tab === 'journal' && <RuleJournal />}
      {tab === 'plan-fact' && <PlanFact />}
    </div>
  )
}

export default function OperationsRulesPage() {
  return <PlanningShell section="Операции">{(data) => <Rules {...data} />}</PlanningShell>
}

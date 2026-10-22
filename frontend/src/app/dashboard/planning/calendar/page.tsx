'use client'
// «Планирование → Календарь» (шаг 3.9): плановые платежи, налоги и прогноз остатка.
import { CalendarTab } from '@/components/planning/Calendar'
import { PlanningShell } from '@/components/planning/Shell'

export default function PlanningCalendarPage() {
  return (
    <PlanningShell section="Планирование">
      {({ state, accounts, categories, reload }) => <CalendarTab accounts={accounts} categories={categories} state={state} onChanged={reload} />}
    </PlanningShell>
  )
}

'use client'
// «Планирование → Обзор» (шаг 3.10): деньги сейчас, прогноз остатка, разрывы, ближайшие платежи.
// Календарь и ДДС — свои страницы (шаг 10.1), операции, статьи, правила и счета — раздел «Операции».
import { OverviewTab } from '@/components/planning/Overview'
import { PlanningShell } from '@/components/planning/Shell'

export default function PlanningOverviewPage() {
  return (
    <PlanningShell section="Планирование">
      {({ state, accounts, reload }) => <OverviewTab accounts={accounts} horizonDays={state.horizon_days} onChanged={reload} />}
    </PlanningShell>
  )
}

'use client'
// «Планирование → ДДС» (шаг 3.10): движение денег по статьям за месяц или неделю; шаг 10.24 — план и прогноз, счёт.
import { CashFlowTab } from '@/components/planning/CashFlow'
import { PlanningShell } from '@/components/planning/Shell'

export default function PlanningCashFlowPage() {
  return <PlanningShell section="Планирование">{({ accounts }) => <CashFlowTab accounts={accounts} />}</PlanningShell>
}

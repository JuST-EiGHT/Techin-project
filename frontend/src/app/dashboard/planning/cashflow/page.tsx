'use client'
// «Планирование → ДДС» (шаг 3.10): движение денег по статьям за месяц или неделю.
import { CashFlowTab } from '@/components/planning/CashFlow'
import { PlanningShell } from '@/components/planning/Shell'

export default function PlanningCashFlowPage() {
  return <PlanningShell section="Планирование">{() => <CashFlowTab />}</PlanningShell>
}

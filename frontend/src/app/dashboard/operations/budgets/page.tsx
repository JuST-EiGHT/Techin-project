'use client'
// «Операции → Бюджеты и сценарии» (шаг 10.21): план по статьям, план-факт, заполнение, шаблон Excel, сценарии и прогноз.
import { BudgetsPage } from '@/components/planning/Budgets'
import { PlanningShell } from '@/components/planning/Shell'

export default function OperationsBudgetsPage() {
  return <PlanningShell section="Операции">{({ reload }) => <BudgetsPage onChanged={reload} />}</PlanningShell>
}

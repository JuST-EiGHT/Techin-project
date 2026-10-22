'use client'
// «Операции → Статьи» (шаг 3.8): статьи доходов и расходов.
import { CategoriesTab } from '@/components/planning/Accounts'
import { PlanningShell } from '@/components/planning/Shell'

export default function OperationsCategoriesPage() {
  return (
    <PlanningShell section="Операции">
      {({ categories, templates, reload }) => <CategoriesTab categories={categories} templates={templates} onChanged={reload} />}
    </PlanningShell>
  )
}

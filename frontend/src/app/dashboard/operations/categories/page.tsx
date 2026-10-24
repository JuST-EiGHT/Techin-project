'use client'
// «Операции → Статьи» (шаги 3.8, 10.19): доходы и расходы раздельно, подстатьи, где учитывается статья, архив.
import { CategoriesPage } from '@/components/planning/Categories'
import { PlanningShell } from '@/components/planning/Shell'

export default function OperationsCategoriesPage() {
  return (
    <PlanningShell section="Операции">
      {({ categories, templates, reload }) => <CategoriesPage categories={categories} templates={templates} onChanged={reload} />}
    </PlanningShell>
  )
}

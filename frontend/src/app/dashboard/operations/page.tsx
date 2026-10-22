'use client'
// «Операции → Все операции» (шаги 3.8, 3.12): что пришло и что ушло, быстрый ввод, фильтры, списком через Excel.
import { useSearchParams } from 'next/navigation'

import { OperationsTab } from '@/components/planning/Operations'
import { type PlanningData, PlanningShell } from '@/components/planning/Shell'

// фильтры приходят ссылками (из ДДС, обзора, уведомлений) — новый адрес заново открывает список с ними
function Operations({ accounts, categories, reload }: PlanningData) {
  const params = useSearchParams()
  return <OperationsTab key={params.toString()} accounts={accounts} categories={categories} onChanged={reload} />
}

export default function OperationsPage() {
  return <PlanningShell section="Операции">{(data) => <Operations {...data} />}</PlanningShell>
}

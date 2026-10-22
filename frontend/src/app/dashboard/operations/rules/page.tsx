'use client'
// «Операции → Правила разнесения» (шаг 3.12; до 10.1 — внизу «Статей»): операция из банка сама получает статью.
import { RulesSection } from '@/components/planning/Rules'
import { PlanningShell } from '@/components/planning/Shell'

export default function OperationsRulesPage() {
  return <PlanningShell section="Операции">{({ categories, reload }) => <RulesSection categories={categories} onChanged={reload} />}</PlanningShell>
}

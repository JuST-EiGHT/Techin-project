'use client'
// «Операции → Счета и выписки» (шаги 3.8, 3.11, 10.20): счета и остатки, куда приходят выплаты Ozon, история по счёту,
// сверка с банком, нераспознанные операции, выписки банков.
import { AccountsTab } from '@/components/planning/Accounts'
import { PlanningShell } from '@/components/planning/Shell'

export default function OperationsAccountsPage() {
  return (
    <PlanningShell section="Операции">
      {({ state, accounts, categories, reload }) => <AccountsTab accounts={accounts} categories={categories} state={state} onChanged={reload} />}
    </PlanningShell>
  )
}

'use client'
// «Операции → Счета и выписки» (шаги 3.8, 3.11): счета и остатки, куда приходят выплаты Ozon, выписки банков.
import { AccountsTab } from '@/components/planning/Accounts'
import { PlanningShell } from '@/components/planning/Shell'

export default function OperationsAccountsPage() {
  return <PlanningShell section="Операции">{({ state, accounts, reload }) => <AccountsTab accounts={accounts} state={state} onChanged={reload} />}</PlanningShell>
}

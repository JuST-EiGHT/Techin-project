// Планирование (шаг 3.8): типы ответов /api/v1/planning/* и общие подписи.
import type { SupplierPayment } from '@/lib/settings'

export type AccountKind = 'checking' | 'card' | 'cash' | 'wallet' | 'other'
export type CategoryKind = 'income' | 'expense' | 'transfer'

export type PlanningState = {
  enabled: boolean
  setup_done: boolean
  ozon_account_id: string | null
  threshold: string | null
  horizon_days: number
  supplier_payment: SupplierPayment
  supplier_deferral_days: number | null
  cost_label: string
}

export type Account = {
  id: string
  name: string
  kind: AccountKind
  source: 'manual' | 'bank'
  archived: boolean
  opening: { amount: string; as_of: string } | null
  balance: string | null // null — остаток не указан, а не 0
  receives_ozon: boolean
}

export type Category = {
  id: string
  name: string
  kind: CategoryKind
  code: string | null
  archived: boolean
  required: boolean
  why: string | null
}

export type TemplateCategory = {
  code: string
  name: string
  kind: CategoryKind
  group: 'common' | 'resale' | 'production' | 'simple'
  why: string
  required: boolean
}

export type Operation = {
  op_id: string
  version: number
  day: string
  amount: string
  account: { id: string; name: string }
  category: { id: string; name: string; kind: CategoryKind } | null
  counterparty: string | null
  comment: string | null
  source: 'manual' | 'import' | 'ozon_payout' | 'bank'
  transfer: { id: string; direction: 'out' | 'in'; account: { id: string; name: string } | null } | null
  before_opening: boolean
}

export type OperationsPage = {
  total: number
  items: Operation[]
  totals: { income: string; expense: string; net: string }
}

export const ACCOUNT_KINDS: { value: AccountKind; label: string }[] = [
  { value: 'checking', label: 'Расчётный счёт' },
  { value: 'card', label: 'Карта' },
  { value: 'cash', label: 'Наличные' },
  { value: 'wallet', label: 'Кошелёк' },
  { value: 'other', label: 'Другое' },
]

export const kindLabel = (kind: AccountKind) => ACCOUNT_KINDS.find((k) => k.value === kind)?.label ?? kind

export const GROUP_TITLES: Record<TemplateCategory['group'], string> = {
  common: 'Общие',
  resale: 'Перепродажа',
  production: 'Своё производство',
  simple: 'Простые',
}

/** «12 000,50» → «12000.50»; не число — null. */
export function parseAmount(text: string): string | null {
  const s = text.replace(/[\s  ₽]/g, '').replace('−', '-').replace(',', '.')
  return /^-?\d+(\.\d{1,2})?$/.test(s) ? s : null
}

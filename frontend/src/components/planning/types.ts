// Планирование (шаги 3.8–3.9): типы ответов /api/v1/planning/* и общие подписи.
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
  gen_ozon: boolean // генераторы плана (3.9): выплаты Ozon, оплата поставщику после продажи, налоги
  gen_supplier: boolean
  gen_taxes: boolean
  supplier_period: 'week' | 'month'
  supplier_day: number
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

// ---------- плановые платежи и календарь (шаг 3.9) ----------

export type PlanKind = 'income' | 'expense'
export type Period = 'once' | 'week' | 'month' | 'quarter' | 'year'
export type Ref = { id: string; name: string }

export type Plan = {
  plan_id: string
  version: number
  kind: PlanKind
  title: string
  amount: string
  period: Period
  day: number | null
  start_date: string
  end_date: string | null
  purchase_day: string | null
  enabled: boolean
  category: Ref | null
  account: Ref | null
  counterparty: string | null
  comment: string | null
  source: 'manual' | 'import' | 'supplier'
  schedule: string
  next_day: string | null
}

export type LineSource = 'ozon' | 'manual' | 'supplier' | 'tax'
export type LineStatus = 'planned' | 'overdue' | 'paid' | 'skipped' | 'disabled'
export type Range = { date_from: string; date_to: string }

export type CalendarLine = {
  key: string
  source: LineSource
  title: string
  kind: PlanKind
  day: string
  planned_day: string
  amount: string | null // + поступление, − платёж; null — сумма пока неизвестна
  plan_amount: string | null
  status: LineStatus
  estimate: boolean
  enabled: boolean
  moved: boolean
  own_amount: boolean
  skipped: boolean
  fact: { op_id: string | null; day: string; amount: string; marked: boolean } | null
  diff: string | null
  plan_id: string | null
  category: Ref | null
  account: Ref | null
  counterparty: string | null
  comment: string | null
  schedule: string | null
  note: string | null
  link: string | null
  period: Range | null
  period_label: string | null
  notice: { day: string; doc: string | null } | null
  calc: Range | null
  editable: boolean
}

export type FactLine = { op_id: string; day: string; amount: string; title: string; category: string | null; source: string }
export type ForecastDay = { day: string; income: string; expense: string; balance: string | null }
export type Gap = { date_from: string; date_to: string; min_balance: string; min_day: string; below_zero: boolean }
export type Generator = { enabled: boolean; available: boolean; period: 'week' | 'month' | null; day: number | null }

export type CalendarData = {
  date_from: string
  date_to: string
  today: string
  start_balance: string | null
  unknown_accounts: string[]
  threshold: string | null
  horizon_days: number
  generators: { ozon: Generator; supplier: Generator; taxes: Generator }
  supplier_payment: SupplierPayment
  deferral_days: number | null
  hints: { code: string; name: string; why: string }[]
  lines: CalendarLine[]
  facts: FactLine[]
  days: ForecastDay[]
  gaps: Gap[]
  first_below_zero: string | null
  first_below_threshold: string | null
  min_balance: string | null
  end_balance: string | null
  overdue: number
  warnings: string[]
}

export const SOURCE_LABELS: Record<LineSource, string> = { ozon: 'Ozon', manual: 'план', supplier: 'поставщик', tax: 'налог' }
export const PERIODS: { value: Period; label: string }[] = [
  { value: 'once', label: 'Разово' },
  { value: 'week', label: 'Раз в неделю' },
  { value: 'month', label: 'Раз в месяц' },
  { value: 'quarter', label: 'Раз в квартал' },
  { value: 'year', label: 'Раз в год' },
]
export const WEEKDAYS = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']

/** Финансы за период — расчёт суммы налога или оплаты поставщику. */
export const calcHref = (r: Range) => `/dashboard/finance?period=custom&from=${r.date_from}&to=${r.date_to}`


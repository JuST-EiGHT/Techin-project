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
  number?: string | null // номер счёта в банке (выписка, шаг 3.11)
  bank_name?: string | null
}

export type Category = {
  id: string
  name: string
  kind: CategoryKind
  code: string | null
  archived: boolean
  required: boolean
  why: string | null
  parent_id: string | null // подстатья (шаг 10.19) — статья, в которую она входит; вложенность одна
  has_children: boolean
  in_pnl: boolean // расход бизнеса в P&L (используется с шага 10.22)
  in_budget: boolean // можно задать бюджет (шаг 10.21); в ДДС статья учитывается всегда
  operations: number // действующих операций со статьёй
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
  category: { id: string; name: string; kind: CategoryKind; parent_id?: string | null } | null
  counterparty: string | null
  comment: string | null
  source: 'manual' | 'import' | 'ozon_payout' | 'bank'
  transfer: { id: string; direction: 'out' | 'in'; account: { id: string; name: string } | null } | null
  before_opening: boolean
  category_by?: 'user' | 'rule' | 'auto' | null // кто поставил статью: вручную, правило, встроенное правило (шаг 3.12)
}

// ---------- правила разнесения (шаг 3.12) ----------

export type RuleDirection = 'in' | 'out'

export type RuleSource = 'bank' | 'import' | 'manual'

export type Rule = {
  id: string
  priority: number
  direction: RuleDirection
  counterparty_inn: string | null
  counterparty: string | null
  purpose: string | null
  amount_min: string | null
  amount_max: string | null
  account?: { id: string; name: string } | null // условие «счёт» (шаг 10.20)
  source?: RuleSource | null // условие «источник»
  category: { id: string; name: string; kind: CategoryKind } | null
  enabled: boolean
  operations: number
  number?: number
  matches?: number // операций подходит под условия (часть может забрать правило выше)
  problem?: string | null // статья в архиве, правило не срабатывает — и что сделать
}

// ---------- журнал применения правил, план-факт (шаг 10.20) ----------

export type RuleRef = { id: string; number: number | null; text: string }
export type RunDetail = { op_id: string; day: string; amount: string; counterparty: string | null; before: string | null; after: string | null; by: 'rule' | 'auto' | 'user' | 'none'; rule: RuleRef | null }
export type RunProblem = { message: string; rule: RuleRef | null }
export type RuleRun = { id: string; created_at: string; trigger: string; title: string; rule: RuleRef | null; checked: number; changed: number; details: RunDetail[]; problems: RunProblem[] }
export type RulesDryRun = { checked: number; changed: number; examples: RunDetail[]; problems: RunProblem[] }

export type PlanFactOp = { op_id: string | null; day: string; amount: string; category: string | null; counterparty: string | null; account: string | null; comment: string | null }
export type PlanFactRow = {
  key: string
  title: string
  source: string
  kind: 'income' | 'expense'
  day: string
  amount: string | null
  estimate: boolean
  category: string | null
  counterparty: string | null
  status: 'paid' | 'partial' | 'overdue' | 'expected' | 'planned'
  how: 'marked' | 'auto' | 'tax' | null
  fact: PlanFactOp | null
  diff: string | null
  days_late: number | null
  suggestion: { op: PlanFactOp; reasons: string[]; category_fix: string | null } | null
}
export type PlanFactPage = { date_from: string; date_to: string; today: string; rows: PlanFactRow[]; counts: Record<'paid' | 'partial' | 'overdue' | 'expected' | 'planned' | 'suggestions', number>; totals: Record<'plan' | 'fact' | 'unpaid', string> }

// ---------- счета: история, сверка, нераспознанные (шаг 10.20) ----------

export type HistoryRow = {
  op_id: string
  day: string
  amount: string
  type: 'income' | 'expense' | 'transfer'
  category: string | null
  counterparty: string | null
  comment: string | null
  source: Operation['source']
  transfer_account: string | null
  balance: string | null
  before_opening: boolean
}
export type AccountHistory = {
  account: { id: string; name: string }
  date_from: string
  date_to: string
  opening: string | null
  closing: string | null
  opening_as_of: string | null
  totals: Record<'income' | 'expense' | 'transfer_in' | 'transfer_out', string>
  items: HistoryRow[]
}
export type Reconcile = {
  account: { id: string; name: string; number: string | null; source: 'manual' | 'bank' }
  status: 'ok' | 'diff' | 'no_bank' | 'no_opening' | 'no_overlap'
  hint: string
  day: string | null
  bank: string | null
  service: string | null
  diff: string | null
  since: string | null
  matched_until: string | null
  from_start: boolean
  adjacent: boolean
  jump: string | null
  suspects: { op_id: string; day: string; amount: string; source: Operation['source']; counterparty: string | null; comment: string | null; category: string | null; likely: boolean }[]
  days: { day: string; bank: string; service: string; diff: string; point: boolean }[]
}
export type Unrecognized = {
  op_id: string
  day: string
  amount: string
  type: 'income' | 'expense'
  account: { id: string; name: string }
  source: Operation['source']
  counterparty: string | null
  inn: string | null
  purpose: string | null
  rule: { direction: RuleDirection; counterparty_inn: string | null; counterparty: string | null }
}
export type UnrecognizedPage = { total: number; items: Unrecognized[]; by_account: { id: string; name: string; count: number }[] }

export type BuiltinRule = { code: string; title: string; when: string; category: string; why: string }
export type RulesPage = { builtin: BuiltinRule[]; rules: Rule[] }

export type RuleSuggestion = {
  direction: RuleDirection
  counterparty_inn: string | null
  counterparty: string | null
  counterparty_name: string | null
  category: { id: string; name: string }
  others: number
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
// шаг 10.24: ожидается — срок сегодня или прошёл не больше 3 дней; частично — факт меньше плана; просрочен — больше 3 дней
export type LineStatus = 'planned' | 'expected' | 'overdue' | 'paid' | 'partial' | 'skipped' | 'disabled'
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
  remaining?: string | null // частично оплачен — сколько осталось (со знаком плана)
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
  causes?: GapCauses | null
  warnings: string[]
}

/** Кассовый разрыв (шаг 10.24): с какого дня, нехватка и почему. */
export type CauseRef = { key: string; title: string; source: string; day: string; amount: string | null }
export type GapCauses = {
  day: string
  below_zero: boolean
  min_balance: string
  min_day: string
  shortfall: string
  expenses: CauseRef[]
  missing_income: CauseRef[]
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
export const calcHref = (r: Range) => `/dashboard/finance/pnl?period=custom&from=${r.date_from}&to=${r.date_to}`


// ---------- обзор и ДДС (шаг 3.10) ----------

export type SeriesDay = {
  day: string
  kind: 'fact' | 'plan' // до сегодня — факт, с сегодня — план
  balance: string | null
  ozon: string
  income: string
  expense: string
  fact: Record<'ozon' | 'income' | 'expense', string> | null // сегодня: уже пришло и ушло
}

/** Ряд графика «Остаток и движение денег» за выбранный период — GET /planning/overview/series. */
export type SeriesOut = {
  today: string
  date_from: string
  date_to: string
  threshold: string | null
  series: SeriesDay[]
}

export type Overview = {
  today: string
  date_from: string
  date_to: string
  horizon_days: number
  balance: string | null
  unknown_accounts: string[]
  accounts: { id: string; name: string; balance: string | null }[]
  threshold: string | null
  next_ozon: { day: string; amount: string | null; estimate: boolean; title: string } | null
  ozon: { enabled: boolean; has_key: boolean }
  first_below_zero: string | null
  first_below_threshold: string | null
  min_balance: string | null
  end_balance: string | null
  gaps: Gap[]
  expected: { ozon: string; income: string; expense: string; unknown: number }
  series: SeriesDay[]
  upcoming: CalendarLine[]
  to_confirm: CalendarLine[] // просрочены → оплачены частично → ожидаются (шаг 10.24)
  ozon_balance: { amount: string | null; day: string } | null
  plan_fact: { date_from: string; paid: number; partial: number; expected: number; overdue: number; plan: string; fact: string }
  causes: GapCauses | null
  empty: { has_ops: boolean; has_plans: boolean; suggestions: { code: string; name: string; why: string; have: boolean }[] }
  warnings: string[]
}

export type CashFlowPeriod = {
  date_from: string
  date_to: string
  kind: 'past' | 'current' | 'future'
  opening: string | null
  income: string // факт
  expense: string
  net: string
  forecast_income: string // оставшийся план с сегодня
  forecast_expense: string
  plan_income: string // план периода по календарю
  plan_expense: string
  closing: string | null // с прогнозом
}
// children — подстатьи (шаг 10.19): их факт уже входит в values и total статьи
export type CashFlowRow = {
  category_id: string | null
  transfer?: boolean // переводы со своими счетами — при выбранном счёте
  name: string
  section: 'income' | 'expense'
  values: string[]
  total: string
  forecast: string[]
  forecast_total: string
  plan: string[]
  plan_total: string
  children?: CashFlowRow[]
}
export type CashFlow = {
  date_from: string
  date_to: string
  granularity: 'week' | 'month'
  today: string
  account_id: string | null
  opening: string | null
  closing: string | null // с прогнозом
  income: string
  expense: string
  net: string
  forecast_income: string
  forecast_expense: string
  plan_income: string
  plan_expense: string
  periods: CashFlowPeriod[]
  rows: CashFlowRow[]
  has_data: boolean
  has_plan: boolean
  plan_note: string | null
  causes: GapCauses | null
  warnings: string[]
}

/** Операции за период (и статью) — подробности точки графика или строки ДДС. */
export const opsHref = (from: string, to: string, category?: string | null, account?: string | null) =>
  `/dashboard/operations?from=${from}&to=${to}${category ? `&category=${category}` : ''}${account ? `&account=${account}` : ''}`

// ---------- «Все операции» — журнал факт + план (шаг 10.19) ----------

export type JournalStatus = 'fact' | 'planned' | 'expected' | 'overdue'
export type JournalSource = Operation['source'] | 'plan' | 'supplier' | 'tax' | 'ozon_forecast'

export type JournalRow = {
  id: string // op:<op_id> | plan:<ключ платежа>
  status: JournalStatus
  type: 'income' | 'expense' | 'transfer'
  day: string
  amount: string | null // у плана сумма может быть ещё неизвестна
  category: { id: string; name: string; kind: CategoryKind; parent_id: string | null } | null
  parent: { id: string; name: string } | null // статья, если category — подстатья
  account: { id: string; name: string } | null
  counterparty: string | null
  comment: string | null
  source: JournalSource
  operation: Operation | null // у факта
  plan: { key: string; title: string; plan_id: string | null; estimate: boolean; link: string | null } | null // у плана; у факта — закрытый платёж
}

type JournalTotals = { income: string; expense: string; net: string }

export type JournalPage = {
  date_from: string
  date_to: string
  total: number
  items: JournalRow[]
  totals: { fact: JournalTotals; plan: JournalTotals }
  counts: Record<JournalStatus, number>
  plan_note: string | null
}

// ---------- бюджеты и сценарии (шаг 10.21) ----------

export type BudgetCell = { plan: string | null; fact: string | null; diff: string | null; pct: string | null; worse: boolean | null }
export type BudgetRow = {
  category: { id: string; name: string; kind: 'income' | 'expense'; parent_id: string | null; archived: boolean }
  own: { month: string; plan: string | null }[] // свой план по 12 месяцам (без подстатей)
  cells: BudgetCell[]
  total: BudgetCell
  children: BudgetRow[]
}
export type BudgetSection = { rows: BudgetRow[]; other: { cells: BudgetCell[]; total: BudgetCell }; cells: BudgetCell[]; total: BudgetCell }
export type BudgetPeriod = { key: string; label: string; date_from: string; date_to: string; started: boolean; partial: boolean }
export type BudgetGranularity = 'month' | 'quarter' | 'year'
export type BudgetPage = {
  year: number
  granularity: BudgetGranularity
  today: string
  periods: BudgetPeriod[]
  income: BudgetSection
  expense: BudgetSection
  net: { cells: BudgetCell[]; total: BudgetCell }
  has_plan: boolean
}
export type BudgetVersion = { amount: string | null; source: 'manual' | 'import' | 'last_year' | 'prev_period'; created_at: string; superseded_at: string | null }
export type BudgetScenario = {
  key: 'base' | 'optimistic' | 'pessimistic'
  label: string
  income_pct: string
  expense_pct: string
  income: string
  expense: string
  end_balance: string | null
  min_balance: string | null
  min_day: string | null
  first_below_zero: string | null
  first_below_threshold: string | null
  days: { day: string; balance: string | null }[]
}
export type BudgetForecast = {
  today: string
  date_to: string
  months: number
  start: string | null
  unknown: string[]
  threshold: string | null
  scenarios: BudgetScenario[]
  budget_added: { category: string; day: string; amount: string }[]
}

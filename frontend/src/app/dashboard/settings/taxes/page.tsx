'use client'

import { CircleHelp, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { UpcomingTaxes } from '@/components/planning/UpcomingTaxes'
import { fmtDateTime, fmtDay, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { todayMsk } from '@/lib/period'

// прошлый налоговый платёж, который изменится при сохранении профиля задним числом (шаг 3.9)
type PastChange = { key: string; title: string; period_label: string | null; day: string; old: string | null; new: string | null; removed: boolean; added: boolean }

type Form = 'ip' | 'ooo' | 'self_employed'
type VatMode = 'none' | 'special' | 'general'
type Regime = {
  key: string
  title: string
  summary: string
  forms: Form[]
  vat_modes: VatMode[]
  default_rates: Record<string, string | null>
  min_rate: string | null
  restriction: string | null
  how: string[]
}
type Options = {
  forms: Record<Form, string>
  regimes: Regime[]
  vat_modes: Record<VatMode, string>
  vat_rates: Record<VatMode, number[]>
  supplier_vat_rates: number[]
  supplier_default: number
  limits: Record<string, number>
}
type Profile = {
  id: string
  effective_from: string
  legal_form: Form | null
  regime: string
  rate_pct: string | null
  custom_base: 'profit' | 'revenue' | null
  vat_mode: VatMode
  vat_rate_pct: string
  supplier_vat_pct: string
  contributions_year: string | null
  has_employees: boolean
  patent_cost_year: string | null
  created_at: string
  title: string
}
type State = { current: Profile | null; history: Profile[]; created: boolean | null }
type Example = {
  title: string
  income: string
  tax: string
  vat: string
  total: string
  lines: string[]
  warnings: string[]
}
type Preview = { title: string; how: string[]; example: Example; report_example: Example | null }
type From = 'beginning' | 'year' | 'quarter' | 'date'

const BEGINNING = '2000-01-01'
const FORM_HINT: Record<Form, string> = {
  ip: 'Индивидуальный предприниматель',
  ooo: 'Юридическое лицо',
  self_employed: 'Физлицо на НПД',
}
const CONTRIBUTIONS = ['usn_income', 'usn_income_expense', 'osn'] // где взносы влияют на налог

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const yearStart = () => `${new Date().getFullYear()}-01-01`
const quarterStart = () => {
  const now = new Date()
  return iso(new Date(now.getFullYear(), Math.floor(now.getMonth() / 3) * 3, 1))
}
const toNumber = (text: string) => {
  const n = Number(text.replace(/\s/g, '').replace(',', '.'))
  return text.trim() && Number.isFinite(n) ? n : null
}
const since = (d: string) => (d === BEGINNING ? 'с начала учёта' : `с ${fmtDay(d)}`)
const num = (v: string | number) => Number(v).toLocaleString('ru-RU')

function Step({ n, title, children, aside }: { n: number; title: string; children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <section className="relative rounded-xl border border-line bg-card p-5">
      <div className="flex items-start gap-2 pr-10">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent/15 text-sm text-accent">{n}</span>
        <h2 className="font-semibold">{title}</h2>
      </div>
      {aside && <div className="absolute right-4 top-4">{aside}</div>}
      <div className="mt-4 space-y-3">{children}</div>
    </section>
  )
}

/** «?» справа сверху: как именно считается налог при выбранной системе. */
function HelpPopover({ title, lines }: { title: string; lines: string[] }) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false)
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', esc)
    }
  }, [open])
  return (
    <div ref={box} className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-label="Как считается налог"
        title="Как считается налог"
        className={`rounded-full p-1 ${open ? 'text-accent' : 'text-muted hover:text-accent'}`}
      >
        <CircleHelp className="h-5 w-5" />
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={title}
          data-testid="tax-help"
          className="absolute right-0 top-9 z-20 w-[min(36rem,calc(100vw-3rem))] rounded-xl border border-line bg-card p-4 text-sm shadow-xl"
        >
          <div className="mb-2 flex items-start gap-3">
            <h3 className="font-semibold">{title}</h3>
            <button type="button" onClick={() => setOpen(false)} aria-label="Закрыть" className="ml-auto text-muted hover:text-fg">
              <X className="h-4 w-4" />
            </button>
          </div>
          <ul className="list-disc space-y-1.5 pl-5 text-muted">
            {lines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function Choice({ active, onClick, title, hint, testid }: { active: boolean; onClick: () => void; title: string; hint?: string; testid?: string }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      onClick={onClick}
      data-testid={testid}
      className={`rounded-lg border px-3 py-2 text-left ${active ? 'border-accent bg-accent/10' : 'border-line hover:border-muted'}`}
    >
      <span className="block text-sm font-medium">{title}</span>
      {hint && <span className="block text-xs text-faint">{hint}</span>}
    </button>
  )
}

function ExampleCard({ example, testid }: { example: Example; testid: string }) {
  return (
    <div className="space-y-2 rounded-lg border border-line bg-bg/40 p-4" data-testid={testid}>
      <div className="text-sm font-medium">{example.title}</div>
      <ul className="space-y-1 text-sm text-muted">
        {example.lines.map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-x-6 gap-y-1 border-t border-line pt-2 text-sm">
        <span>
          Налог: <b className="tabular-nums">{rub(example.tax)}</b>
        </span>
        {Number(example.vat) > 0 && (
          <span>
            НДС: <b className="tabular-nums">{rub(example.vat)}</b>
          </span>
        )}
        <span className="text-muted">
          Всего в бюджет: <span className="tabular-nums">{rub(example.total)}</span>
        </span>
      </div>
      {example.warnings.map((w) => (
        <p key={w} className="text-xs text-warn">
          {w}
        </p>
      ))}
    </div>
  )
}

export default function TaxesPage() {
  const [options, setOptions] = useState<Options | null>(null)
  const [state, setState] = useState<State | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [form, setForm] = useState<Form | null>(null)
  const [regime, setRegime] = useState<string | null>(null)
  const [rate, setRate] = useState('')
  const [customBase, setCustomBase] = useState<'profit' | 'revenue'>('profit')
  const [vatMode, setVatMode] = useState<VatMode>('none')
  const [vatRate, setVatRate] = useState(0)
  const [supplierVat, setSupplierVat] = useState(22)
  const [contributions, setContributions] = useState('')
  const [employees, setEmployees] = useState(false)
  const [patent, setPatent] = useState('')
  const [from, setFrom] = useState<From>('beginning')
  const [fromDate, setFromDate] = useState(iso(new Date()))

  const [preview, setPreview] = useState<Preview | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [past, setPast] = useState<PastChange[] | null>(null) // спрашиваем: пересчитать прошлые платежи?
  const [refresh, setRefresh] = useState(0)

  const fill = useCallback((s: State, o: Options) => {
    const p = s.current
    setFrom(p ? 'quarter' : 'beginning')
    setSupplierVat(p ? Number(p.supplier_vat_pct) : o.supplier_default)
    if (!p) return
    setForm(p.legal_form)
    setRegime(p.regime)
    setRate(p.rate_pct === null ? '' : num(p.rate_pct))
    setCustomBase(p.custom_base ?? 'profit')
    setVatMode(p.vat_mode)
    setVatRate(Number(p.vat_rate_pct))
    setContributions(p.contributions_year === null ? '' : String(Number(p.contributions_year)))
    setEmployees(p.has_employees)
    setPatent(p.patent_cost_year === null ? '' : String(Number(p.patent_cost_year)))
  }, [])

  useEffect(() => {
    Promise.all([api.get<Options>('/tax-profile/options'), api.get<State>('/tax-profile')])
      .then(([o, s]) => {
        setOptions(o.data)
        setState(s.data)
        fill(s.data, o.data)
      })
      .catch((err) => setError(errorMessage(err)))
  }, [fill])

  const regimes = options?.regimes ?? []
  const current = regimes.find((r) => r.key === regime) ?? null
  const available = regimes.filter((r) => r.key !== 'custom' && form !== null && r.forms.includes(form))
  const custom = regimes.find((r) => r.key === 'custom')

  function chooseForm(f: Form) {
    setForm(f)
    if (regime && regime !== 'custom' && !regimes.find((r) => r.key === regime)?.forms.includes(f)) setRegime(null)
    else if (current && current.key !== 'custom') setRate(current.default_rates[f] ? num(current.default_rates[f]!) : '')
  }

  function chooseRegime(r: Regime) {
    setRegime(r.key)
    const def = form ? r.default_rates[form] : null
    setRate(r.key === 'custom' ? (regime === 'custom' ? rate : '') : def ? num(def) : '')
    const mode = r.vat_modes.includes(vatMode) ? vatMode : r.vat_modes[0]
    chooseVat(mode)
  }

  function chooseVat(mode: VatMode) {
    setVatMode(mode)
    const rates = options?.vat_rates[mode] ?? [0]
    if (!rates.includes(vatRate)) setVatRate(rates[0])
  }

  const effectiveFrom = from === 'beginning' ? BEGINNING : from === 'year' ? yearStart() : from === 'quarter' ? quarterStart() : fromDate

  const body = useCallback(() => {
    if (!current || (current.key !== 'custom' && !form)) return null
    const r = toNumber(rate)
    const c = toNumber(contributions)
    const p = toNumber(patent)
    return {
      legal_form: form,
      regime: current.key,
      rate_pct: current.key === 'psn' ? null : r === null ? null : r.toFixed(2),
      custom_base: current.key === 'custom' ? customBase : null,
      vat_mode: vatMode,
      vat_rate_pct: String(vatMode === 'none' ? 0 : vatRate),
      supplier_vat_pct: String(supplierVat),
      contributions_year: CONTRIBUTIONS.includes(current.key) && c !== null ? c.toFixed(2) : null,
      has_employees: employees,
      patent_cost_year: current.key === 'psn' && p !== null ? p.toFixed(2) : null,
      effective_from: effectiveFrom,
    }
  }, [current, form, rate, contributions, patent, customBase, vatMode, vatRate, supplierVat, employees, effectiveFrom])

  // пример расчёта — с задержкой, пока пользователь вводит
  useEffect(() => {
    const b = body()
    if (!b) {
      setPreview(null)
      return
    }
    const t = setTimeout(() => {
      api
        .post<Preview>('/tax-profile/preview', b)
        .then(({ data }) => {
          setPreview(data)
          setPreviewError(null)
        })
        .catch((err) => {
          setPreview(null)
          setPreviewError(errorMessage(err))
        })
    }, 350)
    return () => clearTimeout(t)
  }, [body])

  async function put(b: Record<string, unknown>, pastPayments?: 'recalc' | 'keep') {
    const { data } = await api.put<State>('/tax-profile', { ...b, ...(pastPayments ? { past_payments: pastPayments } : {}) })
    setState(data)
    setPast(null)
    setRefresh((n) => n + 1)
    setNotice(
      data.created
        ? `Сохранено: ${preview?.title ?? 'налоговый профиль'}, ${since(effectiveFrom)}.${pastPayments === 'keep' ? ' Прошлые платежи оставлены как были.' : ''}`
        : 'Такие условия уже действуют с этой даты — ничего не изменилось.',
    )
  }

  async function save(pastPayments?: 'recalc' | 'keep') {
    const b = body()
    if (!b) return
    setBusy(true)
    setNotice(null)
    try {
      // задним числом: какие прошлые налоговые платежи изменятся — спросим, пересчитывать ли их (решение владельца)
      if (!pastPayments && state?.current && b.effective_from < todayMsk()) {
        const { data: changes } = await api.post<PastChange[]>('/tax-profile/past-payments', b)
        if (changes.length > 0) {
          setPast(changes)
          return
        }
      }
      await put(b, pastPayments)
    } catch (err) {
      setPreviewError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const help: { title: string; lines: string[] } = preview
    ? { title: `Как мы считаем налог: ${preview.title}`, lines: preview.how }
    : current
      ? { title: `Как мы считаем налог: ${current.title}`, lines: current.how }
      : {
          title: 'Как мы считаем налог',
          lines: [
            'Налог считаем по выбранной системе налогообложения, ставке и НДС — от полной цены продажи покупателю, а не от суммы, которая пришла на счёт.',
            ...regimes.map((r) => `${r.title}: ${r.summary}`),
          ],
        }

  const input = 'w-40 rounded-lg border border-line bg-bg px-3 py-2 outline-none focus:border-accent'
  const vatRates = options?.vat_rates[vatMode] ?? []
  const limits = options?.limits ?? {}

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Налоги и НДС</h1>
        <p className="text-sm text-muted">
          Кто вы и как платите налоги — по этому сервис считает налог и НДС в P&amp;L. Изменение — новая запись в истории: прошлые
          периоды считаются по прежним условиям.
        </p>
      </div>
      <FormError message={error} />
      {!options && !error && <p className="text-sm text-muted">Загрузка…</p>}

      {state && (
        <div
          data-testid="tax-current"
          className={`rounded-lg border px-4 py-3 text-sm ${state.current ? 'border-line bg-card' : 'border-warn/40 bg-warn/10 text-warn'}`}
        >
          {state.current ? (
            <>
              Сейчас: <b>{state.current.title}</b> <span className="text-muted">— {since(state.current.effective_from)}</span>
            </>
          ) : (
            'Налог не задан: выберите, кто вы и какая у вас система налогообложения. Пока не выберете — в P&L налог не считается.'
          )}
        </div>
      )}

      <UpcomingTaxes refresh={refresh} />

      {options && (
        <>
          <Step n={1} title="Кто вы">
            <div role="radiogroup" aria-label="Кто вы" className="grid gap-3 sm:grid-cols-3">
              {(Object.keys(options.forms) as Form[]).map((f) => (
                <Choice key={f} active={form === f} onClick={() => chooseForm(f)} title={options.forms[f]} hint={FORM_HINT[f]} testid={`form-${f}`} />
              ))}
            </div>
          </Step>

          <Step n={2} title="Система налогообложения" aside={<HelpPopover title={help.title} lines={help.lines} />}>
            {!form && <p className="text-sm text-muted">Сначала выберите, кто вы, — покажем подходящие системы.</p>}
            <div role="radiogroup" aria-label="Система налогообложения" className="grid gap-3 sm:grid-cols-2">
              {available.map((r) => (
                <button
                  key={r.key}
                  type="button"
                  role="radio"
                  aria-checked={regime === r.key}
                  onClick={() => chooseRegime(r)}
                  data-testid={`regime-${r.key}`}
                  className={`rounded-lg border p-3 text-left ${regime === r.key ? 'border-accent bg-accent/10' : 'border-line hover:border-muted'}`}
                >
                  <span className="block text-sm font-medium">{r.title}</span>
                  <span className="mt-0.5 block text-xs text-muted">{r.summary}</span>
                  {r.restriction && <span className="mt-1 block text-xs text-warn">{r.restriction}</span>}
                </button>
              ))}
            </div>
            {custom && (
              <button
                type="button"
                role="radio"
                aria-checked={regime === 'custom'}
                onClick={() => chooseRegime(custom)}
                data-testid="regime-custom"
                className={`w-full rounded-lg border border-dashed p-3 text-left ${regime === 'custom' ? 'border-accent bg-accent/10' : 'border-line hover:border-muted'}`}
              >
                <span className="block text-sm font-medium">{custom.title}</span>
                <span className="mt-0.5 block text-xs text-muted">{custom.summary}</span>
              </button>
            )}
          </Step>

          {current && (
            <Step n={3} title={current.key === 'psn' ? 'Стоимость патента' : 'Ставка'}>
              {current.key === 'psn' ? (
                <label className="block">
                  <span className="mb-1 block text-sm text-muted">Стоимость патента за год, ₽</span>
                  <input aria-label="Стоимость патента" inputMode="decimal" value={patent} onChange={(e) => setPatent(e.target.value)} className={input} />
                </label>
              ) : (
                <label className="block">
                  <span className="mb-1 block text-sm text-muted">{current.key === 'custom' ? 'Ваш процент налога' : 'Ставка налога'}, %</span>
                  <input aria-label="Ставка налога" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} className={input} />
                  {current.key !== 'custom' && form && current.default_rates[form] && (
                    <span className="mt-1 block text-xs text-faint">
                      Стандартная — {num(current.default_rates[form]!)} %
                      {current.min_rate && `, минимальный налог — ${num(current.min_rate)} % от дохода`}. В вашем регионе может быть
                      пониженная — укажите её.
                      {current.key === 'osn' && form === 'ip' && ' У ИП на ОСН это НДФЛ: шкала прогрессивная — укажите свою ставку.'}
                    </span>
                  )}
                </label>
              )}
              {current.key === 'custom' && (
                <div role="radiogroup" aria-label="Налог считается" className="flex flex-wrap gap-3">
                  <Choice active={customBase === 'profit'} onClick={() => setCustomBase('profit')} title="От прибыли" hint="(пришло от Ozon − себестоимость с НДС) × процент" />
                  <Choice active={customBase === 'revenue'} onClick={() => setCustomBase('revenue')} title="От дохода" hint="выручка × процент" />
                </div>
              )}
              {CONTRIBUTIONS.includes(current.key) && (
                <div className="space-y-2">
                  <label className="block">
                    <span className="mb-1 block text-sm text-muted">Страховые взносы за год, ₽ — по желанию</span>
                    <input aria-label="Страховые взносы за год" inputMode="decimal" value={contributions} onChange={(e) => setContributions(e.target.value)} className={input} />
                    <span className="mt-1 block text-xs text-faint">
                      {current.key === 'usn_income'
                        ? 'На УСН «Доходы» взносы уменьшают налог. Годовую сумму делим на дни периода.'
                        : 'Взносы входят в расходы. Годовую сумму делим на дни периода.'}
                    </span>
                  </label>
                  {current.key === 'usn_income' && form === 'ip' && (
                    <label className="flex items-center gap-2 text-sm">
                      <input type="checkbox" checked={employees} onChange={(e) => setEmployees(e.target.checked)} className="accent-[rgb(var(--accent))]" />
                      Есть сотрудники — тогда налог уменьшается на взносы не больше чем наполовину
                    </label>
                  )}
                </div>
              )}
            </Step>
          )}

          {current && (
            <Step n={4} title="НДС">
              <div role="radiogroup" aria-label="НДС" className="grid gap-3 sm:grid-cols-3">
                {current.vat_modes.map((m) => (
                  <Choice key={m} active={vatMode === m} onClick={() => chooseVat(m)} title={options.vat_modes[m]} testid={`vat-${m}`} />
                ))}
              </div>
              {vatMode !== 'none' && (
                <div role="radiogroup" aria-label="Ставка НДС" className="flex flex-wrap gap-2">
                  {vatRates.map((r) => (
                    <Choice key={r} active={vatRate === r} onClick={() => setVatRate(r)} title={`${r} %`} testid={`vat-rate-${r}`} />
                  ))}
                </div>
              )}
              {vatMode === 'none' && current.key.startsWith('usn') && limits.usn_vat_free_income && (
                <p className="text-xs text-faint">
                  На УСН НДС появляется, если доход за год больше {num(limits.usn_vat_free_income / 1_000_000)} млн ₽: тогда выберите 5 % или 7 % без
                  вычетов или 22 % с вычетом входящего НДС — с первого числа квартала.
                </p>
              )}
              <label className="block pt-2">
                <span className="mb-1 block text-sm text-muted">НДС в цене поставщика по умолчанию</span>
                <select aria-label="НДС в цене поставщика" value={supplierVat} onChange={(e) => setSupplierVat(Number(e.target.value))} className={input}>
                  {options.supplier_vat_rates.map((r) => (
                    <option key={r} value={r}>
                      {r === 0 ? 'без НДС' : `${r} %`}
                    </option>
                  ))}
                </select>
                <span className="mt-1 block text-xs text-faint">
                  Для себестоимости, у которой ставка не указана. {vatMode === 'general' ? 'Вы принимаете его к вычету.' : 'Он входит в ваши расходы.'}
                </span>
              </label>
            </Step>
          )}

          {current && (
            <Step n={5} title="С какой даты">
              <div role="radiogroup" aria-label="С какой даты" className="flex flex-wrap gap-3">
                <Choice active={from === 'beginning'} onClick={() => setFrom('beginning')} title="С начала учёта" hint="для всех периодов" testid="from-beginning" />
                <Choice active={from === 'year'} onClick={() => setFrom('year')} title={`С 1 января ${new Date().getFullYear()}`} hint="с начала года" />
                <Choice active={from === 'quarter'} onClick={() => setFrom('quarter')} title={`С ${fmtDay(quarterStart())}`} hint="с начала квартала" />
                <Choice active={from === 'date'} onClick={() => setFrom('date')} title="С даты" hint="укажите свою" />
              </div>
              {from === 'date' && (
                <input type="date" aria-label="Действует с" value={fromDate} onChange={(e) => setFromDate(e.target.value)} className={input} />
              )}
              {state?.current && from === 'beginning' && (
                <p className="text-xs text-warn">С начала учёта — значит, и для прошлых периодов: они пересчитаются по новым условиям.</p>
              )}
            </Step>
          )}

          {current && (
            <section className="space-y-3 rounded-xl border border-accent/40 bg-card p-5" data-testid="tax-preview">
              <h2 className="font-semibold">Как мы будем считать налог</h2>
              {preview && <p className="text-sm">{preview.title}</p>}
              <FormError message={previewError} />
              {!preview && !previewError && <p className="text-sm text-muted">Заполните шаги выше — покажем расчёт на примере.</p>}
              {preview && (
                <div className="grid gap-3 lg:grid-cols-2">
                  <ExampleCard example={preview.example} testid="example" />
                  {preview.report_example ? (
                    <ExampleCard example={preview.report_example} testid="report-example" />
                  ) : (
                    <p className="rounded-lg border border-dashed border-line p-4 text-sm text-muted">
                      Загрузите отчёт Ozon — покажем расчёт и на ваших цифрах.
                    </p>
                  )}
                </div>
              )}
              <div className="flex flex-wrap items-center gap-3 pt-1">
                <button
                  type="button"
                  onClick={() => save()}
                  disabled={busy || !preview}
                  className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
                >
                  {busy ? 'Сохраняем…' : 'Сохранить'}
                </button>
                {notice && (
                  <span className="text-sm text-ok" role="status">
                    {notice}
                  </span>
                )}
              </div>
              {past && (
                <div className="space-y-3 rounded-lg border border-warn/40 bg-warn/10 p-4 text-sm" data-testid="past-question">
                  <p className="font-semibold">Пересчитать и прошлые платежи по новым условиям?</p>
                  <p className="text-muted">
                    Новые условия — {since(effectiveFrom)}, это раньше сегодняшнего дня. Изменятся налоговые платежи, срок которых уже прошёл и которые не
                    отмечены оплаченными. Оплаченные не меняются в любом случае, будущие пересчитаются сами.
                  </p>
                  <ul className="space-y-1" data-testid="past-list">
                    {past.map((c) => (
                      <li key={c.key} className="tabular-nums">
                        {c.title}
                        {c.period_label && <span className="text-muted"> · {c.period_label}</span>} · до {fmtDay(c.day)}:{' '}
                        {c.old ? rub(c.old) : c.added ? 'не было' : 'сумма неизвестна'} → {c.removed ? 'не будет' : c.new ? rub(c.new) : 'сумма неизвестна'}
                      </li>
                    ))}
                  </ul>
                  <div className="flex flex-wrap gap-2">
                    <button type="button" disabled={busy} onClick={() => save('recalc')} className="rounded-lg bg-accent px-4 py-2 font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="past-recalc">
                      Да, пересчитать
                    </button>
                    <button type="button" disabled={busy} onClick={() => save('keep')} className="rounded-lg border border-line px-4 py-2 hover:border-accent hover:text-accent disabled:opacity-60" data-testid="past-keep">
                      Нет, оставить как были
                    </button>
                    <button type="button" onClick={() => setPast(null)} className="rounded-lg px-4 py-2 text-muted hover:text-fg">
                      Отмена
                    </button>
                  </div>
                  <p className="text-xs text-faint">
                    «Да» — прошлые платежи посчитаем так, как будто новые условия действовали с этой даты. «Нет» — они останутся как были, новые условия — только
                    для сроков с сегодняшнего дня.
                  </p>
                </div>
              )}
            </section>
          )}
        </>
      )}

      {state && state.history.length > 0 && (
        <section className="rounded-xl border border-line bg-card">
          <h2 className="border-b border-line px-5 py-3 font-semibold">История</h2>
          <ul className="text-sm" data-testid="tax-history">
            {state.history.map((p, i) => (
              <li
                key={p.id}
                className={`flex flex-wrap items-baseline gap-x-4 gap-y-0.5 border-b border-line px-5 py-2 last:border-b-0 ${i === 0 ? '' : 'text-muted'}`}
              >
                <span className="w-32 shrink-0">{since(p.effective_from)}</span>
                <span className="min-w-0 flex-1">{p.title}</span>
                <span className="text-xs text-faint">записано {fmtDateTime(p.created_at)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

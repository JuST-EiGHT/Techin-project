'use client'
// Блоки страницы «Выплаты Ozon» (шаг 3.6): график выплат по неделям, таблицы, настройка графика.
import { useEffect, useState } from 'react'
import { Bar, CartesianGrid, ComposedChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { FormError } from '@/components/AuthForm'
import { compact, useTokenColors } from '@/components/finance'
import { Badge, fmtDay, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { parseDay } from '@/lib/period'

export type Mode = 'auto' | 'standard' | 'daily' | 'weekly' | 'custom'

export type Schedule = {
  mode: 'daily' | 'weekly' | 'irregular'
  weekday: number | null
  lag_weeks: number | null
  fee_pct: string
  source: 'auto' | 'standard' | 'user'
  confidence: 'high' | 'medium' | 'low' | null
  payouts_used: number
  title: string
}

export type ForecastItem = {
  date: string
  amount: string | null
  kind: 'schedule' | 'estimate'
  week_from: string | null
  week_to: string | null
}

export type PayoutFact = {
  date: string
  amount: string
  week_from: string | null
  week_to: string | null
  expected: string | null
  diff: string | null
}

export type WeekBar = { week_from: string; paid: string; schedule: string; estimate: string }

export type Payouts = {
  has_data: boolean
  last_day: string | null
  balance: string | null
  paid_30d: string | null
  schedule: Schedule
  detected: Omit<Schedule, 'source' | 'payouts_used'> | null
  next_payout: ForecastItem | null
  forecast: ForecastItem[]
  history: PayoutFact[]
  overdue: string[]
  weeks: WeekBar[]
  warnings: string[]
}

type SettingsRow = { effective_from: string; mode: Mode; weekday: number | null; lag_weeks: number | null; fee_pct: string | null }
type SettingsState = { current: SettingsRow | null; history: SettingsRow[]; standard: Schedule; created: boolean | null }

const WEEKDAYS = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье']
const ON_DAYS = ['по понедельникам', 'по вторникам', 'по средам', 'по четвергам', 'по пятницам', 'по субботам', 'по воскресеньям']
const SHORT_DAYS = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб']

/** «2026-10-21» → «21.10». */
export const dayMonth = (iso: string) => fmtDay(iso).slice(0, 5)
/** «2026-10-21» → «ср». */
export const weekdayShort = (iso: string) => SHORT_DAYS[parseDay(iso).getUTCDay()]
export const weekText = (from: string | null, to: string | null) => (from && to ? `${dayMonth(from)}–${dayMonth(to)}` : '—')

const pct = (v: string | null) => (v === null ? '' : `${Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} %`)

// ---------- график по неделям ----------

type ChartRow = { week: string; label: string; paid: number; schedule: number; estimate: number }

function WeekTooltip({ active, payload }: { active?: boolean; payload?: { payload: ChartRow }[] }) {
  if (!active || !payload?.length) return null
  const r = payload[0].payload
  const to = parseDay(r.week)
  to.setUTCDate(to.getUTCDate() + 6)
  return (
    <div className="rounded-lg border border-line bg-card px-3 py-2 text-xs shadow-lg">
      <div className="mb-1 font-semibold">
        Неделя {dayMonth(r.week)}–{dayMonth(to.toISOString().slice(0, 10))}
      </div>
      <div className="space-y-0.5 tabular-nums">
        {r.paid > 0 && <div className="text-ok">Выплачено: {rub(String(r.paid))}</div>}
        {r.schedule > 0 && <div className="text-accent">Ожидается по графику: {rub(String(r.schedule))}</div>}
        {r.estimate > 0 && <div className="text-muted">Оценка: {rub(String(r.estimate))}</div>}
        {r.paid + r.schedule + r.estimate === 0 && <div className="text-muted">выплат нет</div>}
      </div>
    </div>
  )
}

/** Выплаты по неделям: прошлые — сплошные, прогноз — штриховкой (по графику — акцентом, оценка — серым). */
export function WeekChart({ weeks, today }: { weeks: WeekBar[]; today: string }) {
  const colors = useTokenColors()
  if (weeks.length === 0 || !colors) return null
  const rows: ChartRow[] = weeks.map((w) => ({
    week: w.week_from,
    label: dayMonth(w.week_from),
    paid: Number(w.paid),
    schedule: Number(w.schedule),
    estimate: Number(w.estimate),
  }))
  const current = [...rows].reverse().find((r) => r.week <= today)
  const hatch = (id: string, color: string) => (
    <pattern id={id} patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
      <rect width="6" height="6" fill={color} fillOpacity={0.12} />
      <line x1="0" y1="0" x2="0" y2="6" stroke={color} strokeWidth="2.5" />
    </pattern>
  )
  return (
    <section className="rounded-xl border border-line bg-card p-4" data-testid="payout-chart">
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1">
        <h2 className="text-sm font-semibold">Выплаты по неделям</h2>
        <span className="flex items-center gap-1.5 text-xs text-muted">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-ok" /> выплачено
        </span>
        <span className="flex items-center gap-1.5 text-xs text-muted">
          <span className="inline-block h-2.5 w-2.5 rounded-sm border border-accent bg-accent/20" /> по графику
        </span>
        <span className="flex items-center gap-1.5 text-xs text-muted">
          <span className="inline-block h-2.5 w-2.5 rounded-sm border border-muted bg-muted/20" /> оценка
        </span>
      </div>
      <div className="h-60">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
            <defs>
              {hatch('hatch-schedule', colors.accent)}
              {hatch('hatch-estimate', colors.muted)}
            </defs>
            <CartesianGrid vertical={false} stroke={colors.line} />
            <XAxis dataKey="label" tick={{ fill: colors.muted, fontSize: 11 }} tickLine={false} axisLine={{ stroke: colors.line }} minTickGap={8} />
            <YAxis tick={{ fill: colors.muted, fontSize: 11 }} tickFormatter={compact} tickLine={false} axisLine={false} width={64} />
            {current && <ReferenceLine x={current.label} stroke={colors.faint} strokeDasharray="3 3" />}
            <Tooltip content={<WeekTooltip />} cursor={{ fill: colors.line, opacity: 0.4 }} />
            <Bar dataKey="paid" stackId="w" fill={colors.ok} isAnimationActive={false} />
            <Bar dataKey="schedule" stackId="w" fill="url(#hatch-schedule)" stroke={colors.accent} isAnimationActive={false} />
            <Bar dataKey="estimate" stackId="w" fill="url(#hatch-estimate)" stroke={colors.muted} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-2 text-xs text-faint">Неделя — по дню выплаты, пунктир — текущая неделя.</p>
    </section>
  )
}

// ---------- таблицы ----------

export function ForecastTable({ items }: { items: ForecastItem[] }) {
  if (items.length === 0) return null
  return (
    <section className="rounded-xl border border-line bg-card p-4" data-testid="forecast-table">
      <h2 className="text-sm font-semibold">Ближайшие выплаты</h2>
      <p className="mt-0.5 text-xs text-muted">
        «По графику» — начислено за закончившуюся неделю продаж минус комиссия, но не больше, чем лежит на балансе у Ozon.
        «Оценка» — неделя продаж ещё идёт: среднее за 4 последние недели.
      </p>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted">
            <tr className="border-b border-line">
              <th className="py-2 pr-3 font-normal">Дата</th>
              <th className="py-2 pr-3 text-right font-normal">Сумма</th>
              <th className="hidden py-2 pr-3 font-normal sm:table-cell">За продажи</th>
              <th className="py-2 font-normal">Как посчитано</th>
            </tr>
          </thead>
          <tbody>
            {items.map((f) => (
              <tr key={f.date} className="border-b border-line/60 last:border-0">
                <td className="whitespace-nowrap py-2 pr-3">
                  {fmtDay(f.date)} <span className="text-faint">{weekdayShort(f.date)}</span>
                </td>
                <td className="whitespace-nowrap py-2 pr-3 text-right tabular-nums">{f.amount === null ? '—' : rub(f.amount)}</td>
                <td className="hidden whitespace-nowrap py-2 pr-3 text-muted sm:table-cell">{weekText(f.week_from, f.week_to)}</td>
                <td className="py-2">
                  {f.kind === 'schedule' ? <Badge tone="accent">по графику</Badge> : <Badge tone="muted">оценка</Badge>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export function HistoryTable({ items }: { items: PayoutFact[] }) {
  return (
    <section className="rounded-xl border border-line bg-card p-4" data-testid="history-table">
      <h2 className="text-sm font-semibold">История выплат за 12 недель</h2>
      {items.length === 0 ? (
        <p className="mt-2 text-sm text-muted">За загруженные дни выплат не было.</p>
      ) : (
        <>
          <p className="mt-0.5 text-xs text-muted">
            «По графику» — сколько ожидали: начислено за неделю продаж минус комиссия. Разница — переносы, удержания или
            выплата с баланса за прошлые недели.
          </p>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="py-2 pr-3 font-normal">Дата</th>
                  <th className="py-2 pr-3 text-right font-normal">Выплачено</th>
                  <th className="hidden py-2 pr-3 font-normal sm:table-cell">За продажи</th>
                  <th className="hidden py-2 pr-3 text-right font-normal sm:table-cell">По графику</th>
                  <th className="py-2 text-right font-normal">Разница</th>
                </tr>
              </thead>
              <tbody>
                {items.map((p) => {
                  const diff = p.diff === null ? null : Number(p.diff)
                  return (
                    <tr key={p.date} className="border-b border-line/60 last:border-0">
                      <td className="whitespace-nowrap py-2 pr-3">
                        {fmtDay(p.date)} <span className="text-faint">{weekdayShort(p.date)}</span>
                      </td>
                      <td className="whitespace-nowrap py-2 pr-3 text-right font-semibold tabular-nums text-ok">{rub(p.amount)}</td>
                      <td className="hidden whitespace-nowrap py-2 pr-3 text-muted sm:table-cell">{weekText(p.week_from, p.week_to)}</td>
                      <td className="hidden whitespace-nowrap py-2 pr-3 text-right tabular-nums text-muted sm:table-cell">
                        {p.expected === null ? '—' : rub(p.expected)}
                      </td>
                      <td
                        className={`whitespace-nowrap py-2 text-right tabular-nums ${
                          diff === null || Math.abs(diff) < 1 ? 'text-muted' : diff < 0 ? 'text-bad' : 'text-ok'
                        }`}
                      >
                        {diff === null ? '—' : Math.abs(diff) < 1 ? 'совпало' : `${diff > 0 ? '+' : ''}${rub(p.diff)}`}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}

// ---------- график выплат: какой действует и настройка ----------

const CONFIDENCE = { high: 'высокая', medium: 'средняя', low: 'низкая' } as const

function sourceText(s: Schedule): string {
  if (s.source === 'user') return 'Ваша настройка.'
  if (s.source === 'standard') return 'Стандартный график Ozon.'
  return `Определён по выплатам за 12 недель (${s.payouts_used}), уверенность — ${CONFIDENCE[s.confidence ?? 'low']}.`
}

function settingTitle(r: SettingsRow): string {
  const fee = r.fee_pct !== null && Number(r.fee_pct) > 0 ? `, комиссия ${pct(r.fee_pct)}` : ''
  if (r.mode === 'auto') return 'Определять по выплатам'
  if (r.mode === 'standard') return 'Стандартный график Ozon'
  if (r.mode === 'daily') return `Каждый рабочий день${fee}`
  const day = r.weekday === null ? '' : ` ${ON_DAYS[r.weekday]}`
  const lag = r.mode === 'custom' && r.lag_weeks !== null ? `, за неделю продаж ${r.lag_weeks} нед. назад` : ''
  return `Раз в неделю${day}${lag}${fee}`
}

type Option = { mode: Mode; label: string; text: string }

function options(standard: Schedule): Option[] {
  return [
    {
      mode: 'auto',
      label: 'Определять по выплатам (рекомендуем)',
      text: 'Сервис сам смотрит, в какой день и за какую неделю продаж приходят деньги, и подстроится, если вы смените график в кабинете Ozon. Пока выплат мало — стандартный график.',
    },
    {
      mode: 'standard',
      label: 'Стандартный график Ozon',
      text: `${standard.title[0].toUpperCase()}${standard.title.slice(1)}, без комиссии. Подходит, если вы не подключали гибкий график выплат.`,
    },
    {
      mode: 'daily',
      label: 'Каждый рабочий день',
      text: 'Гибкий график Ozon с ежедневной выплатой. Прогноз — на каждый рабочий день: средние продажи за день минус комиссия.',
    },
    {
      mode: 'weekly',
      label: 'Раз в неделю в свой день',
      text: 'Гибкий график с выплатой раз в неделю. Укажите день — за какую неделю продаж приходят деньги, сервис определит по выплатам.',
    },
    {
      mode: 'custom',
      label: 'Свой график полностью',
      text: 'День, неделя продаж и комиссия — как в ваших условиях Ozon. Выберите, если автоматическое определение ошибается.',
    },
  ]
}

const toFee = (text: string): string | null | undefined => {
  const s = text.trim().replace(',', '.').replace('%', '').trim()
  if (s === '') return null
  return /^\d+(\.\d{1,2})?$/.test(s) ? s : undefined
}

export function ScheduleBlock({ schedule, detected, onSaved }: { schedule: Schedule; detected: Payouts['detected']; onSaved: () => void }) {
  const [state, setState] = useState<SettingsState | null>(null)
  const [editing, setEditing] = useState(false)
  const [mode, setMode] = useState<Mode>('auto')
  const [weekday, setWeekday] = useState<string>('')
  const [lag, setLag] = useState('3')
  const [fee, setFee] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<SettingsState>('/ozon/payout-settings')
      .then(({ data }) => setState(data))
      .catch((err) => setError(errorMessage(err)))
  }, [])

  function startEdit() {
    const cur = state?.current
    setMode(cur?.mode ?? 'auto')
    setWeekday(String(cur?.weekday ?? schedule.weekday ?? state?.standard.weekday ?? 2))
    setLag(String(cur?.lag_weeks ?? schedule.lag_weeks ?? state?.standard.lag_weeks ?? 3))
    setFee(cur?.fee_pct && Number(cur.fee_pct) > 0 ? cur.fee_pct.replace('.', ',') : '')
    setError(null)
    setMessage(null)
    setEditing(true)
  }

  async function save() {
    const feePct = toFee(fee)
    if (feePct === undefined) {
      setError('Комиссию введите числом, например 1,5')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const body = {
        mode,
        weekday: mode === 'weekly' || mode === 'custom' ? Number(weekday) : null,
        lag_weeks: mode === 'custom' ? Number(lag) : null,
        fee_pct: mode === 'daily' || mode === 'weekly' || mode === 'custom' ? feePct : null,
      }
      const { data } = await api.put<SettingsState>('/ozon/payout-settings', body)
      setState(data)
      setEditing(false)
      setMessage(data.created ? 'Сохранено — прогноз пересчитан.' : 'Такой график уже действует — ничего не изменилось.')
      onSaved()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const opts = state ? options(state.standard) : []
  const needDay = mode === 'weekly' || mode === 'custom'
  const needFee = mode === 'daily' || mode === 'weekly' || mode === 'custom'
  const showDetected =
    detected !== null && schedule.source !== 'auto' && detected.mode !== 'irregular' && detected.title !== schedule.title

  return (
    <section className="rounded-xl border border-line bg-card p-4" data-testid="schedule-block">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold">График выплат</h2>
          <p className="mt-1 text-base font-semibold first-letter:uppercase" data-testid="schedule-title">
            {schedule.title}
          </p>
          <p className="mt-0.5 text-xs text-muted">{sourceText(schedule)}</p>
          {showDetected && (
            <p className="mt-0.5 text-xs text-faint" data-testid="schedule-detected">
              По выплатам видно: {detected.title}.
            </p>
          )}
        </div>
        {state && !editing && (
          <button
            type="button"
            onClick={startEdit}
            className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent"
            data-testid="schedule-edit"
          >
            Изменить
          </button>
        )}
      </div>
      {message && !editing && <p className="mt-2 text-xs text-ok">{message}</p>}
      {!editing && <FormError message={error} />}

      {editing && state && (
        <div className="mt-4 space-y-4 border-t border-line pt-4" data-testid="schedule-form">
          <p className="text-xs text-muted">
            График нужен, чтобы предсказать даты и суммы следующих выплат. На сами выплаты он не влияет — меняется только
            прогноз на этой странице. Свой график в Ozon смотрите в кабинете: «Финансы → График выплат».
          </p>
          <fieldset className="space-y-2">
            <legend className="sr-only">Как определять график</legend>
            {opts.map((o) => (
              <label
                key={o.mode}
                className={`flex cursor-pointer gap-3 rounded-lg border p-3 ${mode === o.mode ? 'border-accent bg-accent/5' : 'border-line hover:border-accent/60'}`}
              >
                <input
                  type="radio"
                  name="payout-mode"
                  value={o.mode}
                  checked={mode === o.mode}
                  onChange={() => setMode(o.mode)}
                  className="mt-1 accent-accent"
                />
                <span className="min-w-0">
                  <span className="block text-sm font-medium">{o.label}</span>
                  <span className="block text-xs text-muted">{o.text}</span>
                </span>
              </label>
            ))}
          </fieldset>

          {(needDay || needFee) && (
            <div className="grid gap-3 sm:grid-cols-3">
              {needDay && (
                <label className="block text-sm">
                  <span className="text-muted">День выплаты</span>
                  <select
                    value={weekday}
                    onChange={(e) => setWeekday(e.target.value)}
                    className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 outline-none focus:border-accent"
                    data-testid="schedule-weekday"
                  >
                    {WEEKDAYS.map((d, i) => (
                      <option key={d} value={i}>
                        {d}
                      </option>
                    ))}
                  </select>
                  <span className="mt-1 block text-xs text-faint">Выпал на праздник — ждём в следующий рабочий день.</span>
                </label>
              )}
              {mode === 'custom' && (
                <label className="block text-sm">
                  <span className="text-muted">За неделю продаж, недель назад</span>
                  <input
                    type="number"
                    min={0}
                    max={8}
                    value={lag}
                    onChange={(e) => setLag(e.target.value)}
                    className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 outline-none focus:border-accent"
                    data-testid="schedule-lag"
                  />
                  <span className="mt-1 block text-xs text-faint">
                    От этого зависит сумма: 3 — выплата за продажи с понедельника по воскресенье три недели назад (стандарт Ozon).
                  </span>
                </label>
              )}
              {needFee && (
                <label className="block text-sm">
                  <span className="text-muted">Комиссия Ozon за гибкий график, %</span>
                  <input
                    value={fee}
                    inputMode="decimal"
                    placeholder={detected && Number(detected.fee_pct) > 0 ? `по выплатам ${pct(detected.fee_pct)}` : 'необязательно'}
                    onChange={(e) => setFee(e.target.value)}
                    className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 outline-none focus:border-accent"
                    data-testid="schedule-fee"
                  />
                  <span className="mt-1 block text-xs text-faint">
                    Уменьшает прогноз выплат. Не знаете — оставьте пустым: возьмём по выплатам.
                  </span>
                </label>
              )}
            </div>
          )}

          <FormError message={error} />
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={save}
              disabled={saving}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
              data-testid="schedule-save"
            >
              {saving ? 'Сохраняем…' : 'Сохранить'}
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="rounded-lg border border-line px-4 py-2 text-sm text-muted hover:border-accent hover:text-fg"
            >
              Отмена
            </button>
          </div>
        </div>
      )}

      {state && state.history.length > 0 && (
        <details className="mt-3 text-xs text-muted">
          <summary className="cursor-pointer select-none hover:text-fg">История настроек ({state.history.length})</summary>
          <ul className="mt-2 space-y-1">
            {state.history.map((r, i) => (
              <li key={`${r.effective_from}-${i}`}>
                с {fmtDay(r.effective_from)} — {settingTitle(r)}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}

'use client'
// «Планирование → Календарь» (шаг 3.9): свои плановые платежи, выплаты Ozon, оплата поставщику и налоги по дням;
// прогноз остатка и кассовый разрыв, «что если» (выключить план или генератор), перенос перетаскиванием,
// отметка «оплачено», план-факт.
import { CalendarDays, GripVertical, List, Pencil, Trash2 } from 'lucide-react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { FormError } from '@/components/AuthForm'
import { type Drill, TipBox, useDrill } from '@/components/charts'
import { compact, useTokenColors } from '@/components/finance'
import { CategoryHint, ImportPanel, Modal, input, sameName } from '@/components/planning/Operations'
import {
  type Account,
  type CalendarData,
  type CalendarLine,
  type Category,
  type ForecastDay,
  type Period,
  type Plan,
  type PlanKind,
  type PlanningState,
  PERIODS,
  SOURCE_LABELS,
  WEEKDAYS,
  calcHref,
  parseAmount,
} from '@/components/planning/types'
import { Badge, fmtDay, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { addDays, parseDay, todayMsk } from '@/lib/period'

const HORIZONS = [30, 60, 90]
const WEEK_SHORT = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']
const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
const PAST_DAYS = 7 // в списке — и прошлая неделя: что оплачено, что нет

const weekday = (iso: string) => (parseDay(iso).getUTCDay() + 6) % 7 // 0 — понедельник
const dayTitle = (iso: string) => `${WEEK_SHORT[weekday(iso)]}, ${fmtDay(iso).slice(0, 5)}`
const signed = (v: string) => `${Number(v) > 0 ? '+' : ''}${rub(v)}`
const STATUS: Record<CalendarLine['status'], { label: string; tone: 'warn' | 'muted' | 'bad' | 'accent' } | null> = {
  planned: null,
  overdue: { label: 'не отмечен', tone: 'warn' },
  paid: { label: 'оплачено', tone: 'accent' },
  skipped: { label: 'пропущен', tone: 'muted' },
  disabled: { label: 'выключен', tone: 'muted' },
}
const SOURCE_TONE: Record<CalendarLine['source'], 'warn' | 'muted' | 'bad' | 'accent'> = { ozon: 'accent', manual: 'muted', supplier: 'warn', tax: 'bad' }

// ---------- прогноз: сводка, предупреждение, график ----------

function Figure({ label, value, hint, tone, testid }: { label: string; value: string; hint?: string; tone?: string; testid?: string }) {
  return (
    <div className="min-w-[9rem] flex-1 bg-card px-4 py-3" data-testid={testid}>
      <div className="text-[11px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`mt-1 text-lg font-bold tabular-nums ${tone ?? ''}`}>{value}</div>
      {hint && <div className="text-xs text-faint">{hint}</div>}
    </div>
  )
}

function Summary({ data }: { data: CalendarData }) {
  const min = data.days.find((d) => d.balance === data.min_balance)
  const threshold = data.threshold === null ? 0 : Number(data.threshold)
  const tone = (v: string | null) => (v === null ? '' : Number(v) < 0 ? 'text-bad' : Number(v) < threshold ? 'text-warn' : '')
  const enough = data.start_balance === null ? '—' : data.first_below_zero ? `до ${fmtDay(addDays(data.first_below_zero, -1))}` : 'на весь период'
  return (
    <div className="flex flex-wrap gap-px overflow-hidden rounded-xl border border-line bg-line" data-testid="cal-summary">
      <Figure label="Сейчас на счетах" value={data.start_balance === null ? '—' : rub(data.start_balance)} hint={data.start_balance === null ? 'остаток не указан' : undefined} />
      <Figure label="Минимум" value={data.min_balance === null ? '—' : rub(data.min_balance)} hint={min ? fmtDay(min.day) : undefined} tone={tone(data.min_balance)} />
      <Figure label={`На ${fmtDay(data.date_to).slice(0, 5)}`} value={data.end_balance === null ? '—' : rub(data.end_balance)} tone={tone(data.end_balance)} />
      <Figure label="Денег хватит" value={enough} tone={data.first_below_zero ? 'text-bad' : 'text-ok'} testid="cal-enough" />
    </div>
  )
}

function GapBanner({ data }: { data: CalendarData }) {
  const zero = data.first_below_zero
  const low = data.first_below_threshold
  if (!zero && !low) return null
  // разрыв — с первого дня ниже нуля; ниже порога — отдельно (может начаться раньше)
  const gap = data.gaps.find((g) => (zero ? g.below_zero : true))
  return (
    <div className={`rounded-lg border px-4 py-3 text-sm ${zero ? 'border-bad/40 bg-bad/10' : 'border-warn/40 bg-warn/10'}`} data-testid="cal-gap" role="alert">
      <b className={zero ? 'text-bad' : 'text-warn'}>{zero ? `Кассовый разрыв с ${fmtDay(zero)}` : `Остаток ниже порога с ${fmtDay(low!)}`}</b>
      {gap && `: остаток опустится до ${rub(gap.min_balance)} (${fmtDay(gap.min_day)})`}
      {zero && `, денег хватит до ${fmtDay(addDays(zero, -1))}`}.
      {zero && low && low < zero && data.threshold !== null && (
        <span className="text-warn"> Ниже порога {rub(data.threshold)} — уже с {fmtDay(low)}.</span>
      )}
      <span className="mt-1 block text-muted">
        Что можно сделать: перенести платёж на дату после поступления (перетащите его в календаре), выключить план и посмотреть, как изменится прогноз,
        или добавить поступление.
      </span>
    </div>
  )
}

type ChartRow = { day: string; label: string; balance: number | null; row: ForecastDay; count: number }

function ForecastTip({ active, payload, drill }: { active?: boolean; payload?: { payload: ChartRow }[]; drill: Drill<ChartRow> }) {
  if (!active || !payload?.length) return null
  const r = payload[0].payload
  return (
    <TipBox href={drill.hrefFor(r)} touch={drill.touch}>
      <div className="font-medium">{dayTitle(r.day)}</div>
      <div className="tabular-nums">Остаток: {r.row.balance === null ? '—' : rub(r.row.balance)}</div>
      {Number(r.row.income) !== 0 && <div className="tabular-nums text-ok">Поступления: {signed(r.row.income)}</div>}
      {Number(r.row.expense) !== 0 && <div className="tabular-nums text-bad">Платежи: {rub(r.row.expense)}</div>}
      {r.count > 0 && <div className="text-muted">Платежей в плане: {r.count}</div>}
    </TipBox>
  )
}

function ForecastChart({ data, onDay }: { data: CalendarData; onDay: (day: string) => string }) {
  const colors = useTokenColors()
  const drill = useDrill<ChartRow>((r) => onDay(r.day))
  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const ln of data.lines) if (ln.status === 'planned') c[ln.day] = (c[ln.day] ?? 0) + 1
    return c
  }, [data.lines])
  if (!colors || data.start_balance === null || data.days.length === 0) return null
  const rows: ChartRow[] = data.days.map((d) => ({ day: d.day, label: fmtDay(d.day).slice(0, 5), balance: d.balance === null ? null : Number(d.balance), row: d, count: counts[d.day] ?? 0 }))
  return (
    <section className="rounded-xl border border-line bg-card p-4" data-testid="forecast-chart">
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1">
        <h2 className="text-sm font-semibold">Прогноз остатка по дням</h2>
        <span className="flex items-center gap-1.5 text-xs text-muted">
          <span className="inline-block h-0 w-3 border-t-2 border-dashed border-bad" /> ноль
        </span>
        {data.threshold !== null && (
          <span className="flex items-center gap-1.5 text-xs text-muted">
            <span className="inline-block h-0 w-3 border-t-2 border-dashed border-warn" /> порог {rub(data.threshold)}
          </span>
        )}
      </div>
      <div className={`h-56 ${drill.boxProps.className}`} onPointerDown={drill.boxProps.onPointerDown}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: 0 }} onClick={drill.onClick}>
            <CartesianGrid vertical={false} stroke={colors.line} />
            <XAxis dataKey="label" tick={{ fill: colors.muted, fontSize: 11 }} tickLine={false} axisLine={{ stroke: colors.line }} minTickGap={12} />
            <YAxis tick={{ fill: colors.muted, fontSize: 11 }} tickFormatter={compact} tickLine={false} axisLine={false} width={64} />
            <ReferenceLine y={0} stroke={colors.bad} strokeDasharray="4 3" />
            {data.threshold !== null && <ReferenceLine y={Number(data.threshold)} stroke={colors.warn} strokeDasharray="4 3" />}
            <Tooltip content={<ForecastTip drill={drill} />} cursor={{ stroke: colors.faint, strokeDasharray: '3 3' }} {...drill.tooltipProps} />
            <Line type="monotone" dataKey="balance" stroke={colors.accent} strokeWidth={2} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}

// ---------- настройки прогноза: генераторы, порог, поставщик ----------

function Toggle({ on, label, hint, onClick, testid, disabled }: { on: boolean; label: string; hint: string; onClick: () => void; testid: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={onClick}
      disabled={disabled}
      title={hint}
      className={`rounded-full border px-3 py-1.5 text-sm ${on ? 'border-accent bg-accent/10 text-accent' : 'border-line text-muted hover:text-fg'} disabled:opacity-50`}
      data-testid={testid}
    >
      {on ? '✓ ' : ''}
      {label}
    </button>
  )
}

function ForecastSettings({ data, state, onSaved }: { data: CalendarData; state: PlanningState; onSaved: () => void }) {
  const [threshold, setThreshold] = useState(data.threshold === null ? '' : String(Number(data.threshold)))
  const [error, setError] = useState<string | null>(null)
  const g = data.generators

  async function save(body: Record<string, unknown>) {
    setError(null)
    try {
      await api.put('/planning/settings', body)
      onSaved()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  function saveThreshold() {
    const value = threshold.trim() ? parseAmount(threshold) : null
    if (threshold.trim() && value === null) return setError('Порог — числом, например 50 000')
    if ((value === null ? null : Number(value)) === (data.threshold === null ? null : Number(data.threshold))) return
    save({ threshold: value })
  }

  return (
    <section className="space-y-3 rounded-xl border border-line bg-card p-4" data-testid="cal-settings">
      <div>
        <h2 className="text-sm font-semibold">Что учитывать в прогнозе</h2>
        <p className="text-xs text-faint">Выключите — и сразу увидите прогноз без этих платежей («что если»). Платежи и отметки сохраняются.</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Toggle on={g.ozon.enabled} label="Выплаты Ozon" hint="Даты по графику выплат, суммы по начислениям — вводить не нужно" onClick={() => save({ gen_ozon: !g.ozon.enabled })} testid="gen-ozon" />
        {g.supplier.available && (
          <Toggle on={g.supplier.enabled} label="Оплата поставщику" hint="Себестоимость проданного с НДС — поставщик получает деньги после продажи" onClick={() => save({ gen_supplier: !g.supplier.enabled })} testid="gen-supplier" />
        )}
        {g.taxes.available ? (
          <Toggle on={g.taxes.enabled} label="Налоги и взносы" hint="Сроки по системе налогообложения, суммы по налоговому профилю" onClick={() => save({ gen_taxes: !g.taxes.enabled })} testid="gen-taxes" />
        ) : (
          <Link href="/dashboard/settings/taxes" className="rounded-full border border-dashed border-line px-3 py-1.5 text-sm text-muted hover:text-accent" data-testid="gen-taxes-setup">
            Налоги — задайте налоговый профиль →
          </Link>
        )}
      </div>
      <div className="flex flex-wrap items-end gap-x-6 gap-y-3 text-sm">
        <label className="block">
          <span className="text-muted">Порог остатка, ₽</span>
          <input
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            onBlur={saveThreshold}
            onKeyDown={(e) => e.key === 'Enter' && saveThreshold()}
            inputMode="decimal"
            placeholder="не задан"
            className={`${input} mt-1 w-40`}
            data-testid="cal-threshold"
          />
          <span className="mt-1 block max-w-xs text-xs text-faint">Ниже этой суммы день отмечается как риск — например, запас на зарплату. Пусто — только ниже нуля.</span>
        </label>
        {g.supplier.available && g.supplier.enabled && (
          <div className="flex flex-wrap items-end gap-2">
            <label className="block">
              <span className="text-muted">Поставщику платите</span>
              <select
                value={state.supplier_period}
                onChange={(e) => save({ supplier_period: e.target.value, supplier_day: e.target.value === 'week' ? 1 : 10 })}
                className={`${input} mt-1 w-44`}
                data-testid="supplier-period"
              >
                <option value="week">раз в неделю</option>
                <option value="month">раз в месяц</option>
              </select>
            </label>
            <label className="block">
              <span className="text-muted">{state.supplier_period === 'week' ? 'в день' : 'числа'}</span>
              {state.supplier_period === 'week' ? (
                <select value={state.supplier_day} onChange={(e) => save({ supplier_day: Number(e.target.value) })} className={`${input} mt-1 w-40`} data-testid="supplier-day">
                  {WEEKDAYS.map((w, i) => (
                    <option key={w} value={i + 1}>
                      {w.toLowerCase()}
                    </option>
                  ))}
                </select>
              ) : (
                <input type="number" min={1} max={31} value={state.supplier_day} onChange={(e) => Number(e.target.value) >= 1 && save({ supplier_day: Number(e.target.value) })} className={`${input} mt-1 w-24`} data-testid="supplier-day" />
              )}
            </label>
            <span className="max-w-xs text-xs text-faint">За продажи прошлой {state.supplier_period === 'week' ? 'недели' : 'месяца'} — по себестоимости с НДС.</span>
          </div>
        )}
      </div>
      <FormError message={error} />
    </section>
  )
}

// ---------- строка платежа и действия ----------

function LineAmount({ ln }: { ln: CalendarLine }) {
  if (ln.amount === null) return <span className="text-xs text-faint">сумма неизвестна</span>
  const income = ln.kind === 'income'
  const muted = ln.status === 'skipped' || ln.status === 'disabled'
  return (
    <span className={`whitespace-nowrap font-semibold tabular-nums ${muted ? 'text-faint line-through' : income ? 'text-ok' : 'text-bad'}`}>
      {ln.estimate ? '≈ ' : ''}
      {signed(ln.amount)}
    </span>
  )
}

function LineDetails({ ln, accounts, planning, onDone, onClose }: { ln: CalendarLine; accounts: Account[]; planning: boolean; onDone: () => void; onClose: () => void }) {
  const today = todayMsk()
  const active = accounts.filter((a) => !a.archived)
  const [day, setDay] = useState(ln.day)
  const [amount, setAmount] = useState(ln.amount === null ? '' : String(Math.abs(Number(ln.amount))).replace('.', ','))
  const [paidDay, setPaidDay] = useState(ln.day <= today ? ln.day : today)
  const [account, setAccount] = useState(ln.account?.id ?? active[0]?.id ?? '')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function run(action: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await action()
      onDone()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }
  const override = (body: Record<string, unknown>) => run(() => api.post('/planning/calendar/override', { key: ln.key, ...body }))
  const value = parseAmount(amount)
  const button = 'rounded-lg border border-line px-3 py-2 text-sm hover:border-accent hover:text-accent disabled:opacity-50'
  const row = (label: string, v: React.ReactNode, testid?: string) => (
    <div className="grid grid-cols-[8rem_minmax(0,1fr)] gap-3 border-b border-line py-2 last:border-b-0" data-testid={testid}>
      <dt className="text-muted">{label}</dt>
      <dd className="min-w-0 break-words">{v}</dd>
    </div>
  )
  const paidMarked = ln.status === 'paid' && ln.fact?.marked
  return (
    <div className="space-y-4" data-testid="line-card">
      <div>
        <div className="text-2xl">
          <LineAmount ln={ln} />
        </div>
        <div className="text-sm text-muted">
          {ln.kind === 'income' ? 'Поступление' : 'Платёж'} · {dayTitle(ln.day)}
          {ln.moved && ` · перенесён с ${fmtDay(ln.planned_day)}`}
        </div>
      </div>
      <dl className="text-sm">
        {row('Откуда', `${SOURCE_LABELS[ln.source]}${ln.schedule ? ` · ${ln.schedule}` : ''}`)}
        {ln.period_label && row('За период', ln.period_label)}
        {ln.notice && row(ln.notice.doc ?? 'Уведомление', `до ${fmtDay(ln.notice.day)}`)}
        {ln.note && row('Сумма', ln.note, 'line-note')}
        {ln.own_amount && ln.plan_amount && row('По плану', rub(ln.plan_amount))}
        {ln.category && row('Статья', ln.category.name)}
        {ln.account && row('Счёт', ln.account.name)}
        {ln.counterparty && row('Контрагент', ln.counterparty)}
        {ln.comment && row('Описание', <span className="whitespace-pre-wrap">{ln.comment}</span>)}
        {ln.fact &&
          row(
            'Факт',
            <>
              {fmtDay(ln.fact.day)} · {signed(ln.fact.amount)} {ln.fact.marked ? '(отмечено)' : '(найдено по сумме и дате)'}
              {ln.diff && <span className="block text-warn">Расхождение с планом: {signed(ln.diff)}</span>}
            </>,
            'line-fact',
          )}
      </dl>
      {(ln.calc || ln.link) && (
        <div className="flex flex-wrap gap-4 text-sm">
          {ln.calc && (
            <Link href={calcHref(ln.calc)} className="text-accent hover:underline" data-testid="line-calc">
              Расчёт за период →
            </Link>
          )}
          {ln.link && (
            <Link href={ln.link} className="text-accent hover:underline">
              График выплат Ozon →
            </Link>
          )}
        </div>
      )}

      {!ln.editable ? (
        <p className="text-sm text-muted">Выплата Ozon приходит по графику Ozon: оплаченной её делает сама выплата, перенести её нельзя. Выключить в прогнозе — переключателем «Выплаты Ozon».</p>
      ) : (
        <div className="space-y-3">
          {ln.status !== 'paid' && (
            <div className="rounded-lg border border-line p-3">
              <div className="mb-2 text-sm font-medium">Оплачено</div>
              <div className="flex flex-wrap items-end gap-2">
                <input type="date" value={paidDay} max={today} onChange={(e) => setPaidDay(e.target.value)} className={`${input} w-40`} aria-label="Дата оплаты" />
                <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" className={`${input} w-36`} aria-label="Сумма оплаты" data-testid="paid-amount" />
                {planning && active.length > 1 && (
                  <select value={account} onChange={(e) => setAccount(e.target.value)} className={`${input} w-44`} aria-label="Счёт">
                    {active.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                )}
                <button
                  type="button"
                  disabled={busy || value === null}
                  onClick={() => run(() => api.post('/planning/calendar/paid', { key: ln.key, day: paidDay, amount: value, ...(planning && account ? { account_id: account } : {}) }))}
                  className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
                  data-testid="line-paid"
                >
                  Отметить оплату
                </button>
              </div>
              <p className="mt-1 text-xs text-faint">{planning ? 'Запишем операцию на счёт — остаток и прогноз пересчитаются.' : 'Отметка — без операции: «Планирование» не включено.'}</p>
            </div>
          )}
          {paidMarked && (
            <button type="button" disabled={busy} onClick={() => run(() => api.post('/planning/calendar/unpaid', { key: ln.key }))} className={button} data-testid="line-unpaid">
              Снять отметку «оплачено»
            </button>
          )}
          {ln.status !== 'paid' && (
            <div className="flex flex-wrap items-end gap-2">
              <label className="block text-sm">
                <span className="text-muted">Перенести на</span>
                <input type="date" value={day} onChange={(e) => setDay(e.target.value)} className={`${input} mt-1 w-40`} data-testid="line-day" />
              </label>
              <button type="button" disabled={busy || day === ln.day} onClick={() => override({ day })} className={button} data-testid="line-move">
                Перенести
              </button>
              <button type="button" disabled={busy || value === null} onClick={() => override({ amount: value })} className={button} data-testid="line-amount">
                Своя сумма
              </button>
              <button type="button" disabled={busy} onClick={() => override({ skipped: !ln.skipped })} className={button} data-testid="line-skip">
                {ln.skipped ? 'Не пропускать' : 'Пропустить'}
              </button>
              {(ln.moved || ln.own_amount || ln.skipped) && (
                <button type="button" disabled={busy} onClick={() => override({ reset: true })} className={button} data-testid="line-reset">
                  Как по плану
                </button>
              )}
            </div>
          )}
          {ln.plan_id && (
            <button type="button" disabled={busy} onClick={() => run(() => api.patch(`/planning/plans/${ln.plan_id}`, { enabled: !ln.enabled }))} className={button} data-testid="line-plan-toggle">
              {ln.enabled ? 'Выключить весь план — «что если»' : 'Включить план'}
            </button>
          )}
        </div>
      )}
      <FormError message={error} />
      <button type="button" onClick={onClose} className="text-sm text-muted hover:text-fg">
        Закрыть
      </button>
    </div>
  )
}

function LineRow({ ln, onOpen, draggable }: { ln: CalendarLine; onOpen: () => void; draggable: boolean }) {
  const status = STATUS[ln.status]
  const sub = [ln.period_label, ln.category?.name, ln.counterparty].filter(Boolean).join(' · ')
  return (
    <li
      onClick={onOpen}
      draggable={draggable}
      onDragStart={(e) => {
        e.dataTransfer.setData('text/plain', ln.key)
        e.dataTransfer.effectAllowed = 'move'
      }}
      className="grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 px-4 py-2.5 text-sm hover:bg-line/40 sm:items-center"
      data-testid="cal-line"
      data-key={ln.key}
    >
      <span className="flex min-w-0 items-start gap-2">
        {draggable && <GripVertical className="mt-0.5 hidden h-4 w-4 shrink-0 text-faint sm:block" aria-hidden />}
        <span className="min-w-0">
          <span className="font-medium">{ln.title}</span>
          <span className="ml-2 inline-flex flex-wrap gap-1 align-middle">
            <Badge tone={SOURCE_TONE[ln.source]}>{SOURCE_LABELS[ln.source]}</Badge>
            {status && <Badge tone={status.tone}>{status.label}</Badge>}
            {ln.moved && <Badge tone="muted">перенесён</Badge>}
          </span>
          {sub && <span className="block truncate text-xs text-muted">{sub}</span>}
          {ln.diff && <span className="block text-xs text-warn">факт {signed(ln.fact!.amount)}, расхождение {signed(ln.diff)}</span>}
        </span>
      </span>
      <LineAmount ln={ln} />
    </li>
  )
}

// ---------- список по дням и месяц ----------

function useDrop(onMove: (key: string, day: string) => void) {
  return (day: string) => ({
    onDragOver: (e: React.DragEvent) => {
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'
    },
    onDrop: (e: React.DragEvent) => {
      e.preventDefault()
      const key = e.dataTransfer.getData('text/plain')
      if (key) onMove(key, day)
    },
  })
}

const movable = (ln: CalendarLine) => ln.editable && (ln.status === 'planned' || ln.status === 'overdue')

function DayList({ data, selected, onOpen, onMove }: { data: CalendarData; selected: string | null; onOpen: (ln: CalendarLine) => void; onMove: (key: string, day: string) => void }) {
  const drop = useDrop(onMove)
  const balance = useMemo(() => Object.fromEntries(data.days.map((d) => [d.day, d.balance])), [data.days])
  const groups = useMemo(() => {
    const out: { day: string; lines: CalendarLine[] }[] = []
    for (const ln of data.lines) {
      const last = out[out.length - 1]
      if (last && last.day === ln.day) last.lines.push(ln)
      else out.push({ day: ln.day, lines: [ln] })
    }
    return out
  }, [data.lines])
  if (groups.length === 0)
    return <p className="rounded-xl border border-line bg-card p-4 text-sm text-muted">В этом периоде платежей нет. Добавьте плановый платёж ниже — или включите выплаты Ozon и налоги.</p>
  return (
    <section className="overflow-hidden rounded-xl border border-line bg-card" data-testid="cal-list">
      {groups.map((g) => {
        const b = balance[g.day]
        const past = g.day < data.today
        return (
          <div key={g.day} id={`day-${g.day}`} {...drop(g.day)} className={selected === g.day ? 'bg-accent/5' : ''} data-testid="cal-day">
            <div className="flex items-center gap-3 border-b border-line bg-bg/40 px-4 py-1.5 text-xs">
              <span className={`font-semibold ${g.day === data.today ? 'text-accent' : past ? 'text-muted' : ''}`}>
                {dayTitle(g.day)}
                {g.day === data.today && ' · сегодня'}
              </span>
              {b !== undefined && b !== null && (
                <span className={`ml-auto tabular-nums ${Number(b) < 0 ? 'text-bad' : data.threshold !== null && Number(b) < Number(data.threshold) ? 'text-warn' : 'text-muted'}`}>
                  остаток {rub(b)}
                </span>
              )}
            </div>
            <ul className="divide-y divide-line border-b border-line">
              {g.lines.map((ln) => (
                <LineRow key={ln.key} ln={ln} onOpen={() => onOpen(ln)} draggable={movable(ln)} />
              ))}
            </ul>
          </div>
        )
      })}
    </section>
  )
}

function MonthGrid({ data, selected, onSelect, onOpen, onMove }: { data: CalendarData; selected: string | null; onSelect: (day: string) => void; onOpen: (ln: CalendarLine) => void; onMove: (key: string, day: string) => void }) {
  const drop = useDrop(onMove)
  const start = addDays(data.date_from, -weekday(data.date_from))
  const end = addDays(data.date_to, 6 - weekday(data.date_to))
  const cells: string[] = []
  for (let d = start; d <= end; d = addDays(d, 1)) cells.push(d)
  const byDay = useMemo(() => {
    const m: Record<string, CalendarLine[]> = {}
    for (const ln of data.lines) (m[ln.day] ??= []).push(ln)
    return m
  }, [data.lines])
  const balance = useMemo(() => Object.fromEntries(data.days.map((d) => [d.day, d.balance])), [data.days])
  const threshold = data.threshold === null ? 0 : Number(data.threshold)
  const day = selected && byDay[selected] ? selected : null
  return (
    <div className="space-y-3">
      <section className="overflow-hidden rounded-xl border border-line bg-line" data-testid="cal-month">
        <div className="grid grid-cols-7 gap-px text-center text-[11px] uppercase tracking-wider text-muted">
          {WEEK_SHORT.map((w) => (
            <div key={w} className="bg-card py-1">
              {w}
            </div>
          ))}
        </div>
        <div className="grid grid-cols-7 gap-px">
          {cells.map((d) => {
            const inRange = d >= data.date_from && d <= data.date_to
            const lines = byDay[d] ?? []
            const b = balance[d]
            const tone = b === undefined || b === null ? 'text-faint' : Number(b) < 0 ? 'text-bad' : Number(b) < threshold ? 'text-warn' : 'text-muted'
            const date = parseDay(d)
            return (
              <div
                key={d}
                {...(inRange ? drop(d) : {})}
                onClick={() => inRange && onSelect(d)}
                className={`min-h-[3.5rem] cursor-pointer bg-card p-1 text-left text-xs sm:min-h-[6.5rem] sm:p-1.5 ${inRange ? '' : 'opacity-40'} ${selected === d ? 'ring-2 ring-inset ring-accent' : ''}`}
                data-testid="cal-cell"
                data-day={d}
              >
                <div className={`flex items-baseline gap-1 ${d === data.today ? 'font-bold text-accent' : ''}`}>
                  <span>{date.getUTCDate()}</span>
                  {(date.getUTCDate() === 1 || d === start) && <span className="text-faint">{MONTHS_SHORT[date.getUTCMonth()]}</span>}
                  {lines.length > 0 && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-accent sm:hidden" aria-hidden />}
                </div>
                <ul className="mt-1 hidden space-y-0.5 sm:block">
                  {lines.slice(0, 3).map((ln) => (
                    <li
                      key={ln.key}
                      draggable={movable(ln)}
                      onDragStart={(e) => {
                        e.stopPropagation()
                        e.dataTransfer.setData('text/plain', ln.key)
                      }}
                      onClick={(e) => {
                        e.stopPropagation()
                        onOpen(ln)
                      }}
                      className={`truncate rounded px-1 ${ln.kind === 'income' ? 'bg-ok/10 text-ok' : 'bg-bad/10 text-bad'} ${ln.status === 'paid' || ln.status === 'skipped' || ln.status === 'disabled' ? 'opacity-50' : ''} ${movable(ln) ? 'cursor-grab' : ''}`}
                      title={`${ln.title}${ln.amount ? `: ${rub(ln.amount)}` : ''}`}
                      data-testid="cal-chip"
                      data-key={ln.key}
                    >
                      {ln.title}
                    </li>
                  ))}
                  {lines.length > 3 && <li className="px-1 text-faint">ещё {lines.length - 3}</li>}
                </ul>
                {b !== undefined && b !== null && <div className={`mt-1 hidden truncate tabular-nums sm:block ${tone}`}>{compact(Number(b))}</div>}
              </div>
            )
          })}
        </div>
      </section>
      {day && (
        <section className="overflow-hidden rounded-xl border border-line bg-card" data-testid="cal-day-panel">
          <div className="border-b border-line px-4 py-2 text-sm font-semibold">
            {dayTitle(day)}
            {balance[day] && <span className="ml-2 font-normal text-muted">остаток на конец дня {rub(balance[day])}</span>}
          </div>
          <ul className="divide-y divide-line">
            {byDay[day].map((ln) => (
              <LineRow key={ln.key} ln={ln} onOpen={() => onOpen(ln)} draggable={false} />
            ))}
          </ul>
        </section>
      )}
      <p className="text-xs text-faint">Перетащите платёж на другой день — прогноз пересчитается сразу. На телефоне — нажмите на день, затем на платёж: «Перенести на».</p>
    </div>
  )
}

// ---------- плановые платежи: форма и список ----------

function PlanForm({ categories, accounts, data, initial, onSaved, onCancel }: { categories: Category[]; accounts: Account[]; data: CalendarData; initial?: Plan; onSaved: (note: string) => void; onCancel?: () => void }) {
  const today = todayMsk()
  const [kind, setKind] = useState<PlanKind>(initial?.kind ?? 'expense')
  const [title, setTitle] = useState(initial?.title ?? '')
  const [amount, setAmount] = useState(initial ? String(Number(initial.amount)).replace('.', ',') : '')
  const [period, setPeriod] = useState<Period>(initial?.period ?? 'month')
  const [day, setDay] = useState<string>(initial?.day ? String(initial.day) : '')
  const [start, setStart] = useState(initial?.purchase_day ?? initial?.start_date ?? today)
  const [end, setEnd] = useState(initial?.end_date ?? '')
  const [categoryText, setCategoryText] = useState(initial?.category?.name ?? '')
  const [typed, setTyped] = useState(false)
  const [account, setAccount] = useState(initial?.account?.id ?? '')
  const [counterparty, setCounterparty] = useState(initial?.counterparty ?? '')
  const [comment, setComment] = useState(initial?.comment ?? '')
  const [purchase, setPurchase] = useState(Boolean(initial?.purchase_day))
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const cats = categories.filter((c) => (!c.archived && c.kind === kind) || c.id === initial?.category?.id)
  const match = categoryText.trim() ? cats.find((c) => sameName(c.name, categoryText)) : undefined
  const deferral = data.deferral_days
  const recurring = period !== 'once' && !purchase

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    const value = parseAmount(amount)
    if (value === null || Number(value) <= 0) return setError('Сумма — числом больше нуля, например 30 000')
    const body = {
      kind,
      title: title.trim() || null,
      amount: value,
      period: purchase ? 'once' : period,
      day: recurring && day ? Number(day) : null,
      start_date: start,
      end_date: recurring && end ? end : null,
      purchase_day: purchase ? start : null,
      account_id: account || null,
      counterparty: counterparty.trim() || null,
      comment: comment.trim() || null,
      ...(match ? { category_id: match.id } : categoryText.trim() ? { category_name: categoryText.trim() } : { category_id: null }),
    }
    setSaving(true)
    try {
      const { data: saved } = initial
        ? await api.patch<{ category_note: string | null }>(`/planning/plans/${initial.plan_id}`, body)
        : await api.post<{ category_note: string | null }>('/planning/plans', body)
      if (!initial) {
        setTitle('')
        setAmount('')
        setComment('')
        setCounterparty('')
      }
      onSaved(saved.category_note === 'created' ? 'Сохранено. Новая статья добавлена в список.' : 'Сохранено.')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3" data-testid={initial ? 'plan-edit-form' : 'plan-form'}>
      <div className="flex flex-wrap gap-1 rounded-lg border border-line bg-bg p-1" role="group" aria-label="Расход или приход">
        {(['expense', 'income'] as PlanKind[]).map((k) => (
          <button
            key={k}
            type="button"
            aria-pressed={kind === k}
            onClick={() => {
              setKind(k)
              setCategoryText('')
            }}
            className={`flex-1 rounded-md px-3 py-1.5 text-sm ${kind === k ? (k === 'income' ? 'bg-ok text-white' : 'bg-bad text-white') : 'text-muted hover:text-fg'}`}
            data-testid={`plan-kind-${k}`}
          >
            {k === 'income' ? 'Поступление' : 'Платёж'}
          </button>
        ))}
      </div>
      {!initial && data.hints.length > 0 && kind === 'expense' && (
        <div className="text-xs text-muted" data-testid="plan-hints">
          Своё производство — регулярные расходы, которые стоит запланировать, чтобы прогноз их учитывал:
          <span className="mt-1 flex flex-wrap gap-1.5">
            {data.hints.map((h) => (
              <button
                key={h.code}
                type="button"
                title={h.why}
                onClick={() => {
                  setTitle(h.name)
                  setCategoryText(h.name)
                  setTyped(false)
                  setPeriod('month')
                }}
                className="rounded-full border border-line px-2.5 py-1 hover:border-accent hover:text-accent"
              >
                {h.name}
              </button>
            ))}
          </span>
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="block text-sm lg:col-span-2">
          <span className="text-muted">Название</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="Например, аренда склада" className={`${input} mt-1`} data-testid="plan-title" />
        </label>
        <label className="block text-sm">
          <span className="text-muted">Сумма, ₽</span>
          <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" required className={`${input} mt-1`} data-testid="plan-amount" />
        </label>
        <label className="block text-sm">
          <span className="text-muted">Как часто</span>
          <select value={purchase ? 'once' : period} disabled={purchase} onChange={(e) => setPeriod(e.target.value as Period)} className={`${input} mt-1`} data-testid="plan-period">
            {PERIODS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm">
          <span className="text-muted">{purchase ? 'Дата закупки' : recurring ? 'Начиная с' : 'Дата'}</span>
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} required className={`${input} mt-1`} data-testid="plan-start" />
        </label>
        {recurring && (
          <label className="block text-sm">
            <span className="text-muted">{period === 'week' ? 'День недели' : 'Число месяца'}</span>
            {period === 'week' ? (
              <select value={day} onChange={(e) => setDay(e.target.value)} className={`${input} mt-1`} data-testid="plan-day">
                <option value="">как у даты начала</option>
                {WEEKDAYS.map((w, i) => (
                  <option key={w} value={i + 1}>
                    {w}
                  </option>
                ))}
              </select>
            ) : (
              <input type="number" min={1} max={31} value={day} onChange={(e) => setDay(e.target.value)} placeholder="как у даты начала" className={`${input} mt-1`} data-testid="plan-day" />
            )}
          </label>
        )}
        {recurring && (
          <label className="block text-sm">
            <span className="text-muted">До (необязательно)</span>
            <input type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value)} className={`${input} mt-1`} data-testid="plan-end" />
          </label>
        )}
        <label className="block text-sm">
          <span className="text-muted">Статья</span>
          <input
            value={categoryText}
            list="plan-cats"
            onChange={(e) => {
              const kindOf = (e.nativeEvent as InputEvent).inputType
              setTyped(kindOf !== undefined && kindOf !== 'insertReplacementText')
              setCategoryText(e.target.value)
            }}
            onBlur={() => match && setCategoryText(match.name)}
            placeholder="Выберите или впишите новую"
            className={`${input} mt-1`}
            data-testid="plan-category"
          />
          <datalist id="plan-cats">
            {cats.map((c) => (
              <option key={c.id} value={c.name} />
            ))}
          </datalist>
          <CategoryHint text={categoryText} match={match} typed={typed} />
        </label>
        <label className="block text-sm">
          <span className="text-muted">Счёт</span>
          <select value={account} onChange={(e) => setAccount(e.target.value)} className={`${input} mt-1`} data-testid="plan-account">
            <option value="">любой</option>
            {accounts
              .filter((a) => !a.archived || a.id === account)
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
          </select>
        </label>
      </div>
      {deferral !== null && kind === 'expense' && (
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" checked={purchase} onChange={(e) => setPurchase(e.target.checked)} className="mt-1" data-testid="plan-purchase" />
          <span>
            Это закупка с отсрочкой — оплата через {deferral} дн. после даты закупки
            <span className="block text-xs text-faint">Отсрочка — из «Откуда товар» в основных настройках: поменяете её — дата оплаты сдвинется сама.</span>
          </span>
        </label>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <input value={counterparty} onChange={(e) => setCounterparty(e.target.value)} placeholder="Контрагент — по нему узнаем оплату в операциях" aria-label="Контрагент" className={input} data-testid="plan-counterparty" />
        <textarea value={comment} onChange={(e) => setComment(e.target.value)} maxLength={2000} rows={1} placeholder="Описание — необязательно" aria-label="Описание" className={`${input} resize-y`} />
      </div>
      <FormError message={error} />
      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={saving} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="plan-save">
          {saving ? 'Сохраняем…' : initial ? 'Сохранить' : 'Добавить в план'}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} className="rounded-lg border border-line px-4 py-2 text-sm text-muted hover:text-fg">
            Отмена
          </button>
        )}
      </div>
    </form>
  )
}

function PlansList({ plans, onEdit, onChanged }: { plans: Plan[]; onEdit: (p: Plan) => void; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null)
  async function run(action: () => Promise<unknown>) {
    setError(null)
    try {
      await action()
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    }
  }
  if (plans.length === 0) return null
  return (
    <section className="overflow-hidden rounded-xl border border-line bg-card" data-testid="plans-list">
      <h2 className="border-b border-line px-4 py-2 text-sm font-semibold">Ваши плановые платежи</h2>
      <ul className="divide-y divide-line">
        {plans.map((p) => (
          <li key={p.plan_id} className={`grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 px-4 py-2.5 text-sm ${p.enabled ? '' : 'opacity-60'}`} data-testid="plan-row">
            <span className="min-w-0">
              <span className="font-medium">{p.title}</span>
              {!p.enabled && (
                <span className="ml-2">
                  <Badge tone="muted">выключен</Badge>
                </span>
              )}
              <span className="block text-xs text-muted sm:truncate">
                {p.schedule}
                {p.next_day && ` · ближайший ${fmtDay(p.next_day)}`}
              </span>
            </span>
            <span className="flex items-center gap-2">
              <span className={`whitespace-nowrap font-semibold tabular-nums ${p.kind === 'income' ? 'text-ok' : 'text-bad'}`}>
                {p.kind === 'income' ? '+' : '−'}
                {rub(p.amount)}
              </span>
              <button
                type="button"
                role="switch"
                aria-checked={p.enabled}
                aria-label={p.enabled ? 'Выключить план' : 'Включить план'}
                onClick={() => run(() => api.patch(`/planning/plans/${p.plan_id}`, { enabled: !p.enabled }))}
                className={`relative h-5 w-9 shrink-0 rounded-full transition ${p.enabled ? 'bg-accent' : 'bg-line'}`}
                data-testid="plan-toggle"
              >
                <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-card shadow transition ${p.enabled ? 'left-4' : 'left-0.5'}`} />
              </button>
              <button type="button" onClick={() => onEdit(p)} className="rounded p-1 text-muted hover:text-fg" aria-label="Изменить">
                <Pencil className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={() => window.confirm('Удалить план? Оплаченные платежи (операции) останутся.') && run(() => api.delete(`/planning/plans/${p.plan_id}`))}
                className="rounded p-1 text-muted hover:text-bad"
                aria-label="Удалить"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </span>
          </li>
        ))}
      </ul>
      <FormError message={error} />
    </section>
  )
}

// ---------- вкладка ----------

export function CalendarTab({ accounts, categories, state, onChanged }: { accounts: Account[]; categories: Category[]; state: PlanningState; onChanged: () => void }) {
  const params = useSearchParams()
  const selectedParam = params.get('day')
  const today = todayMsk()
  const [horizon, setHorizon] = useState(() => HORIZONS.reduce((a, b) => (Math.abs(b - state.horizon_days) < Math.abs(a - state.horizon_days) ? b : a)))
  const [view, setView] = useState<'list' | 'month'>('list')
  const [data, setData] = useState<CalendarData | null>(null)
  const [plans, setPlans] = useState<Plan[]>([])
  const [error, setError] = useState<string | null>(null)
  const [opened, setOpened] = useState<CalendarLine | null>(null)
  const [editing, setEditing] = useState<Plan | null>(null)
  const [selected, setSelected] = useState<string | null>(selectedParam)
  const [notice, setNotice] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState<boolean | null>(null) // планов ещё нет — форма открыта; дальше — как оставил пользователь
  const scrolled = useRef<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [c, p] = await Promise.all([
        api.get<CalendarData>('/planning/calendar', { params: { date_from: addDays(today, -PAST_DAYS), date_to: addDays(today, horizon - 1) } }),
        api.get<Plan[]>('/planning/plans'),
      ])
      setData(c.data)
      setPlans(p.data)
      setFormOpen((open) => open ?? p.data.length === 0)
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [today, horizon])

  useEffect(() => {
    load()
  }, [load])

  // клик по точке графика — день в календаре
  useEffect(() => {
    if (!selectedParam) return
    setSelected(selectedParam)
    if (data && scrolled.current !== selectedParam) {
      scrolled.current = selectedParam
      document.getElementById(view === 'list' ? `day-${selectedParam}` : 'cal-month')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [selectedParam, data, view])

  const changed = () => {
    load()
    onChanged()
  }

  async function move(key: string, day: string) {
    const ln = data?.lines.find((l) => l.key === key)
    if (!ln || ln.day === day) return
    try {
      await api.post('/planning/calendar/override', { key, day })
      setNotice(`«${ln.title}» перенесён на ${fmtDay(day)} — прогноз пересчитан.`)
      load()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const facts = data?.facts ?? []
  return (
    <div className="space-y-4" data-testid="calendar-tab">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-1 rounded-lg border border-line bg-card p-1" role="group" aria-label="Горизонт">
          {HORIZONS.map((h) => (
            <button key={h} type="button" aria-pressed={horizon === h} onClick={() => setHorizon(h)} className={`rounded-md px-3 py-1 text-sm ${horizon === h ? 'bg-accent text-white' : 'text-muted hover:text-fg'}`} data-testid={`horizon-${h}`}>
              {h} дн.
            </button>
          ))}
        </div>
        <div className="ml-auto flex gap-1 rounded-lg border border-line bg-card p-1" role="group" aria-label="Вид">
          <button type="button" aria-pressed={view === 'list'} onClick={() => setView('list')} className={`flex items-center gap-1.5 rounded-md px-3 py-1 text-sm ${view === 'list' ? 'bg-accent text-white' : 'text-muted hover:text-fg'}`} data-testid="view-list">
            <List className="h-4 w-4" /> Список
          </button>
          <button type="button" aria-pressed={view === 'month'} onClick={() => setView('month')} className={`flex items-center gap-1.5 rounded-md px-3 py-1 text-sm ${view === 'month' ? 'bg-accent text-white' : 'text-muted hover:text-fg'}`} data-testid="view-month">
            <CalendarDays className="h-4 w-4" /> По дням
          </button>
        </div>
      </div>
      <FormError message={error} />
      {!data && !error && <p className="text-sm text-muted">Загрузка…</p>}
      {data && (
        <>
          <Summary data={data} />
          <GapBanner data={data} />
          {data.warnings.length > 0 && (
            <ul className="space-y-1 text-sm text-warn" data-testid="cal-warnings">
              {data.warnings.map((w) => (
                <li key={w}>• {w}</li>
              ))}
            </ul>
          )}
          <ForecastChart data={data} onDay={(day) => `/dashboard/planning?tab=calendar&day=${day}`} />
          {notice && (
            <p className="text-sm text-ok" data-testid="cal-notice">
              {notice}
            </p>
          )}
          {view === 'list' ? (
            <DayList data={data} selected={selected} onOpen={setOpened} onMove={move} />
          ) : (
            <MonthGrid data={data} selected={selected} onSelect={setSelected} onOpen={setOpened} onMove={move} />
          )}
          {facts.length > 0 && (
            <section className="overflow-hidden rounded-xl border border-line bg-card" data-testid="cal-facts">
              <h2 className="border-b border-line px-4 py-2 text-sm font-semibold">
                Вне плана <span className="font-normal text-muted">— операции за эти дни, которых не было в плане</span>
              </h2>
              <ul className="divide-y divide-line">
                {facts.map((f) => (
                  <li key={f.op_id} className="grid grid-cols-[3.5rem_minmax(0,1fr)_auto] gap-x-3 px-4 py-2 text-sm">
                    <span className="tabular-nums text-muted">{fmtDay(f.day).slice(0, 5)}</span>
                    <span className="truncate">
                      {f.title}
                      {f.category && f.category !== f.title && <span className="text-muted"> · {f.category}</span>}
                    </span>
                    <span className={`whitespace-nowrap font-semibold tabular-nums ${Number(f.amount) > 0 ? 'text-ok' : 'text-bad'}`}>{signed(f.amount)}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
          <ForecastSettings data={data} state={state} onSaved={changed} />
          <details
            className="rounded-xl border border-line bg-card p-4"
            open={formOpen ?? false}
            onToggle={(e) => setFormOpen((e.currentTarget as HTMLDetailsElement).open)}
            data-testid="plan-new"
          >
            <summary className="cursor-pointer text-sm font-semibold">Новый плановый платёж</summary>
            <p className="mb-3 mt-1 text-xs text-faint">
              Аренда, зарплата, кредит, подписки, закупки — разовые и регулярные. Выплаты Ozon, налоги и оплату поставщику после продажи вводить не нужно: они
              появляются сами.
            </p>
            <PlanForm
              categories={categories}
              accounts={accounts}
              data={data}
              onSaved={(text) => {
                setNotice(text)
                changed()
              }}
            />
          </details>
          <PlansList plans={plans} onEdit={setEditing} onChanged={changed} />
          <ImportPanel
            base="/planning/plans"
            fileName="plans"
            allLabel="Шаблон с вашими планами (Excel)"
            intro="Скачайте шаблон — в нём ваши плановые платежи, счета и статьи. Исправьте или допишите строки и загрузите обратно: сначала покажем, что изменится."
            onApplied={changed}
          />
        </>
      )}
      {opened && data && (
        <Modal title={opened.title} onClose={() => setOpened(null)}>
          <LineDetails
            ln={opened}
            accounts={accounts}
            planning
            onClose={() => setOpened(null)}
            onDone={() => {
              setOpened(null)
              changed()
            }}
          />
        </Modal>
      )}
      {editing && data && (
        <Modal title="Плановый платёж" onClose={() => setEditing(null)}>
          <PlanForm
            categories={categories}
            accounts={accounts}
            data={data}
            initial={editing}
            onCancel={() => setEditing(null)}
            onSaved={(text) => {
              setEditing(null)
              setNotice(text)
              changed()
            }}
          />
        </Modal>
      )}
    </div>
  )
}

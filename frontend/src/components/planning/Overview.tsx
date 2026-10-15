'use client'
// «Планирование → Обзор» (шаг 3.10) — дашборд раздела, не главная: деньги сейчас, ближайшая выплата Ozon, кассовый
// разрыв или «денег хватит до …», что ожидается за горизонт; график — слева от «сегодня» факт, справа план; ближайшие
// платежи на 14 дней с отметкой «оплачено». Нет данных — «нет данных», а не 0.
import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { Bar, CartesianGrid, Cell, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { FormError } from '@/components/AuthForm'
import { type Drill, MoreLink, TipBox, useDrill } from '@/components/charts'
import { compact, useTokenColors } from '@/components/finance'
import { GapBanner, LineAmount, LineDetails, SOURCE_TONE, STATUS, dayTitle, signed } from '@/components/planning/Calendar'
import { Modal } from '@/components/planning/Operations'
import { type Account, type CalendarLine, type Overview, type SeriesDay, SOURCE_LABELS, opsHref } from '@/components/planning/types'
import { Badge, fmtDay, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { addDays } from '@/lib/period'

const HORIZONS: { days: number; label: string }[] = [
  { days: 28, label: '4 нед.' },
  { days: 56, label: '8 нед.' },
  { days: 30, label: '30 дн.' },
  { days: 60, label: '60 дн.' },
  { days: 90, label: '90 дн.' },
]
const short = (iso: string) => iso.split('-').reverse().slice(0, 2).join('.')

function Figure({ label, children, hint, testid }: { label: string; children: React.ReactNode; hint?: React.ReactNode; testid?: string }) {
  return (
    <div className="min-w-[10rem] flex-1 bg-card px-4 py-3" data-testid={testid}>
      <div className="text-[11px] uppercase tracking-wider text-muted">{label}</div>
      <div className="mt-1 text-lg font-bold tabular-nums">{children}</div>
      {hint && <div className="text-xs text-faint">{hint}</div>}
    </div>
  )
}

function Figures({ data }: { data: Overview }) {
  const e = data.expected
  const income = Number(e.ozon) + Number(e.income)
  const net = income + Number(e.expense) // итог за горизонт: поступления минус платежи
  return (
    <div className="flex flex-wrap gap-px overflow-hidden rounded-xl border border-line bg-line" data-testid="ov-figures">
      <Figure
        label="Деньги сейчас"
        hint={data.balance === null ? 'остаток не указан' : data.accounts.length > 1 ? data.accounts.map((a) => `${a.name}: ${a.balance === null ? '—' : rub(a.balance)}`).join(' · ') : undefined}
      >
        {data.balance === null ? '—' : rub(data.balance)}
      </Figure>
      <Figure
        label="Ближайшая выплата Ozon"
        testid="ov-ozon"
        hint={
          data.next_ozon
            ? `${fmtDay(data.next_ozon.day)}${data.next_ozon.estimate ? ' · оценка' : ''}`
            : !data.ozon.enabled
              ? 'выключены в прогнозе'
              : !data.ozon.has_key
                ? 'нет данных — подключите ключ Ozon'
                : 'нет данных о балансе Ozon'
        }
      >
        {data.next_ozon?.amount ? <span className="text-ok">{signed(data.next_ozon.amount)}</span> : '—'}
      </Figure>
      <Figure
        label="Денег хватит"
        testid="ov-enough"
        hint={data.first_below_zero ? `разрыв с ${fmtDay(data.first_below_zero)}` : data.min_balance ? `минимум ${rub(data.min_balance)}` : undefined}
      >
        {data.balance === null ? (
          '—'
        ) : data.first_below_zero ? (
          <span className="text-bad">до {fmtDay(addDays(data.first_below_zero, -1))}</span>
        ) : (
          <span className="text-ok">на весь период</span>
        )}
      </Figure>
      <Figure
        label={`Ожидается за ${data.horizon_days} дн.`}
        testid="ov-expected"
        hint={
          <>
            поступления <span className="text-ok">{signed(String(income))}</span>
            {Number(e.ozon) !== 0 && ` (Ozon ${rub(e.ozon)})`} · платежи <span className="text-bad">{rub(e.expense)}</span>
            {e.unknown > 0 && ` · без суммы: ${e.unknown}`}
          </>
        }
      >
        <span className={net < 0 ? 'text-bad' : 'text-ok'}>{signed(String(net))}</span>
      </Figure>
    </div>
  )
}

// ---------- график: факт и план ----------

type Row = {
  day: string
  label: string
  kind: SeriesDay['kind']
  ozon: number
  income: number
  expense: number
  fact: number | null // остаток — линия факта (до сегодня и сегодня — чтобы линии сошлись)
  plan: number | null
  src: SeriesDay
}

/** Где на линии граница «ниже порога»: доля высоты от верха (для градиента); вся линия выше — 1, ниже — 0. */
function offset(values: number[], limit: number): number {
  const max = Math.max(...values)
  const min = Math.min(...values)
  if (max <= limit) return 0
  if (min >= limit) return 1
  return (max - limit) / (max - min)
}

function ChartTip({ active, payload, drill, today }: { active?: boolean; payload?: { payload: Row }[]; drill: Drill<Row>; today: string }) {
  if (!active || !payload?.length) return null
  const r = payload[0].payload
  const s = r.src
  return (
    <TipBox href={drill.hrefFor(r)} touch={drill.touch}>
      <div className="font-medium">
        {dayTitle(r.day)} · {r.day === today ? 'сегодня' : r.kind === 'fact' ? 'факт' : 'план'}
      </div>
      <div className="tabular-nums">Остаток: {s.balance === null ? 'нет данных' : rub(s.balance)}</div>
      {Number(s.ozon) !== 0 && <div className="tabular-nums text-accent">Выплата Ozon: {signed(s.ozon)}</div>}
      {Number(s.income) !== 0 && <div className="tabular-nums text-ok">Поступления: {signed(s.income)}</div>}
      {Number(s.expense) !== 0 && <div className="tabular-nums text-bad">Платежи: {rub(s.expense)}</div>}
    </TipBox>
  )
}

function OverviewChart({ data }: { data: Overview }) {
  const colors = useTokenColors()
  const drill = useDrill<Row>((r) => (r.kind === 'fact' ? opsHref(r.day, r.day) : `/dashboard/planning?tab=calendar&day=${r.day}`))
  if (!colors) return null
  const today = data.today
  const rows: Row[] = data.series.map((s) => {
    const b = s.balance === null ? null : Number(s.balance)
    return {
      day: s.day,
      label: short(s.day),
      kind: s.kind,
      ozon: Number(s.ozon),
      income: Number(s.income),
      expense: Number(s.expense),
      fact: s.day <= today ? b : null,
      plan: s.day >= today ? b : null,
      src: s,
    }
  })
  const limit = data.threshold === null ? 0 : Math.max(Number(data.threshold), 0)
  const factValues = rows.flatMap((r) => (r.fact === null ? [] : [r.fact]))
  const planValues = rows.flatMap((r) => (r.plan === null ? [] : [r.plan]))
  // линия остатка: ниже порога (или нуля) — красным; ровная линия градиент не держит — тогда одним цветом
  const stroke = (id: string, values: number[]) => {
    if (values.length === 0) return colors.fg
    if (Math.max(...values) === Math.min(...values)) return values[0] < limit ? colors.bad : colors.fg
    return `url(#${id})`
  }
  const todayLabel = short(today)
  return (
    <section className="rounded-xl border border-line bg-card p-4" data-testid="ov-chart">
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
        <h2 className="text-sm font-semibold text-fg">Остаток и движение денег</h2>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0 w-3 border-t-2 border-fg" /> остаток (ниже {data.threshold === null ? 'нуля' : 'порога'} — красным)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-accent" /> выплаты Ozon
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-ok" /> поступления
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-bad" /> платежи
        </span>
        <span>слева — факт, справа — план (бледнее)</span>
        <MoreLink href="/dashboard/planning?tab=calendar" />
      </div>
      <div className={`h-64 ${drill.boxProps.className}`} onPointerDown={drill.boxProps.onPointerDown}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 12, right: 4, bottom: 0, left: 0 }} stackOffset="sign" onClick={drill.onClick}>
            <defs>
              <linearGradient id="ov-fact" x1="0" y1="0" x2="0" y2="1">
                <stop offset={offset(factValues, limit)} stopColor={colors.fg} />
                <stop offset={offset(factValues, limit)} stopColor={colors.bad} />
              </linearGradient>
              <linearGradient id="ov-plan" x1="0" y1="0" x2="0" y2="1">
                <stop offset={offset(planValues, limit)} stopColor={colors.fg} />
                <stop offset={offset(planValues, limit)} stopColor={colors.bad} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke={colors.line} />
            <XAxis dataKey="label" tick={{ fill: colors.muted, fontSize: 11 }} tickLine={false} axisLine={{ stroke: colors.line }} minTickGap={12} />
            <YAxis tick={{ fill: colors.muted, fontSize: 11 }} tickFormatter={compact} tickLine={false} axisLine={false} width={64} />
            <ReferenceLine y={0} stroke={colors.bad} strokeDasharray="4 3" />
            {data.threshold !== null && <ReferenceLine y={Number(data.threshold)} stroke={colors.warn} strokeDasharray="4 3" />}
            <ReferenceLine x={todayLabel} stroke={colors.faint} label={{ value: 'сегодня', position: 'top', fill: colors.muted, fontSize: 11 }} />
            <Tooltip content={<ChartTip drill={drill} today={today} />} cursor={{ fill: colors.line, fillOpacity: 0.4 }} {...drill.tooltipProps} />
            {(['ozon', 'income', 'expense'] as const).map((key) => (
              <Bar key={key} dataKey={key} stackId="flow" fill={key === 'ozon' ? colors.accent : key === 'income' ? colors.ok : colors.bad} isAnimationActive={false} maxBarSize={18}>
                {rows.map((r) => (
                  <Cell key={r.day} fillOpacity={r.kind === 'plan' ? 0.45 : 0.9} />
                ))}
              </Bar>
            ))}
            <Line type="monotone" dataKey="fact" stroke={stroke('ov-fact', factValues)} strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="plan" stroke={stroke('ov-plan', planValues)} strokeWidth={2} strokeDasharray="5 3" dot={false} connectNulls={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}

// ---------- ближайшие платежи ----------

function Upcoming({ data, accounts, onChanged }: { data: Overview; accounts: Account[]; onChanged: () => void }) {
  const [opened, setOpened] = useState<CalendarLine | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function paid(ln: CalendarLine) {
    setBusy(ln.key)
    setError(null)
    try {
      await api.post('/planning/calendar/paid', { key: ln.key })
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="overflow-hidden rounded-xl border border-line bg-card" data-testid="ov-upcoming">
      <div className="flex flex-wrap items-baseline gap-x-3 border-b border-line px-4 py-2">
        <h2 className="text-sm font-semibold">Ближайшие 14 дней</h2>
        <span className="text-xs text-faint">платежи и поступления по плану; оплатили — отметьте, остаток пересчитается</span>
        <Link href="/dashboard/planning?tab=calendar" className="ml-auto text-xs text-accent hover:underline">
          Календарь →
        </Link>
      </div>
      {data.upcoming.length === 0 ? (
        <p className="px-4 py-3 text-sm text-muted">На ближайшие две недели платежей нет.</p>
      ) : (
        <ul className="divide-y divide-line">
          {data.upcoming.map((ln) => {
            const status = STATUS[ln.status]
            const canPay = ln.editable && (ln.status === 'planned' || ln.status === 'overdue') && ln.amount !== null
            return (
              <li key={ln.key} className="grid grid-cols-[3.25rem_minmax(0,1fr)_auto] items-center gap-x-3 px-4 py-2 text-sm" data-testid="ov-line">
                <button type="button" onClick={() => setOpened(ln)} className="text-left tabular-nums text-muted">
                  {fmtDay(ln.day).slice(0, 5)}
                </button>
                <button type="button" onClick={() => setOpened(ln)} className="min-w-0 text-left">
                  <span className="font-medium">{ln.title}</span>
                  <span className="ml-2 inline-flex flex-wrap gap-1 align-middle">
                    <Badge tone={SOURCE_TONE[ln.source]}>{SOURCE_LABELS[ln.source]}</Badge>
                    {status && <Badge tone={status.tone}>{status.label}</Badge>}
                  </span>
                  {ln.period_label && <span className="block truncate text-xs text-muted">{ln.period_label}</span>}
                </button>
                <span className="flex items-center gap-2">
                  <LineAmount ln={ln} />
                  {canPay && (
                    <button type="button" disabled={busy === ln.key} onClick={() => paid(ln)} className="hidden rounded-md border border-line px-2 py-1 text-xs hover:border-accent hover:text-accent disabled:opacity-50 sm:inline-block" data-testid="ov-paid">
                      Оплачено
                    </button>
                  )}
                </span>
              </li>
            )
          })}
        </ul>
      )}
      <FormError message={error} />
      {opened && (
        <Modal title={opened.title} onClose={() => setOpened(null)}>
          <LineDetails
            ln={opened}
            accounts={accounts}
            planning
            onClose={() => setOpened(null)}
            onDone={() => {
              setOpened(null)
              onChanged()
            }}
          />
        </Modal>
      )}
    </section>
  )
}

// ---------- пустые состояния ----------

function EmptyHints({ data }: { data: Overview }) {
  const hints: React.ReactNode[] = []
  if (data.balance === null)
    hints.push(
      <li key="balance">
        Укажите, сколько денег на счёте сейчас — без этого прогноз остатка не построить.{' '}
        <Link href="/dashboard/planning?tab=accounts" className="text-accent hover:underline">
          Счета →
        </Link>
      </li>,
    )
  if (!data.ozon.has_key && data.ozon.enabled)
    hints.push(
      <li key="ozon">
        Выплат Ozon в прогнозе нет: подключите ключ Ozon API — даты и суммы выплат появятся сами, вводить их не нужно.{' '}
        <Link href="/dashboard/settings/api-keys" className="text-accent hover:underline">
          Подключить →
        </Link>
      </li>,
    )
  return (
    <>
      {hints.length > 0 && (
        <ul className="space-y-1 rounded-lg border border-warn/40 bg-warn/10 px-4 py-3 text-sm" data-testid="ov-hints">
          {hints}
        </ul>
      )}
      {data.empty.suggestions.length > 0 && (
        <section className="rounded-xl border border-line bg-card p-4" data-testid="ov-suggestions">
          <h2 className="text-sm font-semibold">Добавьте регулярные платежи</h2>
          <p className="mt-1 text-xs text-faint">Чтобы прогноз учитывал аренду, зарплату и другие платежи, которые повторяются. Можно добавить позже — или не добавлять.</p>
          <ul className="mt-3 grid gap-2 sm:grid-cols-2">
            {data.empty.suggestions.map((s) => (
              <li key={s.code} className="rounded-lg border border-line p-3 text-sm">
                <div className="flex items-baseline gap-2">
                  <span className="font-medium">{s.name}</span>
                  <Link href={`/dashboard/planning?tab=calendar&plan=${encodeURIComponent(s.name)}`} className="ml-auto whitespace-nowrap text-xs text-accent hover:underline" data-testid="ov-suggest">
                    Запланировать →
                  </Link>
                </div>
                <div className="mt-0.5 text-xs text-muted">{s.why}</div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  )
}

// ---------- вкладка ----------

export function OverviewTab({ accounts, horizonDays, onChanged }: { accounts: Account[]; horizonDays: number; onChanged: () => void }) {
  const [horizon, setHorizon] = useState(() => (HORIZONS.some((h) => h.days === horizonDays) ? horizonDays : 60))
  const [data, setData] = useState<Overview | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const { data: body } = await api.get<Overview>('/planning/overview', { params: { horizon_days: horizon } })
      setData(body)
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [horizon])

  useEffect(() => {
    load()
  }, [load])

  const changed = () => {
    load()
    onChanged()
  }
  // без ключа Ozon про выплаты уже сказано в подсказке выше
  const warnings = (data?.warnings ?? []).filter((w) => data?.ozon.has_key || !w.startsWith('Выплат Ozon в прогнозе нет'))

  return (
    <div className="space-y-4" data-testid="overview-tab">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1 rounded-lg border border-line bg-card p-1" role="group" aria-label="Горизонт">
          {HORIZONS.map((h) => (
            <button key={h.days} type="button" aria-pressed={horizon === h.days} onClick={() => setHorizon(h.days)} className={`rounded-md px-3 py-1 text-sm ${horizon === h.days ? 'bg-accent text-white' : 'text-muted hover:text-fg'}`} data-testid={`ov-horizon-${h.days}`}>
              {h.label}
            </button>
          ))}
        </div>
        <span className="text-xs text-faint">ДДС — по деньгам на счетах; прибыль по начислениям Ozon — в «Финансах»</span>
      </div>
      <FormError message={error} />
      {!data && !error && <p className="text-sm text-muted">Загрузка…</p>}
      {data && (
        <>
          <Figures data={data} />
          <GapBanner data={data} />
          <EmptyHints data={data} />
          {warnings.length > 0 && (
            <ul className="space-y-1 text-sm text-warn" data-testid="ov-warnings">
              {warnings.map((w) => (
                <li key={w}>• {w}</li>
              ))}
            </ul>
          )}
          <OverviewChart data={data} />
          <Upcoming data={data} accounts={accounts} onChanged={changed} />
        </>
      )}
    </div>
  )
}

'use client'
// Блоки страницы «Финансы» (шаг 3.3): источник данных, ручная сумма от Ozon, параметры расчёта, график по дням.
import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'
import { Bar, CartesianGrid, Cell, ComposedChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { type Drill, MoreLink, TipBox, useDrill } from '@/components/charts'
import { Badge, type PnL, fmtDay, rub, vatPctText } from '@/components/reports'
import { useTheme } from '@/contexts/ThemeContext'
import { daysWord } from '@/lib/period'
import { useCostLabel } from '@/lib/settings'

export type DataSource = 'xlsx' | 'api' | 'mixed' | 'none'

export type Coverage = {
  days_total: number
  xlsx_days: number
  api_days: number
  missing_days: number
  missing: { date_from: string; date_to: string }[]
}

export type DayPnL = {
  date: string
  source: 'xlsx' | 'api' | null
  revenue: string | null
  total: string | null
  owed: string | null
  profit_before_tax: string | null
}

// ---------- источник ----------

const SOURCE: Record<DataSource, { label: string; tone: 'accent' | 'warn' | 'muted'; text: string }> = {
  xlsx: {
    label: 'Из XLSX',
    tone: 'accent',
    text: 'По отчётам из личного кабинета Ozon — цифры совпадают с кабинетом.',
  },
  api: {
    label: 'Из API ⚠',
    tone: 'warn',
    text: 'По данным Ozon API: они могут расходиться с личным кабинетом (оговорка Ozon). Для точных цифр загрузите отчёт по начислениям за этот период.',
  },
  mixed: {
    label: 'XLSX + API ⚠',
    tone: 'warn',
    text: 'Где есть отчёт — по нему, остальные дни — по Ozon API (может расходиться с кабинетом).',
  },
  none: { label: 'Нет данных', tone: 'muted', text: 'За период нет ни отчётов, ни данных Ozon API.' },
}

export function SourceBadge({ source }: { source: DataSource }) {
  const s = SOURCE[source]
  return (
    <Badge tone={s.tone} title={s.text}>
      <span data-testid="source-badge">{s.label}</span>
    </Badge>
  )
}

/** Откуда цифры — словами: бейдж и пояснение, сколько дней из отчётов, из API и без данных. */
export function SourceNote({ source, coverage }: { source: DataSource; coverage?: Coverage }) {
  const parts: string[] = []
  if (coverage) {
    if (coverage.xlsx_days) parts.push(`из отчётов — ${daysWord(coverage.xlsx_days)}`)
    if (coverage.api_days) parts.push(`из API — ${daysWord(coverage.api_days)}`)
    if (coverage.missing_days && source !== 'none') parts.push(`нет данных — ${daysWord(coverage.missing_days)}`)
  }
  return (
    <div className="flex flex-wrap items-start gap-2 text-xs text-muted" data-testid="source-note">
      <SourceBadge source={source} />
      <span className="min-w-0 flex-1">
        {SOURCE[source].text}
        {parts.length > 0 && <span className="text-faint"> {parts.join(', ')}.</span>}
      </span>
    </div>
  )
}

// ---------- сумма от Ozon ----------

/** «12 000,50 ₽» → «12000.50»; не число — null. */
export function parseAmount(text: string): string | null {
  const s = text.replace(/[\s  ₽]/g, '').replace('−', '-').replace(',', '.')
  return /^-?\d+(\.\d{1,2})?$/.test(s) ? s : null
}

const plain = (v: string) => Number(v).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/** Сумма от Ozon с ручной правкой: «сколько реально пришло». Пустая или равная сумме по начислениям — авто. */
export function ManualTotal({
  auto,
  manual,
  onChange,
  note,
  status,
}: {
  auto: string
  manual: string | null
  onChange: (value: string | null) => void
  note: string
  status?: string | null
}) {
  const [text, setText] = useState(plain(manual ?? auto))
  const [editing, setEditing] = useState(false)
  const [invalid, setInvalid] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout>>()

  useEffect(() => {
    if (!editing) setText(plain(manual ?? auto))
  }, [auto, manual, editing])
  useEffect(() => () => clearTimeout(timer.current), [])

  function commit(value: string, delay: number) {
    clearTimeout(timer.current)
    const parsed = value.trim() === '' ? auto : parseAmount(value)
    setInvalid(parsed === null)
    if (parsed === null) return
    const next = Number(parsed) === Number(auto) ? null : parsed
    if (next === manual || (next !== null && manual !== null && Number(next) === Number(manual))) return
    timer.current = setTimeout(() => onChange(next), delay)
  }

  return (
    <section className="rounded-xl border border-line bg-card p-4" data-testid="manual-total">
      <h2 className="text-sm font-semibold">
        Сумма от Ozon <span className="font-normal text-muted">— можно изменить, если на счёт пришло другое</span>
      </h2>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input
          value={text}
          inputMode="decimal"
          aria-label="Сумма от Ozon"
          data-testid="manual-input"
          onFocus={() => setEditing(true)}
          onBlur={() => {
            setEditing(false)
            commit(text, 0)
          }}
          onChange={(e) => {
            setText(e.target.value)
            commit(e.target.value, 700)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit(text, 0)
            if (e.key === 'Escape') {
              setText(plain(manual ?? auto))
              setInvalid(false)
            }
          }}
          className={`w-full max-w-[14rem] rounded-lg border bg-bg px-3 py-2 text-lg font-bold tabular-nums text-ok outline-none focus:border-accent ${
            invalid ? 'border-bad' : manual !== null ? 'border-accent' : 'border-line'
          }`}
        />
        <span className="text-sm text-muted">₽</span>
        {manual !== null && (
          <button
            type="button"
            onClick={() => {
              clearTimeout(timer.current)
              setInvalid(false)
              setText(plain(auto))
              onChange(null)
            }}
            className="rounded-md border border-line px-3 py-1.5 text-xs text-muted hover:border-accent hover:text-fg"
            data-testid="manual-reset"
          >
            ↩ Авто
          </button>
        )}
        {status && <span className="text-xs text-faint" data-testid="manual-status">{status}</span>}
      </div>
      <p className={`mt-1 text-xs ${invalid ? 'text-bad' : 'text-muted'}`} data-testid="manual-hint">
        {invalid
          ? 'Введите сумму числом, например 12 000,50'
          : manual !== null
            ? `Введено вручную; по начислениям ${rub(auto)}. ${note}`
            : `По начислениям Ozon. ${note}`}
      </p>
    </section>
  )
}

// ---------- параметры расчёта ----------

/** Свои параметры расчёта; null — как в налоговом профиле. */
export type CalcParams = {
  vat_multiplier: string | null // коэффициент: 1.22 = 22 %
  tax_rate: string | null // доля: 0.06 = 6 %
  tax_base: 'profit' | 'revenue' | null
}

export const PROFILE_PARAMS: CalcParams = { vat_multiplier: null, tax_rate: null, tax_base: null }

const VAT_OPTIONS = [
  { value: '1.22', label: '22 %' },
  { value: '1.20', label: '20 %' },
  { value: '1.10', label: '10 %' },
  { value: '1.07', label: '7 %' },
  { value: '1.05', label: '5 %' },
  { value: '1.00', label: 'без НДС' },
]

const field = 'rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-accent'

/** «0.0600» → «6»; null → «». */
const ratePct = (rate: string | null) => (rate === null ? '' : String(Math.round(Number(rate) * 10000) / 100).replace('.', ','))

/** Параметры: НДС поставщика и налог. Начальные — из налогового профиля; изменения пересчитывают сразу. */
export function ParamsPanel({
  pnl,
  value,
  onChange,
  note,
}: {
  pnl: PnL
  value: CalcParams
  onChange: (value: CalcParams) => void
  note: string
}) {
  const custom = value.tax_rate !== null
  const [rateText, setRateText] = useState(ratePct(value.tax_rate))
  const [rateInvalid, setRateInvalid] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout>>()
  useEffect(() => setRateText(ratePct(value.tax_rate)), [value.tax_rate])
  useEffect(() => () => clearTimeout(timer.current), [])
  const overridden = value.vat_multiplier !== null || custom

  function setRate(text: string) {
    setRateText(text)
    clearTimeout(timer.current)
    const n = Number(text.replace(',', '.').replace('%', '').trim())
    const ok = text.trim() !== '' && Number.isFinite(n) && n >= 0 && n < 100
    setRateInvalid(!ok)
    if (ok) timer.current = setTimeout(() => onChange({ ...value, tax_rate: (n / 100).toFixed(4), tax_base: value.tax_base ?? 'profit' }), 500)
  }

  const summary = `НДС поставщика по умолчанию ${vatPctText(pnl.supplier_vat)} · ${pnl.tax_title ? `налог: ${pnl.tax_title}` : 'налог не задан'}`
  return (
    <details className="group rounded-xl border border-line bg-card" data-testid="params">
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3">
        <span className="text-sm font-semibold">
          <span className="inline-block transition-transform group-open:rotate-90">▸</span> Параметры расчёта
        </span>
        <span className="text-xs text-muted" data-testid="params-summary">
          {summary}
          {overridden && <span className="text-accent"> · свои параметры</span>}
        </span>
      </summary>
      <div className="space-y-4 border-t border-line px-4 py-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm">
            <span className="mb-1 block text-xs text-muted">НДС поставщика (в цене закупки)</span>
            <select
              value={value.vat_multiplier === null ? '' : Number(value.vat_multiplier).toFixed(2)}
              onChange={(e) => onChange({ ...value, vat_multiplier: e.target.value || null })}
              className={`${field} w-full`}
              data-testid="param-vat"
            >
              <option value="">как в налоговом профиле</option>
              {VAT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <span className="mt-1 block text-xs text-faint">
              Себестоимость × (1 + НДС). У артикулов со своей ставкой в справочнике — их ставка.
            </span>
          </label>
          <div className="text-sm">
            <span className="mb-1 block text-xs text-muted">Налог</span>
            <select
              value={custom ? 'custom' : 'profile'}
              onChange={(e) =>
                onChange(
                  e.target.value === 'custom'
                    ? { ...value, tax_rate: '0.0600', tax_base: 'revenue' }
                    : { ...value, tax_rate: null, tax_base: null },
                )
              }
              className={`${field} w-full`}
              data-testid="param-tax-mode"
            >
              <option value="profile">по налоговому профилю</option>
              <option value="custom">свой процент</option>
            </select>
            {custom && (
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <input
                  value={rateText}
                  onChange={(e) => setRate(e.target.value)}
                  inputMode="decimal"
                  aria-label="Свой процент налога"
                  data-testid="param-tax-rate"
                  className={`${field} w-20 tabular-nums ${rateInvalid ? 'border-bad' : ''}`}
                />
                <span className="text-muted">%</span>
                <select
                  value={value.tax_base ?? 'profit'}
                  onChange={(e) => onChange({ ...value, tax_base: e.target.value as 'profit' | 'revenue' })}
                  className={field}
                  data-testid="param-tax-base"
                >
                  <option value="profit">от прибыли</option>
                  <option value="revenue">от дохода</option>
                </select>
              </div>
            )}
            {rateInvalid && <span className="mt-1 block text-xs text-bad">Процент — число от 0 до 99,99</span>}
          </div>
        </div>

        <div className="rounded-lg bg-bg/60 p-3 text-sm" data-testid="tax-explain">
          {pnl.tax_title ? (
            <>
              <div>
                Налог считается: <span className="font-medium">{pnl.tax_title}</span>
              </div>
              {pnl.tax_lines.length > 0 && (
                <ul className="mt-2 space-y-1 text-muted" data-testid="tax-lines">
                  {pnl.tax_lines.map((line, i) => (
                    <li key={i}>{line}</li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            <div>
              <span className="text-warn">Налог не задан</span> — остаток и маржа не считаются.{' '}
              <Link href="/dashboard/settings/taxes" className="text-accent hover:underline">
                Указать в «Налоги и НДС» →
              </Link>
            </div>
          )}
          {!custom && (
            <p className="mt-2 text-xs text-faint">
              Начальные значения — из налогового профиля на дату начала периода.{' '}
              <Link href="/dashboard/settings/taxes" className="text-accent hover:underline">
                Изменить профиль
              </Link>
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
          <span>{note}</span>
          {overridden && (
            <button
              type="button"
              onClick={() => onChange(PROFILE_PARAMS)}
              className="ml-auto rounded-md border border-line px-3 py-1.5 hover:border-accent hover:text-fg"
              data-testid="params-reset"
            >
              ↩ Как в профиле
            </button>
          )}
        </div>
      </div>
    </details>
  )
}

// ---------- график по дням ----------

/** Цвета темы для SVG: значения CSS-переменных (меняются вместе с темой). */
export function useTokenColors() {
  const { theme } = useTheme()
  const [colors, setColors] = useState<Record<string, string> | null>(null)
  useEffect(() => {
    const css = getComputedStyle(document.documentElement)
    const c = (name: string) => `rgb(${css.getPropertyValue(`--${name}`).trim().split(/\s+/).join(',')})`
    setColors({
      accent: c('accent'), ok: c('ok'), bad: c('bad'), warn: c('warn'), muted: c('muted'), faint: c('faint'),
      line: c('line'), card: c('card'), fg: c('fg'),
    })
  }, [theme])
  return colors
}

export const compact = (v: number) =>
  Math.abs(v) >= 1000 ? `${(v / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} тыс.` : v.toLocaleString('ru-RU')

type ChartRow = { date: string; label: string; revenue: number | null; profit: number | null; day: DayPnL }

function ChartTooltip({ active, payload, drill }: { active?: boolean; payload?: { payload: ChartRow }[]; drill: Drill<ChartRow> }) {
  const costLabel = useCostLabel()
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  const { day } = row
  return (
    <TipBox href={drill.hrefFor(row)} touch={drill.touch}>
      <div className="mb-1 font-semibold">{fmtDay(day.date)}</div>
      {day.source === null ? (
        <div className="text-muted">нет данных</div>
      ) : (
        <div className="space-y-0.5 tabular-nums">
          <div>Выручка: {rub(day.revenue)}</div>
          <div>Начислено Ozon: {rub(day.total)}</div>
          <div>
            {costLabel}: {rub(day.owed)}
          </div>
          <div className={Number(day.profit_before_tax) < 0 ? 'text-bad' : 'text-ok'}>Прибыль до налога: {rub(day.profit_before_tax)}</div>
          <div className="text-faint">{day.source === 'xlsx' ? 'из отчёта' : 'из Ozon API'}</div>
        </div>
      )}
    </TipBox>
  )
}

/** Выручка и прибыль до налога по дням. Показывается, только если дней с данными хотя бы два.
 * День ведёт в «По товарам» за этот день (dayHref), «Подробнее →» — за весь период (moreHref). */
export function DayChart({ days, dayHref, moreHref }: { days: DayPnL[]; dayHref?: (day: string) => string; moreHref?: string }) {
  const colors = useTokenColors()
  const drill = useDrill<ChartRow>(dayHref ? (r) => (r.day.source === null ? null : dayHref(r.date)) : undefined)
  const withData = days.filter((d) => d.source !== null)
  if (withData.length < 2 || !colors) return null
  const rows: ChartRow[] = days.map((d) => ({
    date: d.date,
    label: fmtDay(d.date).slice(0, 5),
    revenue: d.revenue === null ? null : Number(d.revenue),
    profit: d.profit_before_tax === null ? null : Number(d.profit_before_tax),
    day: d,
  }))
  const missing = days.length - withData.length
  return (
    <section className="rounded-xl border border-line bg-card p-4" data-testid="day-chart">
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1">
        <h2 className="text-sm font-semibold">По дням</h2>
        <span className="flex items-center gap-1.5 text-xs text-muted">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-accent/40" /> выручка
        </span>
        <span className="flex items-center gap-1.5 text-xs text-muted">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-ok" /> прибыль до налога
        </span>
        {missing > 0 && <span className="text-xs text-faint">без данных — {daysWord(missing)} (пропуски, а не ноль)</span>}
        <MoreLink href={moreHref} />
      </div>
      <div className={`h-60 ${drill.boxProps.className}`} onPointerDown={drill.boxProps.onPointerDown}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: 0 }} onClick={drill.onClick}>
            <CartesianGrid vertical={false} stroke={colors.line} />
            <XAxis dataKey="label" tick={{ fill: colors.muted, fontSize: 11 }} tickLine={false} axisLine={{ stroke: colors.line }} minTickGap={8} />
            <YAxis tick={{ fill: colors.muted, fontSize: 11 }} tickFormatter={compact} tickLine={false} axisLine={false} width={64} />
            <ReferenceLine y={0} stroke={colors.muted} />
            <Tooltip content={<ChartTooltip drill={drill} />} cursor={{ fill: colors.line, opacity: 0.4 }} {...drill.tooltipProps} />
            <Bar dataKey="revenue" fill={colors.accent} fillOpacity={0.35} radius={[3, 3, 0, 0]} isAnimationActive={false} />
            <Bar dataKey="profit" radius={[3, 3, 0, 0]} isAnimationActive={false}>
              {rows.map((r) => (
                <Cell key={r.date} fill={(r.profit ?? 0) < 0 ? colors.bad : colors.ok} />
              ))}
            </Bar>
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}


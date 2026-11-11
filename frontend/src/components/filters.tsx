'use client'
// Фильтры таблиц и графиков (правка владельца 11.11.2026): снаружи — только поиск (артикул, название, штрихкод) и период
// одной кнопкой; всё остальное — за кнопкой «Фильтры» справа, в выпадающей панели со счётчиком выбранного и «Сбросить».
// Период выбирается в одном окне: готовые периоды слева, календарь справа — первое нажатие начало, второе конец.
import { Calendar as CalendarIcon, ChevronLeft, ChevronRight, Search, SlidersHorizontal, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { useDismiss } from '@/hooks/useDismiss'
import { type Range, addDays, parseDay, todayMsk } from '@/lib/period'

const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']
const MONTHS_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']

/** «2026-11-05» → «05.11.2026»; short — без года. */
export function dayLabel(day: string, short = false): string {
  const [y, m, d] = day.split('-')
  return short ? `${d}.${m}` : `${d}.${m}.${y}`
}

/** Период словами: «01.11 – 11.11.2026», «05.11.2026», «01.12.2025 – 11.11.2026». */
export function rangeLabel(r: Range): string {
  if (r.from === r.to) return dayLabel(r.from)
  return `${dayLabel(r.from, r.from.slice(0, 4) === r.to.slice(0, 4))} – ${dayLabel(r.to)}`
}

export type RangePreset = { value: string; label: string; range: Range }

type Month = { y: number; m: number } // m — 0..11

const monthOf = (day: string): Month => ({ y: Number(day.slice(0, 4)), m: Number(day.slice(5, 7)) - 1 })
const shift = (mo: Month, n: number): Month => {
  const t = mo.y * 12 + mo.m + n
  return { y: Math.floor(t / 12), m: ((t % 12) + 12) % 12 }
}
const pad = (n: number) => String(n).padStart(2, '0')
const dayOf = (mo: Month, d: number) => `${mo.y}-${pad(mo.m + 1)}-${pad(d)}`

/** Дни месяца сеткой с понедельника; null — пустые клетки до первого числа. */
function monthGrid(mo: Month): (string | null)[] {
  const first = parseDay(dayOf(mo, 1))
  const lead = (first.getUTCDay() + 6) % 7
  const days = new Date(Date.UTC(mo.y, mo.m + 1, 0)).getUTCDate()
  return [...Array<null>(lead).fill(null), ...Array.from({ length: days }, (_, i) => dayOf(mo, i + 1))]
}

/**
 * Период одной кнопкой: окно с готовыми периодами и календарём. Готовый период — сразу; свой — два нажатия на
 * календаре (начало и конец, порядок не важен). Будущие дни (после max) недоступны.
 */
export function DateRangePicker({
  value,
  preset,
  presets = [],
  onChange,
  min,
  max = todayMsk(),
  maxDays,
  testid = 'period',
  className = '',
}: {
  value: Range
  /** Выбранный готовый период (его value) или null — свой; не задан — узнаётся по датам. */
  preset?: string | null
  presets?: RangePreset[]
  onChange: (next: { range: Range; preset: string | null }) => void
  min?: string
  max?: string
  /** Не длиннее — иначе подсказка и выбор не применяется. */
  maxDays?: number
  testid?: string
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const close = useCallback(() => setOpen(false), [])
  useDismiss(open, close, box)
  const [start, setStart] = useState<string | null>(null) // выбрано начало, ждём конец
  const [hover, setHover] = useState<string | null>(null)
  const [view, setView] = useState<Month>(() => shift(monthOf(value.to), -1)) // левый из двух месяцев
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setStart(null)
    setHover(null)
    setError(null)
    setView(shift(monthOf(value.to), -1))
  }, [open, value.to])

  const current = preset !== undefined ? preset : (presets.find((p) => p.range.from === value.from && p.range.to === value.to)?.value ?? null)
  const active = presets.find((p) => p.value === current)
  const pick = (day: string) => {
    if (start === null) {
      setStart(day)
      setError(null)
      return
    }
    const range = day < start ? { from: day, to: start } : { from: start, to: day }
    const days = Math.round((parseDay(range.to).getTime() - parseDay(range.from).getTime()) / 86_400_000) + 1
    if (maxDays && days > maxDays) {
      setError(`Не длиннее ${maxDays} дней — выберите конец ближе`)
      return
    }
    onChange({ range, preset: null })
    setOpen(false)
  }
  const lo = start && hover ? (hover < start ? hover : start) : start ?? value.from
  const hi = start && hover ? (hover < start ? start : hover) : start ?? value.to

  return (
    <div ref={box} className={`relative ${className}`} data-testid={testid}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={`flex h-9 items-center gap-2 rounded-lg border bg-card px-3 text-sm ${open ? 'border-accent' : 'border-line hover:border-accent'}`}
        data-testid={`${testid}-button`}
      >
        <CalendarIcon className="h-4 w-4 shrink-0 text-muted" aria-hidden />
        {active && <span className="font-medium">{active.label}</span>}
        <span className={active ? 'hidden text-muted sm:inline' : 'font-medium'} data-testid={`${testid}-label`}>
          {rangeLabel(value)}
        </span>
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Период"
          className="z-40 rounded-xl border border-line bg-card p-3 shadow-xl shadow-black/20 max-sm:fixed max-sm:inset-x-3 max-sm:top-20 max-sm:max-h-[80vh] max-sm:overflow-y-auto sm:absolute sm:left-0 sm:top-full sm:mt-2 sm:w-max"
          data-testid={`${testid}-popover`}
        >
          <div className="flex flex-col gap-3 sm:flex-row">
            {presets.length > 0 && (
              <div className="flex flex-wrap gap-1 sm:w-40 sm:flex-col sm:border-r sm:border-line sm:pr-3">
                {presets.map((p) => (
                  <button
                    key={p.value}
                    type="button"
                    onClick={() => {
                      onChange({ range: p.range, preset: p.value })
                      setOpen(false)
                    }}
                    aria-pressed={p.value === current}
                    className={`rounded-lg px-2.5 py-1.5 text-left text-sm ${p.value === current ? 'bg-accent/15 text-accent' : 'text-muted hover:bg-line/60 hover:text-fg'}`}
                    data-testid={`${testid}-preset-${p.value}`}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            )}
            <div>
              <div className="mb-2 flex items-center justify-between">
                <button type="button" onClick={() => setView((v) => shift(v, -1))} className="rounded-lg p-1 text-muted hover:bg-line/60 hover:text-fg" aria-label="Раньше">
                  <ChevronLeft className="h-4 w-4" aria-hidden />
                </button>
                <span className="text-xs text-muted" data-testid={`${testid}-hint`}>
                  {error ?? (start ? `С ${dayLabel(start)} — теперь выберите конец` : 'Выберите начало и конец периода')}
                </span>
                <button type="button" onClick={() => setView((v) => shift(v, 1))} className="rounded-lg p-1 text-muted hover:bg-line/60 hover:text-fg" aria-label="Позже">
                  <ChevronRight className="h-4 w-4" aria-hidden />
                </button>
              </div>
              <div className="flex gap-4">
                {[view, shift(view, 1)].map((mo, i) => (
                  <div key={`${mo.y}-${mo.m}`} className={i === 0 ? 'hidden sm:block' : ''}>
                    <div className="mb-1 text-center text-sm font-medium">
                      {MONTHS[mo.m]} {mo.y}
                    </div>
                    <div className="grid grid-cols-7 gap-y-0.5 text-center text-xs">
                      {WEEKDAYS.map((w) => (
                        <div key={w} className="py-1 text-faint">
                          {w}
                        </div>
                      ))}
                      {monthGrid(mo).map((day, k) => {
                        if (!day) return <div key={`e${k}`} />
                        const off = (max && day > max) || (min && day < min)
                        const edge = day === lo || day === hi
                        const inside = day > lo && day < hi
                        return (
                          <button
                            key={day}
                            type="button"
                            disabled={!!off}
                            onClick={() => pick(day)}
                            onPointerEnter={() => setHover(day)}
                            aria-label={`${Number(day.slice(8))} ${MONTHS_GEN[mo.m]} ${mo.y}`}
                            aria-pressed={edge}
                            className={`h-8 w-9 text-sm tabular-nums disabled:cursor-not-allowed disabled:text-faint/50 ${
                              edge ? 'rounded-lg bg-accent font-medium text-white' : inside ? 'bg-accent/15 text-fg' : 'rounded-lg hover:bg-line/60'
                            }`}
                            data-day={day}
                          >
                            {Number(day.slice(8))}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/** Поиск снаружи панели фильтров: артикул, название или штрихкод. */
export function SearchBox({
  value,
  onChange,
  placeholder = 'Артикул, название или штрихкод',
  testid = 'search',
  className = '',
}: {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  testid?: string
  className?: string
}) {
  return (
    <label className={`relative flex h-9 min-w-0 items-center ${className}`}>
      <Search className="pointer-events-none absolute left-2.5 h-4 w-4 text-muted" aria-hidden />
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className="h-9 w-full min-w-0 rounded-lg border border-line bg-card pl-8 pr-8 text-sm outline-none placeholder:text-faint focus:border-accent [&::-webkit-search-cancel-button]:hidden"
        data-testid={testid}
      />
      {value && (
        <button type="button" onClick={() => onChange('')} className="absolute right-2 rounded p-0.5 text-muted hover:text-fg" aria-label="Очистить поиск">
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      )}
    </label>
  )
}

/** Кнопка «Фильтры» справа и выпадающая панель: count — сколько фильтров выбрано (не по умолчанию). */
export function FilterButton({
  count = 0,
  onReset,
  children,
  testid = 'filters',
  label = 'Фильтры',
}: {
  count?: number
  onReset?: () => void
  children: React.ReactNode
  testid?: string
  label?: string
}) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const close = useCallback(() => setOpen(false), [])
  useDismiss(open, close, box)
  return (
    <div ref={box} className="relative" data-testid={testid}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={`flex h-9 items-center gap-2 rounded-lg border bg-card px-3 text-sm ${open || count > 0 ? 'border-accent text-accent' : 'border-line hover:border-accent'}`}
        data-testid={`${testid}-button`}
      >
        <SlidersHorizontal className="h-4 w-4" aria-hidden />
        {label}
        {count > 0 && (
          <span className="rounded-full bg-accent px-1.5 text-xs font-medium leading-5 text-white" data-testid={`${testid}-count`}>
            {count}
          </span>
        )}
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={label}
          className="z-40 rounded-xl border border-line bg-card shadow-xl shadow-black/20 max-sm:fixed max-sm:inset-x-3 max-sm:top-20 sm:absolute sm:right-0 sm:top-full sm:mt-2 sm:w-80"
          data-testid={`${testid}-panel`}
        >
          <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
            <span className="text-sm font-semibold">{label}</span>
            <button type="button" onClick={close} className="rounded p-1 text-muted hover:text-fg" aria-label="Закрыть">
              <X className="h-4 w-4" aria-hidden />
            </button>
          </div>
          <div className="max-h-[60vh] space-y-4 overflow-y-auto px-4 py-3">{children}</div>
          <div className="flex items-center justify-between gap-2 border-t border-line px-4 py-2.5">
            {onReset ? (
              <button
                type="button"
                onClick={onReset}
                disabled={count === 0}
                className="text-sm text-muted hover:text-fg disabled:opacity-40"
                data-testid={`${testid}-reset`}
              >
                Сбросить
              </button>
            ) : (
              <span />
            )}
            <button type="button" onClick={close} className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90" data-testid={`${testid}-done`}>
              Готово
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

/** Группа в панели фильтров: подпись и элементы. */
export function FilterField({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="text-xs font-medium uppercase tracking-wider text-muted" title={hint}>
        {label}
      </div>
      {children}
    </div>
  )
}

/** Выбор одного варианта кнопками (в панели фильтров). */
export function ChipOptions<T extends string>({
  options,
  value,
  onChange,
  testid,
}: {
  options: { value: T; label: string; count?: number; hint?: string }[]
  value: T
  onChange: (value: T) => void
  testid?: string
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          aria-pressed={value === o.value}
          title={o.hint}
          className={`rounded-lg border px-2.5 py-1 text-sm ${value === o.value ? 'border-accent bg-accent/10 text-accent' : 'border-line text-muted hover:text-fg'}`}
          data-testid={testid ? `${testid}-${o.value}` : undefined}
        >
          {o.label}
          {o.count !== undefined && <span className="ml-1 text-faint">{o.count}</span>}
        </button>
      ))}
    </div>
  )
}

/** Флажок в панели фильтров. */
export function CheckOption({ checked, onChange, label, hint, testid }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string; testid?: string }) {
  return (
    <label className="flex cursor-pointer items-start gap-2 text-sm" title={hint}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="mt-0.5 accent-accent" data-testid={testid} />
      <span>{label}</span>
    </label>
  )
}

/** Строка фильтров: слева поиск и период, справа «Фильтры» и действия (выгрузка, колонки). */
export function FilterBar({ children, end, testid = 'filter-bar' }: { children?: React.ReactNode; end?: React.ReactNode; testid?: string }) {
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid={testid}>
      {children}
      {end && <div className="ml-auto flex flex-wrap items-center gap-2">{end}</div>}
    </div>
  )
}

/** Готовые периоды для DateRangePicker: «Эта неделя», «7 дней», … — диапазоны на сегодня. */
export function lastDays(n: number, today = todayMsk()): Range {
  return { from: addDays(today, -(n - 1)), to: today }
}

export const selectClass = 'h-9 w-full rounded-lg border border-line bg-bg px-2.5 text-sm outline-none focus:border-accent'

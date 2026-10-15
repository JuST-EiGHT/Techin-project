// Периоды для P&L (шаг 3.3). Даты — строки «ГГГГ-ММ-ДД» по Москве, как на бэкенде (начисления Ozon — по МСК).

export type Preset = 'week' | 'month' | 'prev_month' | 'custom'

export const PRESETS: { value: Preset; label: string }[] = [
  { value: 'week', label: 'Эта неделя' },
  { value: 'month', label: 'Этот месяц' },
  { value: 'prev_month', label: 'Прошлый месяц' },
  { value: 'custom', label: 'Свой период' },
]

export type Range = { from: string; to: string }

const iso = (d: Date) => d.toISOString().slice(0, 10)

/** Сегодня по Москве. */
export function todayMsk(now = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Moscow' }).format(now)
}

/** «ГГГГ-ММ-ДД» → дата в UTC (без сдвига часового пояса). */
export const parseDay = (day: string) => new Date(`${day}T00:00:00Z`)

export function addDays(day: string, n: number): string {
  const d = parseDay(day)
  d.setUTCDate(d.getUTCDate() + n)
  return iso(d)
}

export function presetRange(preset: Exclude<Preset, 'custom'>, today = todayMsk()): Range {
  const d = parseDay(today)
  if (preset === 'week') return { from: addDays(today, -((d.getUTCDay() + 6) % 7)), to: today } // с понедельника
  if (preset === 'month') return { from: `${today.slice(0, 7)}-01`, to: today }
  const firstThis = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1))
  const lastPrev = new Date(firstThis.getTime() - 86_400_000)
  return { from: iso(new Date(Date.UTC(lastPrev.getUTCFullYear(), lastPrev.getUTCMonth(), 1))), to: iso(lastPrev) }
}

export const daysCount = (r: Range) => Math.round((parseDay(r.to).getTime() - parseDay(r.from).getTime()) / 86_400_000) + 1

/** Дней в словах: 1 день, 2 дня, 5 дней. */
export function daysWord(n: number): string {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return `${n} день`
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return `${n} дня`
  return `${n} дней`
}

const MONTHS = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']

/** «2026-09» → «Сентябрь 2026». */
export function monthTitle(month: string): string {
  const [y, m] = month.split('-').map(Number)
  const name = MONTHS[m - 1]
  return `${name[0].toUpperCase()}${name.slice(1)} ${y}`
}

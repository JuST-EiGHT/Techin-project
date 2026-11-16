'use client'

// Сортировка в таблицах — одинаково во всех (правка владельца 16.11.2026, как в кабинете Ozon): нажатие на заголовок
// колонки открывает меню «Сначала больше / Сначала меньше» (у текста — «От А до Я / От Я до А», у дат — «Сначала новые
// / Сначала старые»); отсортированная колонка — на мягком фоне с полосой акцента снизу и стрелкой у названия.
// - `useSort` — сортировка строк на странице: значение колонки берёт функция; нет данных — всегда в конце, а не как 0.
// - `SortTh` — заголовок колонки. Таблицам, которые сортирует сервер (страницы), — те же заголовки: `active` и `onSort`
//   задаёт страница.
import { ArrowDown, ArrowDownAZ, ArrowDownNarrowWide, ArrowDownWideNarrow, ArrowDownZA, ArrowUp, ChevronDown, X } from 'lucide-react'
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { useExclusiveMenu } from '@/hooks/useDismiss'

export type SortDir = 'asc' | 'desc'
export type SortState<K extends string = string> = { key: K; dir: SortDir } | null
export type SortKind = 'number' | 'text' | 'date'
/** Значение ячейки для сортировки: число, текст (даты — строкой ISO). null / undefined / '' — нет данных. */
export type SortValue = number | string | null | undefined

/** Число из ответа API (деньги приходят строкой «1234.50»); пусто или не число — null, а не 0. */
export function num(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isFinite(n) ? n : null
}

const collator = typeof Intl !== 'undefined' ? new Intl.Collator('ru', { numeric: true, sensitivity: 'base' }) : null

function empty(v: SortValue): boolean {
  return v === null || v === undefined || v === '' || (typeof v === 'number' && Number.isNaN(v))
}

function compare(a: SortValue, b: SortValue): number {
  if (typeof a === 'number' && typeof b === 'number') return a - b
  const x = String(a)
  const y = String(b)
  return collator ? collator.compare(x, y) : x < y ? -1 : x > y ? 1 : 0
}

/** Отсортировать копию строк. Равные — в прежнем порядке; без значения — в конце при любом направлении. */
export function sortRows<T>(rows: readonly T[], dir: SortDir, value: (row: T) => SortValue): T[] {
  const withValue: { row: T; v: SortValue; i: number }[] = []
  const without: T[] = []
  rows.forEach((row, i) => {
    const v = value(row)
    if (empty(v)) without.push(row)
    else withValue.push({ row, v, i })
  })
  withValue.sort((a, b) => {
    const c = compare(a.v, b.v)
    return c === 0 ? a.i - b.i : dir === 'desc' ? -c : c
  })
  return [...withValue.map((x) => x.row), ...without]
}

/**
 * Сортировка строк таблицы на странице. `by` — как достать значение каждой сортируемой колонки:
 * `const s = useSort(items, { revenue: (r) => num(r.revenue), name: (r) => r.name })` →
 * `<SortTh {...s.th('revenue')}>Выручка</SortTh>` и `s.rows.map(…)`. Без выбранной колонки — порядок как пришёл.
 */
export function useSort<T, K extends string>(rows: readonly T[] | null | undefined, by: Record<K, (row: T) => SortValue>, initial: SortState<K> = null) {
  const [sort, setSort] = useState<SortState<K>>(initial)
  const sorted = useMemo(() => {
    const list = rows ?? []
    if (!sort || !by[sort.key]) return [...list]
    return sortRows(list, sort.dir, by[sort.key])
  }, [rows, sort, by])
  const th = useCallback(
    (key: K) => ({
      active: sort?.key === key ? sort.dir : null,
      onSort: (dir: SortDir | null) => setSort(dir ? { key, dir } : null),
    }),
    [sort],
  )
  return { rows: sorted, sort, setSort, th }
}

const OPTIONS: Record<SortKind, { dir: SortDir; label: string; icon: typeof ArrowDown }[]> = {
  number: [
    { dir: 'desc', label: 'Сначала больше', icon: ArrowDownWideNarrow },
    { dir: 'asc', label: 'Сначала меньше', icon: ArrowDownNarrowWide },
  ],
  text: [
    { dir: 'asc', label: 'От А до Я', icon: ArrowDownAZ },
    { dir: 'desc', label: 'От Я до А', icon: ArrowDownZA },
  ],
  date: [
    { dir: 'desc', label: 'Сначала новые', icon: ArrowDownWideNarrow },
    { dir: 'asc', label: 'Сначала старые', icon: ArrowDownNarrowWide },
  ],
}

type MenuPos = { top: number; left: number }

/**
 * Заголовок сортируемой колонки. `active` — направление, если таблица отсортирована по этой колонке; `onSort(null)` —
 * снять сортировку (вернуть порядок по умолчанию), если `resettable`.
 */
export function SortTh({
  children,
  sub,
  active,
  onSort,
  kind = 'number',
  align = 'left',
  className = 'px-3 py-2',
  title,
  resettable = true,
  colSpan,
  rowSpan,
  as = 'th',
  attrs,
  testid,
}: {
  children: React.ReactNode
  /** Серая вторая строка заголовка: «За 28 дней», «₽». */
  sub?: React.ReactNode
  active: SortDir | null
  onSort: (dir: SortDir | null) => void
  kind?: SortKind
  align?: 'left' | 'right' | 'center'
  className?: string
  title?: string
  resettable?: boolean
  colSpan?: number
  rowSpan?: number
  /** `div` — заголовок колонки списка без `<table>` (строки — сеткой, как журнал операций) */
  as?: 'th' | 'div'
  /** свои атрибуты ячейки заголовка: `data-current`, `data-kind` … */
  attrs?: Record<string, string | boolean | undefined>
  testid?: string
}) {
  const [pos, setPos] = useState<MenuPos | null>(null)
  const button = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const id = useId()
  const open = pos !== null
  const close = useCallback(() => setPos(null), [])
  useExclusiveMenu(open, close, id)

  function place() {
    const r = button.current?.getBoundingClientRect()
    if (!r) return
    const width = 208
    const left = Math.min(Math.max(8, align === 'right' ? r.right - width : r.left), window.innerWidth - width - 8)
    setPos({ top: r.bottom + 6, left })
  }

  // меню рисуется поверх страницы (у таблицы прокрутка вбок — внутри неё оно обрезалось бы); закрывается кликом мимо,
  // Esc, прокруткой и сменой размера окна
  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node
      if (!menu.current?.contains(t) && !button.current?.contains(t)) close()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        close()
        button.current?.focus()
      }
    }
    const onScroll = (e: Event) => {
      if (!menu.current?.contains(e.target as Node)) close()
    }
    document.addEventListener('pointerdown', onDown)
    document.addEventListener('keydown', onKey)
    document.addEventListener('scroll', onScroll, { capture: true, passive: true })
    window.addEventListener('resize', close)
    return () => {
      document.removeEventListener('pointerdown', onDown)
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('scroll', onScroll, { capture: true })
      window.removeEventListener('resize', close)
    }
  }, [open, close])

  useLayoutEffect(() => {
    if (!open) return
    const items = menu.current
    ;(items?.querySelector<HTMLButtonElement>('[aria-checked="true"]') ?? items?.querySelector<HTMLButtonElement>('[role="menuitemradio"]'))?.focus()
  }, [open])

  function pick(dir: SortDir | null) {
    onSort(dir)
    close()
    button.current?.focus()
  }

  const justify = align === 'right' ? 'justify-end text-right' : align === 'center' ? 'justify-center text-center' : 'justify-start text-left'
  const Arrow = active === 'desc' ? ArrowDown : ArrowUp
  const key = testid ?? 'sort'
  const Cell = as
  return (
    <Cell
      className={`${className} ${active ? 'bg-soft shadow-[inset_0_-2px_0_rgb(var(--accent))]' : ''}`}
      aria-sort={active ? (active === 'desc' ? 'descending' : 'ascending') : undefined}
      role={as === 'div' ? 'columnheader' : undefined}
      title={title}
      colSpan={as === 'th' ? colSpan : undefined}
      rowSpan={as === 'th' ? rowSpan : undefined}
      {...attrs}
    >
      <button
        ref={button}
        type="button"
        onClick={() => (open ? close() : place())}
        aria-haspopup="menu"
        aria-expanded={open}
        className={`group inline-flex w-full items-start gap-1 font-[inherit] hover:text-fg ${justify} ${active ? 'text-fg' : ''}`}
        data-testid={key}
        data-sort={active ?? undefined}
      >
        <span className="min-w-0">
          <span className="block">{children}</span>
          {sub && <span className="block font-normal text-faint">{sub}</span>}
        </span>
        {active && <Arrow className="mt-px h-3.5 w-3.5 shrink-0 text-accent" aria-label={active === 'desc' ? 'по убыванию' : 'по возрастанию'} />}
        <ChevronDown className={`mt-px h-3.5 w-3.5 shrink-0 text-faint transition-transform group-hover:text-muted ${open ? 'rotate-180' : ''}`} aria-hidden />
      </button>
      {open &&
        createPortal(
          <div
            ref={menu}
            role="menu"
            aria-label="Сортировка"
            className="fixed z-[60] w-52 rounded-xl border border-line bg-card p-1 text-sm font-normal text-fg shadow-pop"
            style={{ top: pos.top, left: pos.left }}
            data-testid={`${key}-menu`}
          >
            {OPTIONS[kind].map((o) => {
              const I = o.icon
              const on = active === o.dir
              return (
                <button
                  key={o.dir}
                  type="button"
                  role="menuitemradio"
                  aria-checked={on}
                  onClick={() => pick(o.dir)}
                  className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left hover:bg-soft ${on ? 'text-accent' : ''}`}
                  data-testid={`${key}-${o.dir}`}
                >
                  <I className="h-4 w-4 shrink-0" aria-hidden />
                  {o.label}
                </button>
              )
            })}
            {active && resettable && (
              <button
                type="button"
                role="menuitem"
                onClick={() => pick(null)}
                className="mt-1 flex w-full items-center gap-2 rounded-lg border-t border-line px-3 py-2 text-left text-muted hover:bg-soft hover:text-fg"
                data-testid={`${key}-reset`}
              >
                <X className="h-4 w-4 shrink-0" aria-hidden />
                Без сортировки
              </button>
            )}
          </div>,
          document.body,
        )}
    </Cell>
  )
}

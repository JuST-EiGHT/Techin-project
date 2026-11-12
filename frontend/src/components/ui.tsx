'use client'

// Дизайн-система (шаг 10.27a): общие элементы интерфейса. Правила — docs/DESIGN.md, витрина — /design.
// Разделы при переделке берут элементы отсюда, а не собирают свои: так новый вид приходит на все страницы сразу.
import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'

type Icon = React.ComponentType<{ className?: string }>
export type Tone = 'accent' | 'ok' | 'bad' | 'warn' | 'muted'

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(' ')

// ---------- страница ----------

/** Заголовок страницы: название, одна строка «что здесь», действия справа (на телефоне — под заголовком). */
export function PageHeader({ title, description, actions, children, titleTestid }: {
  title: string
  titleTestid?: string
  description?: React.ReactNode
  actions?: React.ReactNode
  /** Под заголовком: вкладки раздела, полоса фильтров. */
  children?: React.ReactNode
}) {
  return (
    <header className="mb-5 space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight" data-testid={titleTestid}>
            {title}
          </h1>
          {description && <p className="mt-1 text-sm text-muted">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </header>
  )
}

/** Панель — главный контейнер: заголовок, «что это» и действие справа (кнопка, «Подробнее →»), содержимое.
 *  Панель в панели не кладём: внутри — разделители или фон `bg-soft`. */
export function Panel({ title, description, action, children, className, flush, testid }: {
  title?: React.ReactNode
  description?: React.ReactNode
  action?: React.ReactNode
  children: React.ReactNode
  className?: string
  /** Без внутренних отступов — для таблицы во всю ширину панели. */
  flush?: boolean
  testid?: string
}) {
  const head = (title || action) && (
    <div className={cx('flex items-start justify-between gap-3', flush ? 'px-5 pt-4' : 'mb-4')}>
      <div className="min-w-0">
        {title && <h2 className="text-base font-semibold">{title}</h2>}
        {description && <p className="mt-0.5 text-sm text-muted">{description}</p>}
      </div>
      {action && <div className="flex shrink-0 items-center gap-2 text-sm">{action}</div>}
    </div>
  )
  return (
    <section className={cx('rounded-xl border border-line bg-card shadow-card', flush ? 'overflow-hidden' : 'p-5', className)} data-testid={testid}>
      {head}
      {flush && head ? <div className="mt-3">{children}</div> : children}
    </section>
  )
}

// ---------- цифры ----------

const num = (v: number) => v.toLocaleString('ru-RU', { maximumFractionDigits: 1 })

/** Изменение к прошлому периоду — плашка: ▲ / ▼, число, единица. Цвет — хорошо или плохо, а не вверх или вниз. */
export function Delta({ pct, unit = '%', upIsBad = false, testid = 'delta' }: {
  pct: string | number | null
  unit?: '%' | 'п.п.'
  /** Рост — плохо (расходы, возвраты): ▲ красным, ▼ зелёным. */
  upIsBad?: boolean
  testid?: string
}) {
  if (pct === null) return null
  const v = Number(pct)
  const good = upIsBad ? v < 0 : v > 0
  const tone = v === 0 ? 'bg-soft text-muted' : good ? 'bg-ok/10 text-ok' : 'bg-bad/10 text-bad'
  return (
    <span className={cx('inline-flex items-center gap-0.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium tabular-nums', tone)} data-testid={testid}>
      <span aria-hidden>{v > 0 ? '▲' : v < 0 ? '▼' : '='}</span>
      <span className="sr-only">{v > 0 ? 'рост' : v < 0 ? 'снижение' : 'без изменений'}</span>
      {num(Math.abs(v))}&nbsp;{unit}
    </span>
  )
}

/** Показатель: подпись, значение, изменение и «было …», переход в подробности. Ставятся в ряд — `StatGrid`. */
export function Stat({ label, value, delta, hint, href, icon: I, tone, testid, children }: {
  label: string
  value: string
  /** Обычно `<Delta …/>`. */
  delta?: React.ReactNode
  /** «было 1 200 ₽», «нет данных за период». */
  hint?: React.ReactNode
  href?: string | null
  icon?: Icon
  tone?: 'ok' | 'bad'
  testid?: string
  children?: React.ReactNode
}) {
  const body = (
    <>
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm text-muted">{label}</span>
        {I && <span className="hidden sm:block"><IconBox icon={I} /></span>}
      </div>
      <div className={cx('mt-2 text-xl font-semibold tracking-tight tabular-nums sm:text-2xl', tone === 'ok' && 'text-ok', tone === 'bad' && 'text-bad')}>{value}</div>
      {(delta || hint) && (
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-faint">
          {delta}
          {hint && <span>{hint}</span>}
        </div>
      )}
      {children}
    </>
  )
  const box = 'block rounded-xl border border-line bg-card p-4 shadow-card'
  if (href) {
    return (
      <Link href={href} className={cx(box, 'transition-colors hover:border-accent/50')} data-testid={testid}>
        {body}
      </Link>
    )
  }
  return <div className={box} data-testid={testid}>{body}</div>
}

/** Ряд показателей: 2 в ряд на телефоне, до 4 на широком экране. */
export function StatGrid({ children, testid }: { children: React.ReactNode; testid?: string }) {
  return <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid={testid}>{children}</div>
}

/** Значок в мягком квадрате — у показателя, в пустом состоянии, в списке событий. */
export function IconBox({ icon: I, tone = 'accent' }: { icon: Icon; tone?: Tone }) {
  return (
    <span className={cx('inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg', TONE_SOFT[tone])}>
      <I className="h-4 w-4" />
    </span>
  )
}

// ---------- плашки ----------

const TONE_SOFT: Record<Tone, string> = {
  accent: 'bg-accent/10 text-accent',
  ok: 'bg-ok/10 text-ok',
  bad: 'bg-bad/10 text-bad',
  warn: 'bg-warn/10 text-warn',
  muted: 'bg-soft text-muted',
}

/** Статус или метка: «Оплачен», «Просрочен», «СРОЧНО», «Ozon API». С точкой — статус процесса. */
export function Badge({ tone, children, title, dot }: { tone: Tone; children: React.ReactNode; title?: string; dot?: boolean }) {
  return (
    <span title={title} className={cx('inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium', TONE_SOFT[tone])}>
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />}
      {children}
    </span>
  )
}

// ---------- кнопки и переключатели ----------

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

/** Классы кнопки — для `<button>`, `<Link>` и `<a>` одинаково. */
export function buttonClass(variant: ButtonVariant = 'secondary', size: 'sm' | 'md' = 'md') {
  return cx(
    'inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50',
    size === 'sm' ? 'h-8 px-3 text-xs' : 'h-9 px-4 text-sm',
    variant === 'primary' && 'bg-accent text-on-accent hover:bg-accent/90',
    variant === 'secondary' && 'border border-line bg-card text-fg hover:bg-soft',
    variant === 'ghost' && 'text-muted hover:bg-soft hover:text-fg',
    variant === 'danger' && 'bg-bad text-on-accent hover:bg-bad/90',
  )
}

export function Button({ variant = 'secondary', size = 'md', className, ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  size?: 'sm' | 'md'
}) {
  return <button type="button" className={cx(buttonClass(variant, size), className)} {...rest} />
}

/** Переключатель вида: «День / Неделя / Месяц», «По товарам / По складам», показатель графика. Не фильтр — на виду. */
export function Segmented<T extends string>({ value, onChange, options, size = 'md', testid, label, optionTestid }: {
  value: T
  onChange: (v: T) => void
  options: { value: T; label: string }[]
  size?: 'sm' | 'md'
  testid?: string
  /** Для экранных читалок: что переключаем. */
  label?: string
  /** data-testid у каждого варианта. */
  optionTestid?: (v: T) => string
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex max-w-full overflow-x-auto rounded-lg bg-soft p-0.5" data-testid={testid}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          data-testid={optionTestid?.(o.value)}
          className={cx(
            'whitespace-nowrap rounded-md font-medium transition-colors',
            size === 'sm' ? 'px-2.5 py-1 text-xs' : 'px-3 py-1.5 text-sm',
            value === o.value ? 'bg-card text-fg shadow-card' : 'text-muted hover:text-fg',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

// ---------- состояния ----------

/** Пустое состояние: что не так и что сделать. «Нет данных» ≠ 0 — говорим, каких данных нет и откуда их взять. */
export function EmptyState({ icon, title, children, action, testid }: {
  icon?: Icon
  title: string
  children?: React.ReactNode
  action?: React.ReactNode
  testid?: string
}) {
  return (
    <div className="flex flex-col items-center px-4 py-10 text-center" data-testid={testid}>
      {icon && <IconBox icon={icon} tone="muted" />}
      <div className={cx('font-medium', icon && 'mt-3')}>{title}</div>
      {children && <div className="mt-1 max-w-md text-sm text-muted">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

/** Заглушка на время загрузки — той же формы, что содержимое, вместо крутилки. */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cx('animate-pulse rounded-lg bg-soft', className)} aria-hidden />
}

// ---------- плотность как у Ozon (шаг 10.27c) ----------

/** Вкладки раздела со счётчиком, как у Ozon: «Участвуют сейчас 42», «Доступные 6». Подчёркнута выбранная. */
export function Tabs<T extends string>({ value, onChange, items, testid, label, itemTestid }: {
  value: T
  onChange: (v: T) => void
  items: { value: T; label: string; count?: number | null; tone?: 'bad' | 'warn'; title?: string }[]
  testid?: string
  label?: string
  itemTestid?: (v: T) => string
}) {
  return (
    <div role="tablist" aria-label={label} className="flex gap-1 overflow-x-auto border-b border-line" data-testid={testid}>
      {items.map((t) => (
        <button
          key={t.value}
          type="button"
          role="tab"
          aria-selected={value === t.value}
          title={t.title}
          onClick={() => onChange(t.value)}
          className={cx(
            '-mb-px inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2 text-sm transition-colors',
            value === t.value ? 'border-accent font-medium text-fg' : 'border-transparent text-muted hover:text-fg',
          )}
          data-testid={itemTestid?.(t.value)}
        >
          {t.label}
          {t.count !== undefined && t.count !== null && <Count n={t.count} tone={t.tone} active={value === t.value} />}
        </button>
      ))}
    </div>
  )
}

/** Быстрые вкладки-фильтры со счётчиком, как у Ozon: «Все товары», «Срочно поставить 3», «Скоро поставить 1».
 *  Это не «Фильтры»: самые частые срезы списка — на виду (решение владельца 13.11.2026); остальное — за «Фильтрами». */
export function QuickTabs<T extends string>({ value, onChange, items, testid, label, itemTestid }: {
  value: T
  onChange: (v: T) => void
  items: { value: T; label: string; count?: number | null; tone?: 'bad' | 'warn'; title?: string }[]
  testid?: string
  label?: string
  itemTestid?: (v: T) => string
}) {
  return (
    <div role="group" aria-label={label} className="flex max-w-full flex-wrap gap-1.5" data-testid={testid}>
      {items.map((t) => (
        <button
          key={t.value}
          type="button"
          aria-pressed={value === t.value}
          title={t.title}
          onClick={() => onChange(t.value)}
          className={cx(
            'inline-flex h-8 items-center gap-1.5 whitespace-nowrap rounded-lg border px-3 text-sm transition-colors',
            value === t.value ? 'border-accent bg-accent/5 text-fg' : 'border-line bg-card text-muted hover:text-fg',
          )}
          data-testid={itemTestid?.(t.value)}
        >
          {t.label}
          {t.count !== undefined && t.count !== null && <Count n={t.count} tone={t.tone} active={value === t.value} />}
        </button>
      ))}
    </div>
  )
}

/** Счётчик во вкладке: «бад» — красный кружок (срочно), «warn» — жёлтый, иначе — серый. Ноль у тревожных — серый. */
function Count({ n, tone, active }: { n: number; tone?: 'bad' | 'warn'; active: boolean }) {
  const cls =
    tone && n > 0 ? (tone === 'bad' ? 'bg-bad text-on-accent' : 'bg-warn text-on-accent') : active ? 'bg-accent/15 text-accent' : 'bg-soft text-muted'
  return <span className={cx('min-w-[1.25rem] rounded-full px-1.5 text-center text-xs font-medium leading-5 tabular-nums', cls)}>{n.toLocaleString('ru-RU')}</span>
}

/** Подсказка «?» вместо абзаца пояснений: наведение или нажатие — текст в облачке. */
export function HelpTip({ children, label = 'Как считаем', testid }: { children: React.ReactNode; label?: string; testid?: string }) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: PointerEvent) => !box.current?.contains(e.target as Node) && setOpen(false)
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('pointerdown', close)
    window.addEventListener('keydown', esc)
    return () => {
      window.removeEventListener('pointerdown', close)
      window.removeEventListener('keydown', esc)
    }
  }, [open])
  return (
    <span ref={box} className="relative inline-flex align-middle" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-line text-[10px] font-semibold leading-none text-muted hover:border-accent hover:text-accent"
        data-testid={testid}
      >
        ?
      </button>
      {open && (
        <span
          role="tooltip"
          className="absolute left-1/2 top-full z-30 mt-1.5 w-72 max-w-[80vw] -translate-x-1/2 rounded-lg border border-line bg-card p-3 text-left text-xs font-normal normal-case leading-relaxed text-muted shadow-pop"
        >
          {children}
        </span>
      )}
    </span>
  )
}

/** Вторая строка ячейки, как у Ozon: под ценой — «ограничение», под штуками — «−42 %». Серым и мельче. */
export function Sub({ children, tone, testid }: { children: React.ReactNode; tone?: 'ok' | 'bad' | 'warn'; testid?: string }) {
  return (
    <div className={cx('mt-0.5 text-xs', tone === 'ok' ? 'text-ok' : tone === 'bad' ? 'text-bad' : tone === 'warn' ? 'text-warn' : 'text-faint')} data-testid={testid}>
      {children}
    </div>
  )
}

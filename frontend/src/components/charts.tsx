'use client'
// Общее поведение графиков (шаг 3.7a, правило 16): подсказка сразу у курсора; клик по точке — подробный раздел
// за неё, «Подробнее →» у графика — за весь период; на телефоне касание показывает подсказку, переход — кнопкой в ней.
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'

type ChartState = { activePayload?: { payload: unknown }[] } | null | undefined

export type Drill<T> = {
  /** Куда ведёт точка; null — у точки нет подробностей (например, день без данных). */
  hrefFor: (row: T) => string | null
  /** Касание (телефон): клик по графику только показывает подсказку, переход — кнопкой в ней. */
  touch: boolean
  boxProps: { onPointerDown: (e: React.PointerEvent) => void; className: string }
  onClick: (state: ChartState) => void
  tooltipProps: { isAnimationActive: false; wrapperStyle: React.CSSProperties }
}

/** Переход внутри той же страницы — без прокрутки наверх: страница сама покажет выбранное. */
function useGo() {
  const router = useRouter()
  const pathname = usePathname()
  return (href: string) => router.push(href, { scroll: href.split('?')[0] !== pathname })
}

export function useDrill<T>(hrefFor?: (row: T) => string | null): Drill<T> {
  const go = useGo()
  const [touch, setTouch] = useState(false)
  const touchRef = useRef(false)
  useEffect(() => {
    touchRef.current = window.matchMedia('(hover: none)').matches
    setTouch(touchRef.current)
  }, [])
  const href = hrefFor ?? (() => null)
  return {
    hrefFor: href,
    touch,
    boxProps: {
      onPointerDown: (e) => {
        touchRef.current = e.pointerType === 'touch'
        setTouch(touchRef.current)
      },
      className: hrefFor ? 'cursor-pointer' : '',
    },
    onClick: (state) => {
      const row = state?.activePayload?.[0]?.payload as T | undefined
      if (!row || touchRef.current) return // на телефоне касание — только подсказка
      const target = href(row)
      if (target) go(target)
    },
    tooltipProps: {
      isAnimationActive: false, // подсказка сразу у курсора, без «выезда» из угла графика
      wrapperStyle: { pointerEvents: touch ? 'auto' : 'none', zIndex: 10 },
    },
  }
}

const stop = (e: React.SyntheticEvent) => e.stopPropagation()

/** Рамка подсказки: цифры точки и что будет по клику. На телефоне — кнопка «Подробнее →». */
export function TipBox({ href, touch, children }: { href: string | null; touch: boolean; children: React.ReactNode }) {
  const go = useGo()
  return (
    // события подсказки не доходят до графика: иначе касание кнопки сдвинуло бы подсказку на соседнюю точку
    <div
      className="max-w-[16rem] rounded-lg border border-line bg-card px-3 py-2 text-xs shadow-lg"
      onPointerDown={stop}
      onMouseMove={stop}
      onTouchStart={stop}
      onTouchMove={stop}
      onTouchEnd={stop}
      onClick={stop}
      data-testid="chart-tip"
    >
      {children}
      {href &&
        (touch ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              go(href)
            }}
            className="mt-2 w-full rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white"
            data-testid="tip-more"
          >
            Подробнее →
          </button>
        ) : (
          <div className="mt-1.5 text-faint">Нажмите — подробнее</div>
        ))}
    </div>
  )
}

/** «Подробнее →» в заголовке графика — тот же раздел за весь период. */
export function MoreLink({ href, testid = 'chart-more' }: { href?: string | null; testid?: string }) {
  if (!href) return null
  return (
    <Link href={href} className="ml-auto text-xs text-accent hover:underline" data-testid={testid}>
      Подробнее →
    </Link>
  )
}


/**
 * Мини-график без осей и подписей (паттерн «signature chart», Dashboard Design Patterns): тренд с одного взгляда рядом
 * с числом и стрелкой. Пропуски (null) — разрыв линии, а не ноль (правило 12); zero — красная пунктирная линия нуля,
 * если ряд уходит в минус. Точных значений не даёт — их показывает подробный график раздела.
 */
export function Sparkline({
  values,
  tone = 'accent',
  zero = false,
  testid,
}: {
  values: (number | null)[]
  tone?: 'accent' | 'ok' | 'bad' | 'warn' | 'muted'
  zero?: boolean
  testid?: string
}) {
  const known = values.filter((v): v is number => v !== null)
  if (known.length < 2) return null
  let min = Math.min(...known)
  let max = Math.max(...known)
  if (zero) {
    min = Math.min(min, 0)
    max = Math.max(max, 0)
  }
  if (max === min) {
    max += 1
    min -= 1
  }
  const W = 100
  const H = 24
  const x = (i: number) => (values.length === 1 ? 0 : (i / (values.length - 1)) * W)
  const y = (v: number) => H - 2 - ((v - min) / (max - min)) * (H - 4)
  let d = ''
  let open = false
  values.forEach((v, i) => {
    if (v === null) {
      open = false
      return
    }
    d += `${open ? 'L' : 'M'}${x(i).toFixed(2)},${y(v).toFixed(2)} `
    open = true
  })
  const color = { accent: 'text-accent', ok: 'text-ok', bad: 'text-bad', warn: 'text-warn', muted: 'text-faint' }[tone]
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className={`mt-1.5 h-6 w-full ${color}`} aria-hidden data-testid={testid}>
      {zero && min < 0 && (
        <line x1="0" x2={W} y1={y(0)} y2={y(0)} className="text-bad" stroke="currentColor" strokeWidth="1" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
      )}
      <path d={d.trim()} fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

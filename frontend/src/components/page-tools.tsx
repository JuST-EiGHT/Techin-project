'use client'

// Общие помощники страницы (правка владельца 16.11.2026) — стоят один раз в `app/dashboard/layout.tsx` и работают на
// всех страницах сами, страницы о них ничего не знают:
// - «Вернуться наверх» — после прокрутки вниз, по центру вверху экрана, как в кабинете Ozon;
// - ползунок прокрутки вбок для широких таблиц — прижат к низу экрана, пока таблица на экране, а её собственный
//   ползунок (внизу таблицы) ещё ниже экрана. Шириной ровно в таблицу; появляется, когда видна шапка таблицы и хотя
//   бы немного строк; у конца таблицы прячется — дальше виден её собственный.
import { ArrowUp } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

/** Корень содержимого страницы (`data-page-root` в `app/dashboard/layout.tsx`): таблицы ищем внутри него. */
const PAGE_ROOT_ATTR = 'data-page-root'

/** Сколько прокрутить вниз, чтобы появилась «Вернуться наверх»: экран, но не меньше 600 px. */
function scrollTopThreshold() {
  return Math.max(600, window.innerHeight)
}

export function ScrollTopButton() {
  const [show, setShow] = useState(false)

  useEffect(() => {
    let raf = 0
    const check = () => {
      raf = 0
      setShow(window.scrollY > scrollTopThreshold())
    }
    const schedule = () => {
      if (!raf) raf = window.requestAnimationFrame(check)
    }
    check()
    window.addEventListener('scroll', schedule, { passive: true })
    window.addEventListener('resize', schedule)
    return () => {
      window.cancelAnimationFrame(raf)
      window.removeEventListener('scroll', schedule)
      window.removeEventListener('resize', schedule)
    }
  }, [])

  function toTop() {
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    window.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' })
  }

  return (
    <button
      type="button"
      onClick={toTop}
      tabIndex={show ? 0 : -1}
      aria-hidden={!show}
      className={`fixed left-1/2 top-3 z-40 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-line bg-card px-3.5 py-1.5 text-sm font-medium text-accent shadow-pop transition duration-200 hover:bg-soft ${
        show ? 'translate-y-0 opacity-100' : 'pointer-events-none -translate-y-2 opacity-0'
      }`}
      data-testid="scroll-top"
      data-visible={show || undefined}
    >
      <ArrowUp className="h-4 w-4" aria-hidden />
      Вернуться наверх
    </button>
  )
}

// ---------------------------------------------------------------------------------------------------------------
// Ползунок прокрутки вбок внизу экрана

const BAR_H = 14 // высота полосы, px
const MIN_THUMB = 32

type Bar = { left: number; width: number; thumbLeft: number; thumbWidth: number }

/** Ближайший предок таблицы, который прокручивается вбок, — в пределах корня страницы. */
function scrollParent(table: Element, root: Element): HTMLElement | null {
  for (let el = table.parentElement; el && el !== root; el = el.parentElement) {
    const ox = getComputedStyle(el).overflowX
    if (ox === 'auto' || ox === 'scroll') return el
  }
  return null
}

/** Таблицы в окнах и выезжающих панелях прокручиваются внутри окна — общий ползунок им не нужен. */
function insideFixed(el: HTMLElement, root: Element): boolean {
  if (el.closest('[role="dialog"]')) return true
  for (let p: HTMLElement | null = el; p && p !== root; p = p.parentElement) {
    if (getComputedStyle(p).position === 'fixed') return true
  }
  return false
}

function findContainers(): HTMLElement[] {
  const root = document.querySelector(`[${PAGE_ROOT_ATTR}]`)
  if (!root) return []
  const found = new Set<HTMLElement>()
  root.querySelectorAll('table, [data-hscroll]').forEach((t) => {
    const c = t.hasAttribute('data-hscroll') ? (t as HTMLElement) : scrollParent(t, root)
    if (c && !found.has(c) && !insideFixed(c, root)) found.add(c)
  })
  return Array.from(found)
}

/** Какой таблице сейчас нужен ползунок внизу экрана и где его рисовать. */
function measure(containers: HTMLElement[]): { el: HTMLElement; bar: Bar } | null {
  const vh = window.innerHeight
  for (const el of containers) {
    const span = el.scrollWidth - el.clientWidth
    if (span <= 1 || !el.isConnected) continue
    const rect = el.getBoundingClientRect()
    if (rect.width === 0 || rect.height === 0) continue
    const head = (el.querySelector('thead') as HTMLElement | null)?.offsetHeight ?? 0
    // шапка таблицы и полоса под ней — на экране, а низ таблицы (с её собственным ползунком) — ниже экрана
    if (rect.top + head + BAR_H * 2 > vh || rect.bottom <= vh) continue
    const width = el.clientWidth
    const thumbWidth = Math.max(MIN_THUMB, Math.round((width * el.clientWidth) / el.scrollWidth))
    const thumbLeft = Math.round(((width - thumbWidth) * el.scrollLeft) / span)
    return { el, bar: { left: Math.round(rect.left + el.clientLeft), width, thumbLeft, thumbWidth } }
  }
  return null
}

export function StickyScrollbar() {
  const [bar, setBar] = useState<Bar | null>(null)
  const containers = useRef<HTMLElement[]>([])
  const active = useRef<HTMLElement | null>(null)
  const drag = useRef<{ x: number; start: number } | null>(null)
  const raf = useRef(0)
  const rescanTimer = useRef<number>()

  const update = useCallback(() => {
    raf.current = 0
    const m = measure(containers.current)
    active.current = m?.el ?? null
    setBar((prev) => {
      const next = m?.bar ?? null
      if (prev === next) return prev
      if (prev && next && prev.left === next.left && prev.width === next.width && prev.thumbLeft === next.thumbLeft && prev.thumbWidth === next.thumbWidth) return prev
      return next
    })
  }, [])

  const schedule = useCallback(() => {
    if (!raf.current) raf.current = window.requestAnimationFrame(update)
  }, [update])

  useEffect(() => {
    const rescan = () => {
      containers.current = findContainers()
      schedule()
    }
    rescan()
    // таблицы появляются и меняются после загрузки данных, смены вкладок и фильтров
    const mo = new MutationObserver(() => {
      window.clearTimeout(rescanTimer.current)
      rescanTimer.current = window.setTimeout(rescan, 120)
    })
    // вся страница, а не корень содержимого: корень пересоздаётся (вход, смена магазина), подписка на старый ослепла бы
    mo.observe(document.body, { childList: true, subtree: true })
    // прокрутка страницы и прокрутка самой таблицы вбок (событие scroll не всплывает — ловим на погружении)
    document.addEventListener('scroll', schedule, { capture: true, passive: true })
    window.addEventListener('resize', rescan)
    return () => {
      mo.disconnect()
      window.clearTimeout(rescanTimer.current)
      window.cancelAnimationFrame(raf.current)
      raf.current = 0 // иначе после повторного подключения (React, режим разработки) кадр больше не запланируется
      document.removeEventListener('scroll', schedule, { capture: true })
      window.removeEventListener('resize', rescan)
    }
  }, [schedule])

  function scrollTo(el: HTMLElement, thumbLeft: number, b: Bar) {
    const span = el.scrollWidth - el.clientWidth
    const free = b.width - b.thumbWidth
    el.scrollLeft = free > 0 ? (thumbLeft / free) * span : 0
  }

  function onThumbDown(e: React.PointerEvent) {
    const el = active.current
    if (!el || !bar) return
    e.preventDefault()
    e.stopPropagation()
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    drag.current = { x: e.clientX, start: bar.thumbLeft }
  }

  function onThumbMove(e: React.PointerEvent) {
    const el = active.current
    if (!drag.current || !el || !bar) return
    scrollTo(el, drag.current.start + (e.clientX - drag.current.x), bar)
  }

  function onThumbUp(e: React.PointerEvent) {
    drag.current = null
    ;(e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId)
  }

  // нажатие на полосу мимо ползунка — ползунок встаёт серединой туда, куда нажали
  function onTrackDown(e: React.PointerEvent) {
    const el = active.current
    if (!el || !bar) return
    const x = e.clientX - bar.left - bar.thumbWidth / 2
    scrollTo(el, Math.min(Math.max(0, x), bar.width - bar.thumbWidth), bar)
  }

  // колесо над полосой — прокрутка таблицы вбок
  const trackRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const track = trackRef.current
    if (!track) return
    const onWheel = (e: WheelEvent) => {
      const el = active.current
      if (!el) return
      e.preventDefault()
      el.scrollLeft += Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY
    }
    track.addEventListener('wheel', onWheel, { passive: false })
    return () => track.removeEventListener('wheel', onWheel)
  }, [bar !== null]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!bar) return null
  return (
    <div
      ref={trackRef}
      className="group fixed z-20 flex cursor-pointer items-center border-t border-line bg-card/95 px-0.5 backdrop-blur-sm"
      style={{ left: bar.left, width: bar.width, height: BAR_H, bottom: 'env(safe-area-inset-bottom, 0px)' }}
      onPointerDown={onTrackDown}
      role="scrollbar"
      aria-orientation="horizontal"
      aria-controls={active.current?.id || undefined}
      aria-valuenow={Math.round((bar.thumbLeft / Math.max(1, bar.width - bar.thumbWidth)) * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label="Прокрутка таблицы вбок"
      data-testid="sticky-scrollbar"
    >
      <div
        className="h-1.5 touch-none rounded-full bg-faint/50 transition-[height,background-color] group-hover:h-2 group-hover:bg-faint active:bg-muted"
        style={{ width: bar.thumbWidth, transform: `translateX(${bar.thumbLeft}px)` }}
        onPointerDown={onThumbDown}
        onPointerMove={onThumbMove}
        onPointerUp={onThumbUp}
        onPointerCancel={onThumbUp}
        data-testid="sticky-scrollbar-thumb"
      />
    </div>
  )
}

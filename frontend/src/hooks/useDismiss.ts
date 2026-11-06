'use client'
// Закрытие выпадающего списка: клик мимо, переход на другую страницу, открытие другого списка (шапка, колокольчик
// уведомлений, синхронизация, фильтры). Открыт всегда один список — меню не наезжают друг на друга.
import { usePathname } from 'next/navigation'
import { useEffect, useId } from 'react'

const MENU_OPEN = 'app:menu-open'

/** Сообщить остальным спискам, что открылся этот (`id`), — они закроются. */
export function announceMenuOpen(id: string) {
  window.dispatchEvent(new CustomEvent(MENU_OPEN, { detail: id }))
}

/** Один открытый список на странице: открылся другой — закрыть этот. */
export function useExclusiveMenu(open: boolean, close: () => void, id: string) {
  useEffect(() => {
    if (open) announceMenuOpen(id)
  }, [open, id])
  useEffect(() => {
    const onOther = (e: Event) => {
      if ((e as CustomEvent<string>).detail !== id) close()
    }
    window.addEventListener(MENU_OPEN, onOther)
    return () => window.removeEventListener(MENU_OPEN, onOther)
  }, [close, id])
}

export function useDismiss(open: boolean, close: () => void, ref: React.RefObject<HTMLElement>) {
  const pathname = usePathname()
  const id = useId()
  useExclusiveMenu(open, close, id)
  useEffect(() => close(), [pathname, close])
  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close()
    }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [open, close, ref])
}

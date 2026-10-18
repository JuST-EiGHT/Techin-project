'use client'
// Закрытие выпадающего списка: клик мимо, переход на другую страницу (шапка, колокольчик уведомлений).
import { usePathname } from 'next/navigation'
import { useEffect } from 'react'

export function useDismiss(open: boolean, close: () => void, ref: React.RefObject<HTMLElement>) {
  const pathname = usePathname()
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

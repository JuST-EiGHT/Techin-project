'use client'
// «Товары → Цены» — один подраздел с вкладками (шаг 10.15): цены и расчёт (4.2), цены списком (6.3), история (6.4).
// Вкладки — пункт меню «Цены» и его подпункты (lib/nav.ts), как вкладки «Аналитики».
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useRef } from 'react'

import { NAV } from '@/lib/nav'

export function PricesTabs() {
  const pathname = usePathname()
  const prices = NAV.find((s) => s.label === 'Товары')?.items?.find((i) => i.href === '/dashboard/products/prices')
  const tabs = prices ? [{ href: prices.href, label: prices.label }, ...(prices.children ?? [])] : []
  const nav = useRef<HTMLElement>(null)
  useEffect(() => {
    const el = nav.current?.querySelector<HTMLElement>('[aria-current=page]')
    if (nav.current && el) nav.current.scrollLeft = el.offsetLeft - (nav.current.clientWidth - el.offsetWidth) / 2
  }, [pathname])
  return (
    <nav ref={nav} className="flex gap-1 overflow-x-auto border-b border-line" aria-label="Цены" data-testid="prices-tabs">
      {tabs.map((t) => {
        const active = pathname === t.href
        return (
          <Link
            key={t.href}
            href={t.href}
            aria-current={active ? 'page' : undefined}
            className={`-mb-px whitespace-nowrap border-b-2 px-4 py-2 text-sm ${active ? 'border-accent font-medium text-fg' : 'border-transparent text-muted hover:text-fg'}`}
            data-testid="prices-tab"
          >
            {t.label}
          </Link>
        )
      })}
    </nav>
  )
}

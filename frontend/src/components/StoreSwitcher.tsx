'use client'

// Переключатель магазинов (шаг 8.4b): полоса под шапкой — только когда магазинов больше одного. Один магазин —
// полосы нет, добавить второй — «Магазины» в меню аккаунта.
import { Check, ChevronDown, Store as StoreIcon } from 'lucide-react'
import Link from 'next/link'
import { useCallback, useRef, useState } from 'react'

import { useStores } from '@/contexts/StoreContext'
import { useDismiss } from '@/hooks/useDismiss'

export function StoreBar() {
  const { active, current, switchTo } = useStores()
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const close = useCallback(() => setOpen(false), [])
  useDismiss(open, close, box)
  if (active.length < 2 || !current) return null

  const elsewhere = active.filter((s) => s.id !== current.id).reduce((n, s) => n + s.unread_alerts, 0)
  return (
    <div className="flex items-center gap-2 border-b border-line px-4 py-1.5 text-sm md:px-6" data-testid="store-bar">
      <StoreIcon className="h-4 w-4 shrink-0 text-muted" aria-hidden />
      <span className="hidden text-muted sm:inline">Магазин:</span>
      <div ref={box} className="relative min-w-0">
        <button
          type="button"
          aria-haspopup="menu"
          aria-expanded={open}
          data-testid="store-button"
          onClick={() => setOpen((o) => !o)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') close()
          }}
          className="flex min-w-0 items-center gap-1 rounded-lg px-1.5 py-0.5 font-medium hover:text-accent"
        >
          <span className="truncate">{current.name}</span>
          <ChevronDown className={`h-4 w-4 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden />
        </button>
        {open && (
          <div
            role="menu"
            aria-label="Магазины"
            data-testid="store-menu"
            className="absolute left-0 top-full z-30 mt-1 w-72 max-w-[calc(100vw-2rem)] rounded-xl border border-line bg-card p-1 shadow-xl shadow-black/20"
          >
            {active.map((s) => (
              <button
                key={s.id}
                type="button"
                role="menuitem"
                onClick={() => (s.id === current.id ? close() : switchTo(s.id))}
                className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left outline-none hover:bg-line/60 focus:bg-line/60"
              >
                <Check className={`h-4 w-4 shrink-0 ${s.id === current.id ? 'text-accent' : 'invisible'}`} aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className={`block truncate ${s.id === current.id ? 'text-accent' : ''}`}>{s.name}</span>
                  <span className="block text-xs text-faint">
                    {s.client_id_masked ? `Client-Id ${s.client_id_masked}` : 'Ключ Ozon не добавлен'}
                  </span>
                </span>
                {s.unread_alerts > 0 && (
                  <span className="rounded-full bg-warn/15 px-2 py-0.5 text-xs text-warn" title="Уведомления без «Ок»">
                    {s.unread_alerts}
                  </span>
                )}
              </button>
            ))}
            <div className="mt-1 border-t border-line pt-1">
              <Link
                href="/dashboard/settings/stores"
                role="menuitem"
                onClick={close}
                className="block rounded-lg px-3 py-2 text-sm text-muted outline-none hover:bg-line/60 hover:text-fg focus:bg-line/60"
              >
                Все магазины и добавить новый →
              </Link>
            </div>
          </div>
        )}
      </div>
      {elsewhere > 0 && (
        <span className="ml-auto text-xs text-warn" data-testid="store-elsewhere">
          В других магазинах уведомлений: {elsewhere}
        </span>
      )}
    </div>
  )
}

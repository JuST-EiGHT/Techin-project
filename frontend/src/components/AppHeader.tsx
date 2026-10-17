'use client'

// Шапка кабинета: разделы с выпадающими списками, статус синхронизации, уведомления (7.2), меню аккаунта.
import { ChevronDown, KeyRound, LogOut, Menu, Settings, UserRound, X } from 'lucide-react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useCallback, useEffect, useRef, useState } from 'react'

import { AlertBell } from '@/components/alerts'
import { SyncStatus } from '@/components/SyncStatus'
import { useAuth } from '@/contexts/AuthContext'
import { useDismiss } from '@/hooks/useDismiss'
import { ACCOUNT_MENU, NAV, type NavSection, sectionActive } from '@/lib/nav'

const CLOSE_DELAY_MS = 150 // курсор ушёл с меню — закрыть не сразу, чтобы не мигало

const ACCOUNT_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  '/dashboard/settings/account': UserRound,
  '/dashboard/settings/api-keys': KeyRound,
  '/dashboard/settings': Settings,
}

/** Стрелки вверх/вниз по пунктам меню, Esc — закрыть и вернуть фокус на кнопку. */
function onMenuKeyDown(e: React.KeyboardEvent<HTMLElement>, close: () => void, button: HTMLButtonElement | null) {
  const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('[role=menuitem]'))
  const i = items.indexOf(document.activeElement as HTMLElement)
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    items[(i + 1) % items.length]?.focus()
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    items[(i - 1 + items.length) % items.length]?.focus()
  } else if (e.key === 'Escape') {
    close()
    button?.focus()
  }
}

function SectionMenu({ section, pathname }: { section: NavSection; pathname: string }) {
  const [open, setOpen] = useState(false)
  const timer = useRef<number>()
  const box = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const close = useCallback(() => setOpen(false), [])
  useDismiss(open, close, box)
  useEffect(() => () => window.clearTimeout(timer.current), [])

  // наведение — только мышью; на телефоне и планшете список открывается нажатием
  const enter = (e: React.PointerEvent) => {
    if (e.pointerType !== 'mouse') return
    window.clearTimeout(timer.current)
    setOpen(true)
  }
  const leave = (e: React.PointerEvent) => {
    if (e.pointerType !== 'mouse') return
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(close, CLOSE_DELAY_MS)
  }
  const openWithKeyboard = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowDown') return
    e.preventDefault()
    setOpen(true)
    window.setTimeout(() => box.current?.querySelector<HTMLElement>('[role=menuitem]')?.focus())
  }

  const active = sectionActive(section, pathname)
  return (
    <div ref={box} className="relative" onPointerEnter={enter} onPointerLeave={leave} data-testid="nav-section">
      <button
        ref={button}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={openWithKeyboard}
        className={`flex items-center gap-1 rounded-lg px-2 py-1.5 ${active ? 'text-accent' : 'text-muted hover:text-fg'}`}
      >
        {section.label}
        <ChevronDown className={`h-4 w-4 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden />
      </button>
      {open && (
        // pt-2 — «мостик» между кнопкой и списком: курсор не теряет меню по пути вниз
        <div className="absolute left-0 top-full z-30 pt-2">
          <div
            role="menu"
            aria-label={section.label}
            onKeyDown={(e) => onMenuKeyDown(e, close, button.current)}
            className="w-72 rounded-xl border border-line bg-card p-1 shadow-xl shadow-black/20"
          >
            {section.items?.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                role="menuitem"
                onClick={close}
                className={`block rounded-lg px-3 py-2 outline-none hover:bg-line/60 focus:bg-line/60 ${
                  pathname === item.href ? 'text-accent' : ''
                }`}
              >
                <div className="text-sm">{item.label}</div>
                {item.description && <div className="text-xs text-faint">{item.description}</div>}
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function initials(name: string | null, email: string): string {
  const source = (name || email).trim()
  const parts = source.split(/\s+/).filter(Boolean)
  const letters = parts.length > 1 ? parts[0][0] + parts[1][0] : source.slice(0, 2)
  return letters.toUpperCase()
}

function AccountMenu({ onLogout, leaving }: { onLogout: () => void; leaving: boolean }) {
  const { user } = useAuth()
  const pathname = usePathname()
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const close = useCallback(() => setOpen(false), [])
  useDismiss(open, close, box)
  if (!user) return null

  const onSettings = pathname.startsWith('/dashboard/settings')
  return (
    <div ref={box} className="relative">
      <button
        ref={button}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Меню аккаунта"
        data-testid="account-button"
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            setOpen(true)
            window.setTimeout(() => box.current?.querySelector<HTMLElement>('[role=menuitem]')?.focus())
          }
        }}
        className={`flex items-center gap-1.5 rounded-full border py-1 pl-1 pr-2 ${
          open || onSettings ? 'border-accent' : 'border-line hover:border-muted'
        }`}
      >
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-accent/15 text-xs font-semibold text-accent">
          {initials(user.full_name, user.email)}
        </span>
        <ChevronDown className={`h-4 w-4 text-muted transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden />
      </button>
      {open && (
        <div
          role="menu"
          aria-label="Аккаунт"
          onKeyDown={(e) => onMenuKeyDown(e, close, button.current)}
          data-testid="account-menu"
          className="absolute right-0 top-full z-30 mt-2 w-64 rounded-xl border border-line bg-card p-1 shadow-xl shadow-black/20"
        >
          <div className="border-b border-line px-3 py-2">
            <div className="truncate text-sm font-medium">{user.full_name || 'Без имени'}</div>
            <div className="truncate text-xs text-muted">{user.email}</div>
          </div>
          <div className="py-1">
            {ACCOUNT_MENU.map((item) => {
              const Icon = ACCOUNT_ICONS[item.href] ?? Settings
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  role="menuitem"
                  onClick={close}
                  className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm outline-none hover:bg-line/60 focus:bg-line/60 ${
                    pathname === item.href ? 'text-accent' : ''
                  }`}
                >
                  <Icon className="h-4 w-4 text-muted" />
                  {item.label}
                </Link>
              )
            })}
          </div>
          <div className="border-t border-line pt-1">
            <button
              type="button"
              role="menuitem"
              onClick={onLogout}
              disabled={leaving}
              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-bad outline-none hover:bg-bad/10 focus:bg-bad/10 disabled:opacity-60"
            >
              <LogOut className="h-4 w-4" />
              Выйти
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function MobileNav({ pathname, onClose }: { pathname: string; onClose: () => void }) {
  return (
    <nav className="border-b border-line bg-card px-4 py-3 lg:hidden" data-testid="mobile-nav">
      {NAV.map((section) =>
        section.href ? (
          <Link key={section.label} href={section.href} onClick={onClose} className={`block py-2 font-medium ${sectionActive(section, pathname) ? 'text-accent' : ''}`}>
            {section.label}
          </Link>
        ) : (
          <div key={section.label} className="py-2">
            <div className={`text-xs uppercase tracking-wide ${sectionActive(section, pathname) ? 'text-accent' : 'text-faint'}`}>{section.label}</div>
            {section.items?.map((item) => (
              <Link key={item.href} href={item.href} onClick={onClose} className="block py-1.5 pl-3 text-sm">
                {item.label}
              </Link>
            ))}
          </div>
        ),
      )}
    </nav>
  )
}

export function AppHeader({ onLogout, leaving }: { onLogout: () => void; leaving: boolean }) {
  const pathname = usePathname()
  const [mobileOpen, setMobileOpen] = useState(false)
  useEffect(() => setMobileOpen(false), [pathname])

  return (
    <>
      <header className="flex items-center gap-4 border-b border-line px-4 py-3 md:px-6">
        <button
          type="button"
          className="rounded-lg p-1.5 text-muted hover:text-fg lg:hidden"
          aria-label={mobileOpen ? 'Закрыть меню' : 'Открыть меню'}
          aria-expanded={mobileOpen}
          onClick={() => setMobileOpen((o) => !o)}
        >
          {mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
        <Link href="/dashboard" className="font-semibold">
          Ozon Analytics
        </Link>
        <nav className="hidden items-center gap-1 text-sm lg:flex" aria-label="Разделы">
          {NAV.filter((section) => !section.bellOnDesktop).map((section) =>
            section.href ? (
              <Link
                key={section.label}
                href={section.href}
                className={`rounded-lg px-2 py-1.5 ${sectionActive(section, pathname) ? 'text-accent' : 'text-muted hover:text-fg'}`}
              >
                {section.label}
              </Link>
            ) : (
              <SectionMenu key={section.label} section={section} pathname={pathname} />
            ),
          )}
        </nav>
        <div className="ml-auto hidden sm:block">
          <SyncStatus />
        </div>
        <div className="ml-auto flex items-center gap-2 sm:ml-0">
          <AlertBell />
          <AccountMenu onLogout={onLogout} leaving={leaving} />
        </div>
      </header>
      {mobileOpen && <MobileNav pathname={pathname} onClose={() => setMobileOpen(false)} />}
    </>
  )
}

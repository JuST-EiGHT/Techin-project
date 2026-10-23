'use client'
// Кнопка «Синхронизация» в шапке (шаг 10.2): состояние одним значком — всё свежее / идёт загрузка / есть ошибка; по нажатию —
// окно по видам данных текущего магазина: когда обновлено, идёт ли сейчас, что не так и что сделать, «Обновить» по одному и
// «Обновить всё». UpdatedAt — общий «Обновлено …» для разделов (Аналитика 10.7, «Требует внимания» 10.5).
import { AlertTriangle, RefreshCw } from 'lucide-react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { useStores } from '@/contexts/StoreContext'
import { useDismiss } from '@/hooks/useDismiss'
import { errorMessage } from '@/lib/api'
import {
  type SyncKind,
  type SyncState,
  type SyncStatus,
  freshness,
  minutesLeft,
  refreshSyncStatus,
  syncAll,
  timeAgo,
  useSyncStatus,
} from '@/lib/sync'

// Открыть окно синхронизации из любого места (например, «Обновлено …» в разделе). Ссылка «…?sync=1» (аномалии
// синхронизации 10.5, Telegram) открывает его после перехода.
const OPEN_SYNC = 'open-sync-panel'
export const openSyncPanel = () => window.dispatchEvent(new Event(OPEN_SYNC))
export const isSyncLink = (href: string) => /[?&]sync=1(&|$)/.test(href)

const DOT: Record<SyncState, string> = {
  fresh: 'bg-ok',
  running: 'bg-accent',
  error: 'bg-bad',
  stale: 'bg-warn',
  never: 'bg-warn',
  no_source: 'bg-faint',
}
const STATE_TEXT: Record<SyncState, string> = {
  fresh: 'все данные свежие',
  running: 'идёт загрузка',
  error: 'есть ошибка',
  stale: 'не всё свежее',
  never: 'ещё не загружались',
  no_source: 'ключ Ozon не подключён',
}

const TICK_MS = 30_000 // обновить «N минут назад» и «снова можно через N мин»

function useTick() {
  const [, setTick] = useState(0)
  useEffect(() => {
    const t = window.setInterval(() => setTick((x) => x + 1), TICK_MS)
    return () => window.clearInterval(t)
  }, [])
}

const titles = (kinds: SyncKind[]) => kinds.map((k) => k.title.toLowerCase()).join(', ')
const isRecent = (k: SyncKind) => k.recent_until !== null && new Date(k.recent_until).getTime() > Date.now()

function summary(s: SyncStatus): string {
  const of = (state: SyncKind['state'][]) => s.kinds.filter((k) => state.includes(k.state))
  switch (s.state) {
    case 'running':
      return `Идёт загрузка: ${titles(of(['running']))}.`
    case 'error':
      return `Есть ошибки: ${titles(of(['error']))} — что сделать, написано ниже.`
    case 'stale':
      return `Не всё свежее: ${titles(of(['stale', 'never']))}.`
    case 'fresh': {
      const oldest = s.kinds.find((k) => k.kind === s.oldest_kind)
      return `Все данные свежие${oldest?.last_success_at ? `: самые старые — ${oldest.title.toLowerCase()}, ${timeAgo(oldest.last_success_at)}` : ''}.`
    }
    default:
      return 'Ключ Ozon не подключён — данные из Ozon сами не загружаются. Можно загружать отчёты XLSX из кабинета Ozon.'
  }
}

/** «Обновлено 5 минут назад» — самые старые из данных kinds (без kinds — все виды). Нажатие открывает окно синхронизации. */
export function UpdatedAt({ kinds, className = '' }: { kinds?: string[]; className?: string }) {
  const s = useSyncStatus()
  useTick()
  if (!s || s.kinds.length === 0) return null
  const f = freshness(s, kinds)
  if (f.kinds.length === 0) return null
  const failed = f.kinds.filter((k) => k.state === 'error')
  const running = f.kinds.some((k) => k.running)
  const text = running
    ? 'Данные загружаются…'
    : failed.length
      ? `Ошибка загрузки: ${titles(failed)}`
      : f.never
        ? 'Данные ещё не загружались'
        : f.at
          ? `Обновлено ${timeAgo(f.at)}`
          : ''
  return (
    <button
      type="button"
      onClick={openSyncPanel}
      title="Синхронизация: когда обновлены данные и что сделать"
      className={`inline-flex items-center gap-1.5 text-xs hover:underline ${failed.length ? 'text-bad' : 'text-muted'} ${className}`}
      data-testid="updated-at"
    >
      <span className={`h-1.5 w-1.5 rounded-full ${running ? 'bg-accent' : failed.length ? 'bg-bad' : f.never ? 'bg-warn' : 'bg-ok'}`} aria-hidden />
      {text}
    </button>
  )
}

function KindRow({ k, onRefresh, onClose, busy }: { k: SyncKind; onRefresh: () => void; onClose: () => void; busy: boolean }) {
  const recent = isRecent(k)
  return (
    <li className="px-4 py-3" data-testid="sync-kind" data-kind={k.kind} data-state={k.state}>
      <div className="flex items-start gap-3">
        <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${DOT[k.state]}`} aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">{k.title}</div>
          <div className="text-xs text-muted">
            {k.running ? (
              <span className="text-accent">Загружается…</span>
            ) : k.last_success_at ? (
              `Обновлено ${timeAgo(k.last_success_at)}`
            ) : (
              'Ещё не загружались'
            )}
            <span className="text-faint"> · {k.schedule}</span>
          </div>
        </div>
        {k.can_refresh && (
          <button
            type="button"
            onClick={onRefresh}
            disabled={busy || k.running || recent}
            title={recent && k.recent_until ? `Обновлено недавно — снова можно через ${minutesLeft(k.recent_until)}` : `Обновить: ${k.title.toLowerCase()}`}
            className="shrink-0 rounded-lg border border-line px-2.5 py-1 text-xs hover:border-accent hover:text-accent disabled:opacity-50 disabled:hover:border-line disabled:hover:text-fg"
            data-testid="sync-kind-refresh"
          >
            {k.running ? 'Идёт…' : recent ? 'Свежие' : 'Обновить'}
          </button>
        )}
      </div>
      {k.problem && !k.running && (
        <div className={`ml-5 mt-1.5 rounded-lg px-3 py-2 text-xs ${k.state === 'error' ? 'bg-bad/10' : 'bg-warn/10'}`} data-testid="sync-problem">
          <span className={`font-medium ${k.state === 'error' ? 'text-bad' : 'text-warn'}`}>{k.problem}.</span>{' '}
          <span className="text-muted">{k.hint}</span>
          {k.action && (
            <Link href={k.action.href} onClick={onClose} className="ml-1 whitespace-nowrap text-accent hover:underline" data-testid="sync-action">
              {k.action.label} →
            </Link>
          )}
          {k.error && (
            <details className="mt-1 text-faint">
              <summary className="cursor-pointer hover:text-muted">Подробности</summary>
              <span className="break-words">{k.error}</span>
            </details>
          )}
        </div>
      )}
    </li>
  )
}

export function SyncButton() {
  const s = useSyncStatus()
  const { active, current } = useStores()
  const pathname = usePathname()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const close = useCallback(() => setOpen(false), [])
  useDismiss(open, close, box)
  useTick()

  // при переходах по кабинету — свежий статус (как прежний статус товаров в шапке); «?sync=1» — сразу открыть окно
  useEffect(() => {
    refreshSyncStatus()
    const url = new URL(window.location.href)
    if (url.searchParams.get('sync') !== '1') return
    url.searchParams.delete('sync')
    window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash)
    setOpen(true)
  }, [pathname])

  useEffect(() => {
    const show = () => {
      setOpen(true)
      refreshSyncStatus()
    }
    window.addEventListener(OPEN_SYNC, show)
    return () => window.removeEventListener(OPEN_SYNC, show)
  }, [])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      close()
      button.current?.focus()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, close])

  async function refresh(kinds?: string[]) {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      setNotice((await syncAll(kinds)).message)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const state: SyncState = s?.state ?? 'no_source'
  const anyToRefresh = s?.kinds.some((k) => k.can_refresh && !k.running && !isRecent(k)) ?? false
  const label = `Синхронизация: ${STATE_TEXT[state]}`

  return (
    <div ref={box} className="relative">
      <button
        ref={button}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={label}
        title={label}
        onClick={() => {
          setOpen((o) => !o)
          setNotice(null)
          setError(null)
          refreshSyncStatus()
        }}
        className={`relative flex items-center gap-2 rounded-lg border px-2 py-1.5 text-sm hover:border-accent hover:text-accent ${
          open ? 'border-accent text-accent' : state === 'error' ? 'border-bad/60 text-bad' : 'border-line text-muted'
        }`}
        data-testid="sync-button"
        data-state={state}
      >
        {state === 'error' ? (
          <AlertTriangle className="h-4 w-4" aria-hidden />
        ) : (
          <RefreshCw className={`h-4 w-4 ${state === 'running' ? 'animate-spin text-accent' : ''}`} aria-hidden />
        )}
        <span className="hidden 2xl:inline">Синхронизация</span>
        {s && state !== 'running' && state !== 'error' && (
          <span className={`absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full border-2 border-bg ${DOT[state]}`} data-testid="sync-dot" aria-hidden />
        )}
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Синхронизация"
          className="fixed inset-x-2 top-14 z-30 rounded-xl border border-line bg-card shadow-xl shadow-black/20 sm:absolute sm:inset-x-auto sm:right-0 sm:top-full sm:mt-2 sm:w-[28rem]"
          data-testid="sync-panel"
        >
          <div className="border-b border-line px-4 py-3">
            <div className="flex items-baseline gap-2">
              <span className="font-semibold">Синхронизация</span>
              {active.length > 1 && current && <span className="truncate text-xs text-muted">магазин «{current.name}»</span>}
            </div>
            {s && (
              <p className={`mt-1 text-sm ${state === 'error' ? 'text-bad' : 'text-muted'}`} data-testid="sync-summary">
                {summary(s)}
              </p>
            )}
          </div>
          {!s ? (
            <p className="px-4 py-3 text-sm text-muted">Загружаем…</p>
          ) : s.kinds.length === 0 ? (
            <div className="space-y-2 px-4 py-3 text-sm" data-testid="sync-no-source">
              <Link href="/dashboard/settings/integrations" onClick={close} className="block text-accent hover:underline">
                Подключить ключ Ozon →
              </Link>
              <Link href="/dashboard/analytics/reports/upload" onClick={close} className="block text-accent hover:underline">
                Загрузить отчёт XLSX →
              </Link>
            </div>
          ) : (
            <ul className="max-h-[60vh] divide-y divide-line overflow-y-auto">
              {s.kinds.map((k) => (
                <KindRow key={k.kind} k={k} busy={busy} onRefresh={() => refresh([k.kind])} onClose={close} />
              ))}
            </ul>
          )}
          <div className="space-y-2 border-t border-line px-4 py-3 text-sm">
            <FormError message={error} />
            {notice && (
              <p className="text-xs text-muted" data-testid="sync-notice">
                {notice}
              </p>
            )}
            <div className="flex flex-wrap items-center gap-3">
              {s && s.kinds.length > 0 && (
                <button
                  type="button"
                  onClick={() => refresh()}
                  disabled={busy || !anyToRefresh}
                  title={anyToRefresh ? 'Загрузить всё, что не загружается сейчас и не обновлялось только что' : 'Всё уже загружается или только что обновлено'}
                  className="flex items-center gap-2 rounded-lg bg-accent px-3 py-1.5 font-medium text-white hover:opacity-90 disabled:opacity-50"
                  data-testid="sync-all"
                >
                  <RefreshCw className={`h-4 w-4 ${busy ? 'animate-spin' : ''}`} aria-hidden />
                  Обновить всё
                </button>
              )}
              <Link href="/dashboard/settings/integrations" onClick={close} className="ml-auto text-xs text-accent hover:underline">
                Ключи и банки →
              </Link>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

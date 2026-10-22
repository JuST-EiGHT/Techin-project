'use client'

// Магазины аккаунта (шаг 8.4b): список, текущий магазин и переключение. Выбор хранится в cookie store_id, прокси
// передаёт его бэкенду заголовком X-Store-Id — все данные страниц приходят уже по выбранному магазину. Переключение —
// перезагрузка страницы: ничего от прежнего магазина на экране не остаётся.
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import { api } from '@/lib/api'
import { STORE_COOKIE } from '@/lib/session'

export type Store = {
  id: string
  name: string
  is_main: boolean
  is_active: boolean
  client_id_masked: string | null
  keys: number
  active_keys: number
  unread_alerts: number
  created_at: string
}

type StoreContextValue = {
  /** null — ещё загружаются */
  stores: Store[] | null
  current: Store | null
  /** Включённые магазины — для переключателя. */
  active: Store[]
  switchTo: (id: string, path?: string) => void
  reload: () => Promise<Store[] | null>
}

const StoreContext = createContext<StoreContextValue | null>(null)
const YEAR = 365 * 24 * 3600

export function storeCookie(): string | null {
  if (typeof document === 'undefined') return null
  const hit = document.cookie.split('; ').find((c) => c.startsWith(`${STORE_COOKIE}=`))
  return hit ? decodeURIComponent(hit.slice(STORE_COOKIE.length + 1)) || null : null
}

function setStoreCookie(id: string | null) {
  document.cookie = id
    ? `${STORE_COOKIE}=${encodeURIComponent(id)}; path=/; max-age=${YEAR}; samesite=lax`
    : `${STORE_COOKIE}=; path=/; max-age=0; samesite=lax`
}

/** Перейти в магазин: cookie и перезагрузка. Без path — та же страница без параметров (в них — id товаров и отчётов
 * прежнего магазина). */
export function openStore(id: string, path?: string) {
  setStoreCookie(id)
  window.location.href = path ?? window.location.pathname
}

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const [stores, setStores] = useState<Store[] | null>(null)

  const reload = useCallback(async () => {
    try {
      const { data } = await api.get<Store[]>('/stores')
      setStores(data)
      return data
    } catch {
      setStores((s) => s ?? [])
      return null
    }
  }, [])

  useEffect(() => {
    reload().then((list) => {
      if (!list) return
      const active = list.filter((s) => s.is_active)
      const url = new URL(window.location.href)
      const wanted = url.searchParams.get('store') // ссылка из Telegram открывает сайт в магазине уведомления
      if (wanted) {
        url.searchParams.delete('store')
        const path = url.pathname + url.search
        if (active.some((s) => s.id === wanted) && wanted !== (storeCookie() ?? list[0]?.id)) openStore(wanted, path)
        else window.history.replaceState(null, '', path)
        return
      }
      const saved = storeCookie()
      if (saved && !active.some((s) => s.id === saved)) {
        // магазин отключили или он чужой (вход другого человека) — к первому магазину
        setStoreCookie(null)
        window.location.reload()
      }
    })
  }, [reload])

  const value = useMemo<StoreContextValue>(() => {
    const active = (stores ?? []).filter((s) => s.is_active)
    const saved = storeCookie()
    const current = active.find((s) => s.id === saved) ?? active.find((s) => s.is_main) ?? null
    return { stores, current, active, switchTo: openStore, reload }
  }, [stores, reload])

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>
}

export function useStores(): StoreContextValue {
  const ctx = useContext(StoreContext)
  if (!ctx) throw new Error('useStores — только внутри StoreProvider')
  return ctx
}

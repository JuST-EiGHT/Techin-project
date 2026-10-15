'use client'

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import { useAuth } from '@/contexts/AuthContext'
import { api } from '@/lib/api'
import { type Theme, type ThemePreference, applyTheme, readStoredPreference, storePreference } from '@/lib/theme'

type ThemeContextValue = {
  preference: ThemePreference
  theme: Theme
  /** Сменить тему: сразу на экране, в браузере и в настройках аккаунта (одинаково на всех устройствах). */
  setPreference: (pref: ThemePreference) => Promise<void>
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth()
  const [preference, setPref] = useState<ThemePreference>('dark')
  const [theme, setTheme] = useState<Theme>('dark')

  const show = useCallback((pref: ThemePreference) => {
    setPref(pref)
    setTheme(applyTheme(pref))
    storePreference(pref)
  }, [])

  // при старте — выбор из браузера (уже применён скриптом в <head>)
  useEffect(() => show(readStoredPreference()), [show])

  // после входа — выбор из аккаунта: он главный, если пользователь менял тему на другом устройстве
  useEffect(() => {
    if (!user) return
    api
      .get<{ theme: ThemePreference }>('/settings', { skipAuthRedirect: true })
      .then((r) => show(r.data.theme))
      .catch(() => undefined)
  }, [user, show])

  // «как в системе» — следить за сменой темы системы
  useEffect(() => {
    if (preference !== 'system') return
    const mq = window.matchMedia('(prefers-color-scheme: light)')
    const onChange = () => setTheme(applyTheme('system'))
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [preference])

  const setPreference = useCallback(
    async (pref: ThemePreference) => {
      show(pref)
      if (user) await api.patch('/settings', { theme: pref })
    },
    [user, show],
  )

  const value = useMemo(() => ({ preference, theme, setPreference }), [preference, theme, setPreference])
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme должен использоваться внутри ThemeProvider')
  return ctx
}

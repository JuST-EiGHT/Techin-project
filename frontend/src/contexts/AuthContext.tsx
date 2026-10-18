'use client'

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import { api, session } from '@/lib/api'

export type User = {
  id: string
  email: string
  full_name: string | null
  is_active: boolean
  is_verified: boolean
  created_at: string
}

export type RegisterData = { email: string; password: string; full_name?: string; invite_code?: string }

type AuthContextValue = {
  user: User | null
  /** true, пока при старте загружается /auth/me */
  loading: boolean
  login: (email: string, password: string) => Promise<User>
  register: (data: RegisterData) => Promise<User>
  logout: () => Promise<void>
  /** Обновить данные пользователя после правки профиля. */
  setUser: (user: User) => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

async function fetchMe(): Promise<User> {
  // Без редиректа: на публичных страницах отсутствие сессии — нормальное состояние
  const { data } = await api.get<User>('/auth/me', { skipAuthRedirect: true })
  return data
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchMe()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    await session.post('/login', { email, password })
    const me = await fetchMe()
    setUser(me)
    return me
  }, [])

  const register = useCallback(
    async (data: RegisterData) => {
      await api.post('/auth/register', data, { skipAuthRedirect: true })
      return login(data.email, data.password)
    },
    [login],
  )

  const logout = useCallback(async () => {
    try {
      await session.post('/logout')
    } finally {
      setUser(null)
    }
  }, [])

  const value = useMemo(
    () => ({ user, loading, login, register, logout, setUser }),
    [user, loading, login, register, logout],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth должен использоваться внутри AuthProvider')
  return ctx
}

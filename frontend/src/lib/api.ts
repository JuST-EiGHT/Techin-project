// HTTP-клиент браузера. Токены лежат в httpOnly cookies и JS-у недоступны,
// поэтому запросы идут через прокси Next (/api/session/proxy → бэкенд /api/v1),
// который сам подставляет Authorization.
import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'

declare module 'axios' {
  interface AxiosRequestConfig {
    /** Не уводить на /login, если сессию восстановить не удалось (решает вызывающий код). */
    skipAuthRedirect?: boolean
    _retried?: boolean
  }
}

export const api = axios.create({ baseURL: '/api/session/proxy' })

/** Запросы управления сессией (cookies ставит/чистит сервер Next). */
export const session = axios.create({ baseURL: '/api/session' })

// Несколько одновременных 401 → один общий refresh (refresh-токен одноразовый).
let refreshing: Promise<boolean> | null = null

export function refreshSession(): Promise<boolean> {
  refreshing ??= session
    .post('/refresh')
    .then(() => true)
    .catch(() => false)
    .finally(() => {
      refreshing = null
    })
  return refreshing
}

function goToLogin() {
  if (typeof window === 'undefined') return
  const next = window.location.pathname + window.location.search
  window.location.href = `/login?next=${encodeURIComponent(next)}`
}

api.interceptors.response.use(undefined, async (error: AxiosError) => {
  const config = error.config as InternalAxiosRequestConfig | undefined
  if (error.response?.status !== 401 || !config) throw error
  if (!config._retried) {
    config._retried = true
    if (await refreshSession()) return api(config)
  }
  if (!config.skipAuthRedirect) goToLogin()
  throw error
})

/** Текст ошибки для пользователя: detail с бэкенда уже на русском. */
export function errorMessage(err: unknown, fallback = 'Что-то пошло не так, попробуйте ещё раз'): string {
  if (axios.isAxiosError(err)) {
    const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail
    if (typeof detail === 'string' && detail) return detail
    if (!err.response) return 'Нет связи с сервером'
  }
  return fallback
}

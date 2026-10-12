// Общие константы сессии. Без Node-зависимостей — используется и в middleware (edge).
export const ACCESS_COOKIE = 'access_token'
export const REFRESH_COOKIE = 'refresh_token'

/** Безопасный адрес возврата после входа: только внутренний путь. */
export function safeNext(next: string | null | undefined, fallback = '/dashboard'): string {
  if (!next || !next.startsWith('/') || next.startsWith('//') || next.includes('\\')) return fallback
  return next
}

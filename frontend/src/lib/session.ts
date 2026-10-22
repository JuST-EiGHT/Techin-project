// Общие константы сессии. Без Node-зависимостей — используется и в middleware (edge).
export const ACCESS_COOKIE = 'access_token'
export const REFRESH_COOKIE = 'refresh_token'
/** Выбранный магазин (шаг 8.4b): не секрет — обычная cookie, её ставит переключатель, прокси передаёт бэкенду X-Store-Id. */
export const STORE_COOKIE = 'store_id'

/** Безопасный адрес возврата после входа: только внутренний путь. */
export function safeNext(next: string | null | undefined, fallback = '/dashboard'): string {
  if (!next || !next.startsWith('/') || next.startsWith('//') || next.includes('\\')) return fallback
  return next
}

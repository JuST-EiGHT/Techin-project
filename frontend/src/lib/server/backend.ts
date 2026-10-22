// Только для серверного кода (route handlers): обращение к FastAPI и работа с cookies токенов.
import { NextResponse, type NextRequest } from 'next/server'

import { ACCESS_COOKIE, REFRESH_COOKIE, STORE_COOKIE } from '@/lib/session'

// Сервер Next ходит к бэкенду напрямую. В Docker это http://api:8000 (API_URL),
// при локальном запуске — NEXT_PUBLIC_API_URL / localhost.
const BACKEND_URL = (process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000').replace(/\/$/, '')

export type Tokens = { access_token: string; refresh_token: string; expires_in: number }

export function backendUrl(path: string): string {
  return `${BACKEND_URL}/api/v1${path.startsWith('/') ? path : `/${path}`}`
}

export function errorResponse(detail: string, code: number) {
  return NextResponse.json({ detail, code }, { status: code })
}

/** IP и браузер посетителя — бэкенду для журнала действий и лимита попыток входа (иначе он видит только адрес сервера
 * Next). На сервере X-Real-IP и X-Forwarded-For ставит Nginx — адрес соединения, присланное браузером он перезаписывает. */
export function clientHeaders(req: NextRequest): Record<string, string> {
  const headers: Record<string, string> = {}
  const ip = req.headers.get('x-real-ip') || req.headers.get('x-forwarded-for')?.split(',')[0].trim()
  if (ip) headers['x-forwarded-for'] = ip
  const agent = req.headers.get('user-agent')
  if (agent) headers['user-agent'] = agent
  return headers
}

/** fetch к бэкенду; если бэкенд недоступен — понятная ошибка 502, а не исключение. */
export async function backendFetch(path: string, init: RequestInit): Promise<Response | NextResponse> {
  try {
    return await fetch(backendUrl(path), { ...init, cache: 'no-store' })
  } catch {
    return errorResponse('Сервер недоступен, попробуйте позже', 502)
  }
}

/** Пробрасывает ответ бэкенда как есть (статус, тело, тип, имя файла). */
export async function passThrough(res: Response): Promise<NextResponse> {
  if (res instanceof NextResponse) return res
  const body = res.status === 204 ? null : await res.arrayBuffer()
  const headers = new Headers()
  for (const name of ['content-type', 'content-disposition']) {
    const value = res.headers.get(name)
    if (value) headers.set(name, value) // content-disposition — имя скачиваемого файла
  }
  return new NextResponse(body, { status: res.status, headers })
}

/** Срок жизни cookie = остаток жизни JWT (поле exp). */
function secondsLeft(jwt: string, fallback: number): number {
  try {
    const payload = JSON.parse(Buffer.from(jwt.split('.')[1], 'base64url').toString('utf8'))
    const left = payload.exp - Math.floor(Date.now() / 1000)
    return left > 0 ? left : fallback
  } catch {
    return fallback
  }
}

const cookieBase = {
  httpOnly: true,
  sameSite: 'lax' as const,
  secure: process.env.NODE_ENV === 'production',
  path: '/',
}

export function setTokenCookies(res: NextResponse, tokens: Tokens): void {
  res.cookies.set(ACCESS_COOKIE, tokens.access_token, { ...cookieBase, maxAge: tokens.expires_in })
  res.cookies.set(REFRESH_COOKIE, tokens.refresh_token, {
    ...cookieBase,
    maxAge: secondsLeft(tokens.refresh_token, 30 * 24 * 3600),
  })
}

export function clearTokenCookies(res: NextResponse): void {
  res.cookies.set(ACCESS_COOKIE, '', { ...cookieBase, maxAge: 0 })
  res.cookies.set(REFRESH_COOKIE, '', { ...cookieBase, maxAge: 0 })
  clearStoreCookie(res)
}

/** Выбор магазина — до выхода: следующий вход (может быть, другого человека) начинается с первого магазина. */
export function clearStoreCookie(res: NextResponse): void {
  res.cookies.set(STORE_COOKIE, '', { path: '/', maxAge: 0 })
}

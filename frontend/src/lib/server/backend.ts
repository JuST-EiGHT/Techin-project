// Только для серверного кода (route handlers): обращение к FastAPI и работа с cookies токенов.
import { NextResponse } from 'next/server'

import { ACCESS_COOKIE, REFRESH_COOKIE } from '@/lib/session'

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
}

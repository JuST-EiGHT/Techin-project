import { NextRequest, NextResponse } from 'next/server'

import { resolveRedirect } from '@/lib/redirects'
import { ACCESS_COOKIE, REFRESH_COOKIE } from '@/lib/session'

// /dashboard/* — только для вошедших. Подлинность токена здесь не проверяем:
// это делает бэкенд; истёкший access обновится через refresh на клиенте (lib/api.ts).
// Сначала — переадресация старых адресов (шаг 10.1): без входа попадём на вход уже с новым адресом в next.
export function middleware(req: NextRequest) {
  const moved = resolveRedirect(req.nextUrl.pathname, req.nextUrl.search)
  if (moved) {
    const url = req.nextUrl.clone()
    const [pathname, search] = moved.to.split('?')
    url.pathname = pathname
    url.search = search ? `?${search}` : ''
    return NextResponse.redirect(url, moved.permanent ? 308 : 307)
  }

  const hasSession = req.cookies.has(ACCESS_COOKIE) || req.cookies.has(REFRESH_COOKIE)
  if (hasSession) return NextResponse.next()

  const url = req.nextUrl.clone()
  url.pathname = '/login'
  url.search = `?next=${encodeURIComponent(req.nextUrl.pathname + req.nextUrl.search)}`
  return NextResponse.redirect(url)
}

export const config = { matcher: ['/dashboard', '/dashboard/:path*'] }

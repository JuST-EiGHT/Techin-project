import { NextRequest, NextResponse } from 'next/server'

import { ACCESS_COOKIE, REFRESH_COOKIE } from '@/lib/session'

// /dashboard/* — только для вошедших. Подлинность токена здесь не проверяем:
// это делает бэкенд; истёкший access обновится через refresh на клиенте (lib/api.ts).
export function middleware(req: NextRequest) {
  const hasSession = req.cookies.has(ACCESS_COOKIE) || req.cookies.has(REFRESH_COOKIE)
  if (hasSession) return NextResponse.next()

  const url = req.nextUrl.clone()
  url.pathname = '/login'
  url.search = `?next=${encodeURIComponent(req.nextUrl.pathname + req.nextUrl.search)}`
  return NextResponse.redirect(url)
}

export const config = { matcher: ['/dashboard', '/dashboard/:path*'] }

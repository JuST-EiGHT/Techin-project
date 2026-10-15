import { NextRequest, NextResponse } from 'next/server'

import { backendFetch, clearTokenCookies } from '@/lib/server/backend'
import { ACCESS_COOKIE, REFRESH_COOKIE } from '@/lib/session'

// Выход: отзываем оба токена на бэкенде и чистим cookies.
// Cookies чистим в любом случае — даже если бэкенд недоступен или токен уже истёк.
export async function POST(req: NextRequest) {
  const access = req.cookies.get(ACCESS_COOKIE)?.value
  const refresh = req.cookies.get(REFRESH_COOKIE)?.value

  if (access) {
    await backendFetch('/auth/logout', {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${access}` },
      body: JSON.stringify({ refresh_token: refresh ?? null }),
    })
  }

  const out = new NextResponse(null, { status: 204 })
  clearTokenCookies(out)
  return out
}

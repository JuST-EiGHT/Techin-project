import { NextRequest, NextResponse } from 'next/server'

import { backendFetch, clearTokenCookies, errorResponse, passThrough, setTokenCookies, type Tokens } from '@/lib/server/backend'
import { REFRESH_COOKIE } from '@/lib/session'

// Обновление токенов по refresh-cookie. Refresh одноразовый (ротация на бэкенде),
// поэтому новые токены сразу перезаписывают cookies.
export async function POST(req: NextRequest) {
  const refresh = req.cookies.get(REFRESH_COOKIE)?.value
  if (!refresh) return errorResponse('Сессия истекла, войдите снова', 401)

  const res = await backendFetch('/auth/refresh', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ refresh_token: refresh }),
  })
  if (res instanceof NextResponse) return res
  if (!res.ok) {
    const out = await passThrough(res)
    if (res.status === 401 || res.status === 403) clearTokenCookies(out)
    return out
  }

  const out = NextResponse.json({ ok: true })
  setTokenCookies(out, (await res.json()) as Tokens)
  return out
}

import { NextRequest, NextResponse } from 'next/server'

import { backendFetch, passThrough, setTokenCookies, type Tokens } from '@/lib/server/backend'

// Вход: бэкенд выдаёт токены, мы кладём их в httpOnly cookies и в браузер не отдаём.
export async function POST(req: NextRequest) {
  const res = await backendFetch('/auth/login', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: await req.text(),
  })
  if (res instanceof NextResponse || !res.ok) return passThrough(res)

  const tokens = (await res.json()) as Tokens
  const out = NextResponse.json({ ok: true })
  setTokenCookies(out, tokens)
  return out
}

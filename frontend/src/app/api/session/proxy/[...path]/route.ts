import { NextRequest } from 'next/server'

import { backendFetch, clientHeaders, errorResponse, passThrough } from '@/lib/server/backend'
import { ACCESS_COOKIE, STORE_COOKIE } from '@/lib/session'

// Прокси браузер → бэкенд: /api/session/proxy/<путь> → <API>/api/v1/<путь>.
// Подставляет access-токен из httpOnly cookie в заголовок Authorization, выбранный магазин — в X-Store-Id (шаг 8.4b).

// Эти методы выдают или отзывают токены — только через /api/session/{login,refresh,logout},
// чтобы токены никогда не попадали в JS браузера.
const BLOCKED = new Set(['auth/login', 'auth/refresh', 'auth/logout'])
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

async function handle(req: NextRequest, { params }: { params: { path: string[] } }) {
  if (params.path.some((p) => p === '..' || p === '.' || p.includes('/'))) return errorResponse('Не найдено', 404)
  const path = params.path.join('/')
  if (BLOCKED.has(path)) return errorResponse('Не найдено', 404)

  const headers: Record<string, string> = clientHeaders(req) // IP и браузер — в журнал действий
  const type = req.headers.get('content-type')
  if (type) headers['content-type'] = type
  const access = req.cookies.get(ACCESS_COOKIE)?.value
  if (access) headers.authorization = `Bearer ${access}`
  const store = req.cookies.get(STORE_COOKIE)?.value
  if (store && UUID.test(store)) headers['x-store-id'] = store // чужой магазин бэкенд не покажет — ответит 404

  const hasBody = !['GET', 'HEAD'].includes(req.method)
  const res = await backendFetch(`/${path}${req.nextUrl.search}`, {
    method: req.method,
    headers,
    body: hasBody ? await req.arrayBuffer() : undefined,
  })
  return passThrough(res)
}

export { handle as GET, handle as POST, handle as PUT, handle as PATCH, handle as DELETE }

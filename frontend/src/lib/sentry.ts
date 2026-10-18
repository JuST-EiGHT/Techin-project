// Ошибки браузера — в Sentry или совместимый сервис (шаг 8.4). DSN вписывается при сборке (NEXT_PUBLIC_SENTRY_DSN из
// .env.prod); пусто — SDK даже не загружается. Наружу не уходят IP, cookies и код приглашения из адреса страницы.
const DSN = process.env.NEXT_PUBLIC_SENTRY_DSN || ''

type Sentry = typeof import('@sentry/browser')
let loading: Promise<Sentry | null> | null = null

function scrub<T>(data: T): T {
  const text = JSON.stringify(data)
  if (!text || !text.includes('invite=')) return data
  return JSON.parse(text.replace(/invite=[^&#"\\]+/g, 'invite=<скрыто>'))
}

function sentry(): Promise<Sentry | null> {
  if (!DSN || typeof window === 'undefined') return Promise.resolve(null)
  loading ??= import('@sentry/browser')
    .then((S) => {
      S.init({
        dsn: DSN,
        environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT || 'production',
        release: process.env.NEXT_PUBLIC_APP_VERSION || undefined,
        sendDefaultPii: false,
        tracesSampleRate: 0,
        beforeSend: (event) => scrub(event),
        beforeBreadcrumb: (crumb) => scrub(crumb),
      })
      return S
    })
    .catch(() => null)
  return loading
}

/** Подключить отлов ошибок (вызывается один раз при загрузке страницы). */
export function startSentry(): void {
  void sentry()
}

/** Отправить ошибку, пойманную границей ошибок React (app/error.tsx, app/global-error.tsx). */
export function reportError(error: unknown): void {
  void sentry().then((S) => S?.captureException(error))
}

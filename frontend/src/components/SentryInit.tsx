'use client'

import { useEffect } from 'react'

import { startSentry } from '@/lib/sentry'

/** В корневом макете: отлов необработанных ошибок браузера, если задан NEXT_PUBLIC_SENTRY_DSN. */
export default function SentryInit() {
  useEffect(() => startSentry(), [])
  return null
}

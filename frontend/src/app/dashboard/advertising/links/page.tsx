'use client'
// «Продвижение → Ссылки с UTM» (шаги 11.1–11.2): конструктор и созданные ссылки (?tab=history, ?link= — открыть ссылку
// из уведомления) — components/traffic.tsx, components/traffic-history.tsx.
import { Suspense } from 'react'

import { LinkBuilderPage } from '@/components/traffic'

export default function Page() {
  return (
    <Suspense>
      <LinkBuilderPage />
    </Suspense>
  )
}

'use client'
// «Аналитика → Отчёты Ozon и загрузка» (шаги 2.2, 10.12): история отчётов, получение через API, документы Ozon, полнота
// данных и какие отчёты бывают. Та же страница — «Финансы → Отчёты и документы» (только финансовые).
import Link from 'next/link'

import { AnalyticsTabs } from '@/components/analytics'
import { ReportsPage } from '@/components/reports-page'

export default function AnalyticsReportsPage() {
  return (
    <ReportsPage
      scope="analytics"
      header={
        <>
          <AnalyticsTabs />
          <div className="flex flex-wrap items-center gap-4">
            <h1 className="text-2xl font-semibold">Отчёты Ozon</h1>
            <Link href="/dashboard/analytics/reports/upload" className="ml-auto rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90">
              Загрузить отчёт
            </Link>
          </div>
        </>
      }
    />
  )
}

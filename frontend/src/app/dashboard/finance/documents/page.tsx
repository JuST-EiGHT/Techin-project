'use client'
// «Финансы → Отчёты и документы» (шаги 10.1, 10.12): та же страница, что «Аналитика → Отчёты Ozon и загрузка», — только
// финансовые документы: начисления, реализация, взаиморасчёты, компенсации, документы по услугам.
import Link from 'next/link'

import { ReportsPage } from '@/components/reports-page'

export default function FinanceDocumentsPage() {
  return (
    <ReportsPage
      scope="finance"
      header={
        <div className="flex flex-wrap items-center gap-4">
          <div>
            <h1 className="text-2xl font-semibold">Отчёты и документы</h1>
            <p className="mt-1 text-sm text-muted">
              Финансовые документы Ozon: начисления, реализация, взаиморасчёты, компенсации — через API или файлом.
            </p>
          </div>
          <Link href="/dashboard/analytics/reports/upload" className="ml-auto rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90">
            Загрузить отчёт
          </Link>
        </div>
      }
    />
  )
}

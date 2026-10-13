'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'

import { useAuth } from '@/contexts/AuthContext'
import { api } from '@/lib/api'

export default function DashboardPage() {
  const { user } = useAuth()
  // налог не задан — не 0 и не чужие 20 %: просим указать систему налогообложения (шаг 2.5)
  const [taxSet, setTaxSet] = useState<boolean | null>(null)
  useEffect(() => {
    api
      .get<{ current: unknown }>('/tax-profile')
      .then(({ data }) => setTaxSet(data.current !== null))
      .catch(() => setTaxSet(null))
  }, [])

  return (
    <div className="space-y-4">
      <div className="bg-card border border-line rounded-xl p-6">
        <h1 className="text-2xl font-semibold">Добро пожаловать, {user?.full_name || user?.email}</h1>
      </div>
      {taxSet === false && (
        <div
          data-testid="tax-banner"
          className="flex flex-wrap items-center gap-3 rounded-lg border border-warn/40 bg-warn/10 px-4 py-3 text-sm text-warn"
        >
          Налог не задан: укажите, кто вы и какая у вас система налогообложения, — без этого сервис не посчитает налог в P&amp;L.
          <Link href="/dashboard/settings/taxes" className="ml-auto underline">
            Указать →
          </Link>
        </div>
      )}
    </div>
  )
}

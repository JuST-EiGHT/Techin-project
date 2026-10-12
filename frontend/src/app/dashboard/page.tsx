'use client'

import { useAuth } from '@/contexts/AuthContext'

export default function DashboardPage() {
  const { user } = useAuth()
  return (
    <div className="bg-card border border-line rounded-xl p-6">
      <h1 className="text-2xl font-semibold">Добро пожаловать, {user?.full_name || user?.email}</h1>
    </div>
  )
}

// Основные настройки аккаунта для страниц: загружаются один раз на пользователя, обновляются при сохранении.
import { useEffect, useState } from 'react'

import { useAuth } from '@/contexts/AuthContext'
import { api } from '@/lib/api'
import type { ThemePreference } from '@/lib/theme'

/** «Откуда товар»: при закупке, после продажи, с отсрочкой, своё производство, по-разному (варианты — lib/supplier.ts). */
export type SupplierPayment = 'upfront' | 'after_sale' | 'deferred' | 'own_production' | 'mixed'

export type UserSettings = {
  theme: ThemePreference
  supplier_payment: SupplierPayment
  supplier_deferral_days: number | null
  cost_label: string
}

/** Как называется себестоимость с НДС в P&L (подпись приходит с бэкенда — это запасной вариант). */
export const DEFAULT_COST_LABEL = 'Себестоимость с НДС'

let cache: { userId: string; settings: Promise<UserSettings> } | null = null
const listeners = new Set<(s: UserSettings) => void>()

export function loadSettings(userId: string): Promise<UserSettings> {
  if (cache?.userId !== userId) {
    const settings = api.get<UserSettings>('/settings').then((r) => r.data)
    settings.catch(() => {
      if (cache?.settings === settings) cache = null
    })
    cache = { userId, settings }
  }
  return cache.settings
}

export async function saveSettings(userId: string, changes: Partial<Omit<UserSettings, 'cost_label'>>): Promise<UserSettings> {
  const { data } = await api.patch<UserSettings>('/settings', changes)
  cache = { userId, settings: Promise.resolve(data) }
  listeners.forEach((l) => l(data))
  return data
}

/** Настройки текущего пользователя; null — ещё загружаются (или не загрузились). */
export function useSettings(): UserSettings | null {
  const { user } = useAuth()
  const [settings, setSettings] = useState<UserSettings | null>(null)
  useEffect(() => {
    if (!user) return
    let alive = true
    loadSettings(user.id)
      .then((s) => alive && setSettings(s))
      .catch(() => undefined)
    listeners.add(setSettings)
    return () => {
      alive = false
      listeners.delete(setSettings)
    }
  }, [user])
  return settings
}

/** «Себестоимость с НДС», «Долг поставщику» или «Себестоимость производства» — по «Откуда товар». */
export function useCostLabel(): string {
  return useSettings()?.cost_label ?? DEFAULT_COST_LABEL
}
